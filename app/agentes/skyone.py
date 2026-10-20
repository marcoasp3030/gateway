"""Contrato Skyone e credenciais isolados do pipeline."""
import asyncio
import base64
import os
from .controle import CredencialRecusada, CircuitoAberto
import json
import logging
import uuid
from zoneinfo import ZoneInfo
import httpx
import seguranca
from nucleo.banco import Repositorio
from nucleo.contatos import validar_nome,nome_do_cliente
from nucleo.atendimento import PERFIL_PADRAO


def perfil_padrao(): return {k:(list(v) if isinstance(v,list) else v) for k,v in PERFIL_PADRAO.items()}
from nucleo.credenciais import CredencialInvalida


from .erros import FalhaAgente, FalhaTransitoria, TempoEsgotado, SilencioSemMotivo
import re
import time
from datetime import datetime,timezone,timedelta

log=logging.getLogger('gateway.skyone')

# Nova tentativa única (Fase 0.6): só depois deste intervalo e dentro do timeout_s.
ESPERA_NOVA_TENTATIVA_S=2


# Acima disto o cliente está voltando depois de um tempo: o agente deve retomar, não continuar (INV10).
RETOMADA_APOS=timedelta(hours=12)
MAX_BALOES=10
# MULTI1-b: as do núcleo e as que os módulos declaram (pedido_fechado, do módulo de pedidos).
from modulos import declarados as _declarados
ACOES=('nenhuma','silencio','transferir_humano','encerrar','notificar_equipe')+_declarados('acoes')


def ha_quanto_tempo(intervalo):
    """Texto para o agente: "há 2 minutos", "há 3 dias"."""
    s=max(0,int(intervalo.total_seconds()))
    if s<60: return 'há menos de 1 minuto'
    for limite,divisor,um,varios in ((3600,60,'minuto','minutos'),(86400,3600,'hora','horas'),(None,86400,'dia','dias')):
        if limite is None or s<limite:
            n=s//divisor
            return f'há {n} {um if n==1 else varios}'


def baloes(resposta):
    """Resposta do agente em balões: lista (um item = um balão) ou texto único
    (parágrafos separados por linha em branco). Devolve None se o formato for inválido."""
    if isinstance(resposta,dict): resposta=resposta.get('body')
    if resposta is None: return []
    if isinstance(resposta,str): partes=re.split(r'\n[ \t]*\n',resposta)
    elif isinstance(resposta,list) and all(isinstance(p,str) for p in resposta): partes=resposta
    else: return None
    partes=[p.strip() for p in partes if p.strip()]
    if len(partes)>MAX_BALOES:
        # Nunca descarta texto: o excedente segue junto no último balão.
        partes=partes[:MAX_BALOES-1]+['\n\n'.join(partes[MAX_BALOES-1:])]
    return partes


async def interacao(db,tenant_id,conversa_id,canal_id,mensagem):
    """Clique em botão ou escolha em lista de uma mensagem do lote: {"tipo","id","titulo","atual"} ou None (texto puro).

    Só vale se a mensagem dos botões foi enviada pelo gateway nesta conversa e, havendo opções
    gravadas nela, o id é uma delas. `atual` = o bot não enviou mais nada depois daquela mensagem
    (clique em mensagem antiga vem com atual=false; o gateway nunca executa nada pelo clique)."""
    meta=mensagem.get('meta');meta=json.loads(meta) if isinstance(meta,str) else meta
    clique=(meta or {}).get('interacao') if isinstance(meta,dict) else None
    if not isinstance(clique,dict) or clique.get('tipo') not in ('botao','lista') or not clique.get('id') or not clique.get('mensagem') or not canal_id: return None
    origem=await db.fetchrow("""SELECT id,meta FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND message_id=$3
        AND direcao='saida' AND origem='bot'""",tenant_id,conversa_id,f'{canal_id}:{clique["mensagem"]}')
    if not origem: return None
    gravado=origem['meta'];gravado=json.loads(gravado) if isinstance(gravado,str) else gravado
    opcoes=((gravado or {}).get('botoes') or {}).get('opcoes') if isinstance(gravado,dict) else None
    if isinstance(opcoes,list) and clique['id'] not in {o.get('id') for o in opcoes if isinstance(o,dict)}: return None
    depois=await db.fetchval("""SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND id>$3
        AND direcao='saida' AND coalesce(tipo,'')<>'reacao')""",tenant_id,conversa_id,origem['id'])
    return {'tipo':clique['tipo'],'id':clique['id'],'titulo':mensagem['conteudo'],'atual':not depois}


async def pacote(repo,tenant_id,conversa_id,antes_id,message,cfg,request_id=None,lote=None,acoes=None,aviso=None):
    """Pacote do Apêndice A. `lote` são as mensagens pendentes do turno (enviada_em, tipo, conteudo)."""
    conv = await repo.obter(tenant_id,'conversas',conversa_id)
    if not conv or conv['agente_id']!=cfg['id'] or cfg['tenant_id']!=tenant_id:
        raise LookupError('Conversa não encontrada')
    tenant = await repo.tenant(tenant_id)
    if not tenant: raise LookupError('Tenant não encontrado')
    fuso = ZoneInfo(tenant['timezone'])
    iso = lambda quando: quando.astimezone(fuso).isoformat(timespec='seconds')
    rid = request_id or str(uuid.uuid4())
    agora = datetime.now(timezone.utc)
    # ENC1-a: atendimento atual; o histórico é só dele (E4) e o anterior vai só como resumo.
    from nucleo import atendimentos
    atendimento = await atendimentos.do_pacote(repo.db,tenant_id,conversa_id,fuso,agora)
    historico = await repo.historico(tenant_id,conversa_id,antes_id,cfg['history_turns'],atendimento['id'] if atendimento else None)
    do_lote = []; ultima_interacao = None; origens = []
    for i in (dict(x) for x in (lote or [])):
        item = {'enviada_em':iso(i.get('enviada_em') or i.get('criado_em') or agora),'tipo':i.get('tipo') or 'texto','texto':i['conteudo']}
        clique = await interacao(repo.db,tenant_id,conversa_id,conv.get('canal_id'),i)
        origem_do_clique = None
        if clique:
            item['interacao'] = {k:clique[k] for k in ('tipo','id','atual')}
            ultima_interacao = clique
            meta = i.get('meta'); meta = json.loads(meta) if isinstance(meta,str) else (meta or {})
            origem_do_clique = (meta.get('interacao') or {}).get('mensagem')
        do_lote.append(item); origens.append(origem_do_clique)
    # Toques contraditórios (SOL1): dois ou mais cliques na MESMA mensagem de botões ou lista → só o último vai ao agente,
    # levando em "substituiu" os ids dos anteriores. Cliques em mensagens diferentes e texto digitado ficam como estão.
    descartados = set()
    for origem_msg in {o for o in origens if o}:
        posicoes = [n for n,o in enumerate(origens) if o==origem_msg]
        if len(posicoes)>1:
            do_lote[posicoes[-1]]['interacao']['substituiu'] = [do_lote[n]['interacao']['id'] for n in posicoes[:-1]]
            descartados.update(posicoes[:-1])
    if descartados:
        do_lote = [item for n,item in enumerate(do_lote) if n not in descartados]
        message = '\n'.join(item['texto'] for item in do_lote)
    do_lote = do_lote or [{'enviada_em':iso(agora),'tipo':'texto','texto':message}]
    # Tempo explícito (INV10): da última mensagem da conversa antes deste lote até a primeira do lote.
    anterior = await repo.db.fetchval('SELECT max(enviada_em) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND id<$3',
                                      tenant_id,conversa_id,antes_id)
    primeira = min((i.get('enviada_em') or i.get('criado_em') or agora for i in (dict(x) for x in (lote or []))),default=agora)
    intervalo = max(primeira-anterior,timedelta(0)) if anterior else None
    contato = await repo.obter(tenant_id,'contatos',conv['contato_id']) if conv.get('contato_id') else None
    canal = await repo.obter(tenant_id,'tenant_canais',conv['canal_id']) if conv.get('canal_id') else None
    if contato: nome_cliente,nome_origem = nome_do_cliente(contato)
    else: nome_cliente,nome_origem = (conv.get('nome'),'perfil') if conv.get('nome') else (None,None)
    from modulos import contextos,ganchos
    from nucleo.horarios import pacote as unidades
    contexto = await contextos(repo,tenant_id,conversa_id,cfg)
    if cfg.get('notificar_equipe')=='on':
        # SOL1: o que a equipe já foi avisada nesta conversa (para o agente complementar em vez de repetir). Só com o recurso ligado.
        from nucleo.solicitacoes import abertas
        contexto['solicitacoes'] = await abertas(repo.db,tenant_id,conversa_id)
    # CFG1-c: situação da empresa e da equipe agora, e o que está ligado neste agente. Sempre presentes.
    from nucleo import expediente
    # HOR2: a próxima abertura que vai ao agente é a de uma unidade que atende (no catálogo: tem_cardapio). Sem módulo que diga
    # quais atendem, ou empresa sem nenhuma, vale a de qualquer unidade.
    cardapio = set()
    for _,f in ganchos('unidades_que_atendem',cfg.get('modulos') or ()): cardapio |= await f(repo.db,tenant_id)
    empresa_agora = await expediente.da_empresa(repo.db,tenant_id,tenant['timezone'],agora,cardapio or None)
    abertura = {**empresa_agora,**empresa_agora['do_cardapio']} if cardapio and empresa_agora['fora'] and empresa_agora['do_cardapio'] else \
               ({**empresa_agora,'proxima':None} if cardapio and empresa_agora['fora'] else empresa_agora)
    equipe_agora = await expediente.da_equipe(repo.db,tenant_id,cfg,tenant['timezone'],agora,empresa_agora)
    com_menu = cfg.get('botoes_interativos')=='on' and (canal or {}).get('tipo')=='uazapi'
    com_reacao = cfg.get('reacoes')=='on' and (canal or {}).get('tipo')=='uazapi'
    from nucleo import calendario
    return {
        # CAL1: hoje + 7 dias com dia da semana, feriado e o que muda nas unidades. O agente não calcula datas.
        'calendario':await calendario.do_pacote(repo.db,tenant_id,tenant['timezone'],agora),
        'unidades':await unidades(repo.db,tenant_id,tenant['timezone'],agora,cfg.get('modulos') or ()),
        'contexto_modulos':contexto,
        'fora_do_horario':empresa_agora['fora'],'proxima_abertura':expediente.proxima_abertura(abertura),
        'equipe_disponivel':equipe_agora['disponivel'],'retorno_equipe':expediente.retorno_equipe(equipe_agora),
        'recursos':{'botoes':com_menu,'lista':com_menu,'reacoes':com_reacao,'emojis_de_reacao':list(cfg.get('reacoes_emojis') or []) if com_reacao else [],
                    'notificar_equipe':cfg.get('notificar_equipe')=='on'},
        'conversation_id':conversa_id,'request_id':rid,'session_id':rid,'message':message,
        'instrucoes_agente':cfg['instrucoes_agente'],
        # CFG1-a: preferências de estilo da empresa (versão publicada), sempre com todos os campos. São dados, não instruções de sistema.
        'perfil_versao':cfg.get('perfil_versao') or 'e0','perfil_atendimento':cfg.get('perfil_atendimento') or perfil_padrao(),
        'historico':[{'role':'user' if h['direcao']=='entrada' else 'assistant',
                     'content':h['conteudo'],'enviada_em':iso(h['enviada_em'])}
                    for h in historico],
        'tenant_id':tenant_id,'agente_id':cfg['id'],'modelo_id':cfg['modelo_id'],
        # MULTI1-a: modelo, segmento e módulos do agente, com as listas efetivas, para o Normalizar montar as instruções certas.
        'agente':{'modelo':{'id':cfg['modelo_id'],'versao':cfg['modelo_versao'],'nome':cfg.get('modelo_nome')},'segmento':cfg.get('segmento'),
                  'modulos':list(cfg.get('modulos') or []),
                  'motivos_transferencia':[{'id':m['id'],'rotulo':m['rotulo']} for m in cfg.get('motivos_transferencia_permitidos') or []],
                  'tipos_solicitacao':[{'id':t['id'],'rotulo':t['rotulo']} for t in cfg.get('tipos_solicitacao_permitidos') or []]},
        'canal':{'id':conv.get('canal_id'),'tipo':(canal or {}).get('tipo') or conv.get('canal'),'publico':(canal or {}).get('publico')},
        'modo_resposta':cfg['modo_resposta'],'tarefa':'responder',
        'agora':iso(agora),
        # Nulo no primeiro contato: não há mensagem anterior.
        'tempo_desde_ultima':ha_quanto_tempo(intervalo) if intervalo is not None else None,
        'retomada':bool(intervalo is not None and intervalo>RETOMADA_APOS),
        'mensagens_do_lote':do_lote,
        # Último clique em botão do lote (nulo se não houve). O texto do clique é o título do botão.
        'ultima_interacao':ultima_interacao,
        # Só em turno refeito (UX2): o que a tentativa descartada já fez nas Skills. Lista vazia nos demais.
        'acoes_ja_executadas':acoes or [],
        # PED2: recado do gateway para este turno (nulo quase sempre). Hoje: turno refeito porque o agente anunciou pedido
        # fechado sem fechar_pedido de sucesso.
        'aviso_do_gateway':aviso,
        # Sempre presente (UX3.1): chamadas de Skill desta conversa nos últimos minutos, com há quantos segundos foi cada uma.
        'acoes_recentes':[a for _,f in ganchos('acoes_recentes',cfg.get('modulos') or ()) for a in await f(repo.db,tenant_id,conversa_id,cfg)],
        # Nome pela prioridade cadastro > informado > perfil (PED1-c); nunca o telefone. nome_origem diz de onde veio.
        'cliente':{'nome':nome_cliente,'nome_origem':nome_origem,'unidade_preferida':conv.get('unidade_preferida'),
                   'resumo_anterior':((atendimento or {}).get('anterior') or {}).get('resumo') or (contato or {}).get('resumo')},
        # ENC1-a: atendimento atual (novo = primeiro turno entregue dele) e o anterior, resumido. Nulo só em conversa sem atendimento.
        'atendimento':atendimento,
    }


async def _chamar(client,repo,cofre,tenant_id,cfg,payload):
    if cfg['tenant_id']!=tenant_id or payload['tenant_id']!=tenant_id or payload['agente_id']!=cfg['id']:
        raise FalhaAgente('Contexto inválido')
    if cfg['agente_mode']=='mock':
        # MOCK_ATRASO_S simula o tempo de resposta do agente em testes de carga; padrão 0.
        atraso=float(os.getenv('MOCK_ATRASO_S','0') or 0)
        if atraso>0: await asyncio.sleep(min(atraso,120))
        return {'resposta':['Resposta simulada.'],'acao':'nenhuma'}
    if cfg['provedor']!='skyone' or cfg['modo_resposta']!='sync':
        raise FalhaAgente('Provedor/modo indisponível')
    tenant = await repo.tenant(tenant_id)
    agente = await repo.obter(tenant_id,'agentes',cfg['id'])
    if not tenant or not agente: raise FalhaAgente('Agente indisponível')
    try:
        headers = {}
        protegidos = []
        tipo = agente['skyone_auth_tipo']
        if tipo=='nenhuma':
            if not tenant['tenant_teste']: raise FalhaAgente('Autenticação obrigatória')
        else:
            segredo = cofre.abrir(tenant_id,agente['skyone_auth_enc'])
            if not isinstance(segredo,str) or not segredo or '\r' in segredo or '\n' in segredo:
                raise FalhaAgente('Credencial inválida')
            protegidos.append(segredo)
            if tipo=='basic':
                usuario=cofre.abrir(tenant_id,agente['skyone_auth_usuario_enc'])
                if not isinstance(usuario,str) or not usuario or ':' in usuario or any(ord(c)<32 or ord(c)==127 for c in usuario):
                    raise FalhaAgente('Usuário Basic inválido')
                codificado=base64.b64encode((usuario+':'+segredo).encode('utf-8')).decode('ascii')
                headers['Authorization']='Basic '+codificado
                protegidos.extend([usuario,codificado])
            elif tipo=='bearer': headers['Authorization']='Bearer '+segredo
            elif tipo=='cabecalho':
                nome=agente['skyone_auth_cabecalho']
                if not nome or not re.fullmatch(r'[A-Za-z0-9-]+',nome) or nome.lower() in ('host','content-length','transfer-encoding','connection'):
                    raise FalhaAgente('Cabeçalho inválido')
                headers[nome]=segredo
            else: raise FalhaAgente('Autenticação desconhecida')
        async def requisitar():
            await seguranca.validar_url_saida(cfg['skyone_webhook_url'],cfg['skyone_dominios_permitidos'])
            async with client.stream('POST',cfg['skyone_webhook_url'],json=payload,headers=headers,
                                     timeout=cfg['timeout_s'],follow_redirects=False) as response:
                if response.status_code in (401,403): raise CredencialRecusada()
                if response.status_code in (502,503,504): raise FalhaTransitoria()
                if not 200<=response.status_code<300: raise FalhaAgente('HTTP do agente recusado')
                partes=[]; tamanho=0
                async for parte in response.aiter_bytes():
                    tamanho+=len(parte)
                    if tamanho>2097152: raise FalhaAgente('Resposta excede limite')
                    partes.append(parte)
                return json.loads(b''.join(partes))
        corpo=await asyncio.wait_for(requisitar(),timeout=cfg['timeout_s'])
        if not isinstance(corpo,dict) or corpo.get('request_id')!=payload['request_id'] or corpo.get('conversation_id')!=payload['conversation_id'] or corpo.get('status')!='success' or corpo.get('tipo')!='text':
            raise FalhaAgente('Contrato inválido')
        acao=corpo.get('acao')
        if acao not in ACOES:
            raise FalhaAgente('Ação inválida')
        textos=baloes(corpo.get('resposta'))
        if textos is None: raise FalhaAgente('Resposta vazia/inválida')
        motivo=corpo.get('motivo').strip()[:500] if isinstance(corpo.get('motivo'),str) else ''
        if acao=='silencio':
            # INV04: silêncio só vale com motivo declarado.
            if not motivo: raise SilencioSemMotivo()
            textos=[]
        elif not textos and acao in ('nenhuma','pedido_fechado'):
            # Resposta vazia sem acao "silencio" é falha (INV04). Transferir e encerrar podem vir sem texto.
            raise SilencioSemMotivo()
        # Guardar somente campos do contrato, sem eco arbitrário do provedor.
        result={'resposta':textos,'acao':acao}
        if motivo: result['motivo']=motivo
        desfecho=corpo.get('desfecho')
        if isinstance(desfecho,str) and re.fullmatch(r'[a-z0-9_]{1,40}',desfecho.strip()): result['desfecho']=desfecho.strip()
        from nucleo import botoes as mod_botoes
        escolhas=mod_botoes.do_retorno(corpo.get('botoes'),acao,textos)
        if escolhas: result['botoes']=escolhas
        # Nome que o cliente disse na conversa (PED1-c). Opcional; inválido é ignorado sem derrubar o turno.
        if corpo.get('nome_cliente') is not None:
            nome,recusa=validar_nome(corpo.get('nome_cliente'))
            if nome: result['nome_cliente']=nome
            else: log.warning('nome_cliente ignorado: %s',recusa)
        # Dados coletados para a equipe (SOL1). Opcional; só o que é do contrato; campo inválido é ignorado.
        if corpo.get('solicitacao') is not None and acao in ('notificar_equipe','transferir_humano'):
            from nucleo.solicitacoes import validar as validar_solicitacao
            solicitacao=validar_solicitacao(corpo.get('solicitacao'))
            if solicitacao: result['solicitacao']=solicitacao
        # Reação pedida pelo agente (R1). Opcional; valor que não seja um emoji é ignorado.
        from nucleo.reacoes import do_retorno as reacao_do_retorno
        reacao=reacao_do_retorno(corpo.get('reacao'))
        if reacao: result['reacao']=reacao
        # ENC1-a: versão do prompt que respondeu ("v6.7"). Fora do formato (v + números e pontos, até 40) vira nulo sem recusar.
        versao=corpo.get('prompt_versao')
        if isinstance(versao,str) and len(versao)<=40 and re.fullmatch(r'v[0-9]+(\.[0-9]+)*',versao): result['prompt_versao']=versao
        # "resumo" no encerrar fica previsto no contrato, mas ignorado por enquanto (decisão 3 de 20/10): vale o resumo factual do gateway.
        uso=corpo.get('uso')
        if isinstance(uso,dict) and all(isinstance(k,str) and type(v) is int and v>=0 for k,v in uso.items()): result['uso']=uso
        # Não permitir eco da credencial mesmo dentro de campos do contrato.
        if any(s in json.dumps(result,ensure_ascii=False) for s in protegidos):
            raise FalhaAgente('Resposta contém credencial')
        return result
    except (httpx.TimeoutException,TimeoutError):
        raise TempoEsgotado() from None
    except httpx.ConnectError:
        # Não chegou a conectar: nada foi processado do outro lado.
        raise FalhaTransitoria() from None
    except (httpx.HTTPError,TimeoutError,ValueError,TypeError,CredencialInvalida):
        raise FalhaAgente('Falha na comunicação com o agente') from None


async def chamar(client,repo,cofre,tenant_id,cfg,payload,controle=None):
    if cfg['tenant_id']!=tenant_id or payload['tenant_id']!=tenant_id or payload['agente_id']!=cfg['id']:
        raise FalhaAgente('Contexto inválido')
    if cfg['agente_mode']=='mock' or controle is None:
        return await _chamar(client,repo,cofre,tenant_id,cfg,payload)
    geracao=None
    async def executar():
        nonlocal geracao
        inicio=time.monotonic()
        await controle.esperar(cfg['skyone_webhook_url'])
        geracao=await controle.iniciar(tenant_id,cfg['id'],cfg['timeout_s'])
        try:
            return await _chamar(client,repo,cofre,tenant_id,cfg,payload)
        except FalhaTransitoria:
            # Uma única nova tentativa, rápida, e só se couber no timeout_s. Nunca em tempo esgotado.
            if time.monotonic()-inicio+ESPERA_NOVA_TENTATIVA_S>=cfg['timeout_s']: raise
            await asyncio.sleep(ESPERA_NOVA_TENTATIVA_S)
            await controle.esperar(cfg['skyone_webhook_url'])
            return await _chamar(client,repo,cofre,tenant_id,cfg,payload)
    try:
        result=await asyncio.wait_for(executar(),timeout=cfg['timeout_s'])
    except Exception as erro:
        if geracao is not None:
            await controle.terminar(tenant_id,cfg['id'],geracao,erro)
        if isinstance(erro,(CredencialRecusada,CircuitoAberto,FalhaAgente)): raise
        if isinstance(erro,TimeoutError): raise TempoEsgotado() from None
        raise FalhaAgente('Controle do Skyone indisponível') from None
    await controle.terminar(tenant_id,cfg['id'],geracao)
    return result
