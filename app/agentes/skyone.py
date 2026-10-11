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
ACOES=('nenhuma','silencio','transferir_humano','encerrar','pedido_fechado')


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
        AND direcao='saida')""",tenant_id,conversa_id,origem['id'])
    return {'tipo':clique['tipo'],'id':clique['id'],'titulo':mensagem['conteudo'],'atual':not depois}


async def pacote(repo,tenant_id,conversa_id,antes_id,message,cfg,request_id=None,lote=None,acoes=None):
    """Pacote do Apêndice A. `lote` são as mensagens pendentes do turno (enviada_em, tipo, conteudo)."""
    conv = await repo.obter(tenant_id,'conversas',conversa_id)
    if not conv or conv['agente_id']!=cfg['id'] or cfg['tenant_id']!=tenant_id:
        raise LookupError('Conversa não encontrada')
    tenant = await repo.tenant(tenant_id)
    if not tenant: raise LookupError('Tenant não encontrado')
    fuso = ZoneInfo(tenant['timezone'])
    iso = lambda quando: quando.astimezone(fuso).isoformat(timespec='seconds')
    historico = await repo.historico(tenant_id,conversa_id,antes_id,cfg['history_turns'])
    rid = request_id or str(uuid.uuid4())
    agora = datetime.now(timezone.utc)
    do_lote = []; ultima_interacao = None
    for i in (dict(x) for x in (lote or [])):
        item = {'enviada_em':iso(i.get('enviada_em') or i.get('criado_em') or agora),'tipo':i.get('tipo') or 'texto','texto':i['conteudo']}
        clique = await interacao(repo.db,tenant_id,conversa_id,conv.get('canal_id'),i)
        if clique:
            item['interacao'] = {k:clique[k] for k in ('tipo','id','atual')}
            ultima_interacao = clique
        do_lote.append(item)
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
    from modulos import contextos
    from modulos.pedidos.servico import acoes_recentes
    from nucleo.horarios import pacote as unidades
    return {
        'unidades':await unidades(repo.db,tenant_id,tenant['timezone']),
        'contexto_modulos':await contextos(repo,tenant_id,conversa_id,cfg),
        'conversation_id':conversa_id,'request_id':rid,'session_id':rid,'message':message,
        'instrucoes_agente':cfg['instrucoes_agente'],
        'historico':[{'role':'user' if h['direcao']=='entrada' else 'assistant',
                     'content':h['conteudo'],'enviada_em':iso(h['enviada_em'])}
                    for h in historico],
        'tenant_id':tenant_id,'agente_id':cfg['id'],'modelo_id':cfg['modelo_id'],
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
        # Sempre presente (UX3.1): chamadas de Skill desta conversa nos últimos minutos, com há quantos segundos foi cada uma.
        'acoes_recentes':await acoes_recentes(repo.db,tenant_id,conversa_id,cfg.get('acoes_recentes_min')),
        # Nome pela prioridade cadastro > informado > perfil (PED1-c); nunca o telefone. nome_origem diz de onde veio.
        'cliente':{'nome':nome_cliente,'nome_origem':nome_origem,'unidade_preferida':conv.get('unidade_preferida'),
                   'resumo_anterior':(contato or {}).get('resumo')},
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
