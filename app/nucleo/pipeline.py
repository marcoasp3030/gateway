"""Recepção com tenant/canal e debounce; um agendador até a Fase 0.6."""
import asyncio
from functools import partial
from agentes.controle import Controle, CredencialRecusada, CircuitoAberto
from agentes.erros import TempoEsgotado, SilencioSemMotivo
import hashlib
import json
import logging
import os
import random
import time
import uuid
from datetime import datetime,timezone,timedelta
from fastapi import HTTPException
from agentes import skyone
from canais import uazapi
from nucleo import botoes
from nucleo import atendimentos
from nucleo.contatos import nome_de_perfil,gravar_informado
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from nucleo.solicitacoes import converter as converter_transferencia,registrar as registrar_solicitacao
from admin.servico import auditar

log=logging.getLogger('gateway.pipeline')


def motivo_fallback(falha):
    """Motivo de "aguardando atendente" para uma falha do agente."""
    if isinstance(falha,CircuitoAberto): return 'circuito_aberto'
    if isinstance(falha,TempoEsgotado): return 'tempo_esgotado'
    return 'erro_agente'


def campos_do_retorno(resultado):
    """(acao, motivo_silencio, desfecho, prompt_versao) do Retorno do agente para gravar no turno."""
    if not resultado: return None,None,None,None
    acao=resultado.get('acao')
    return acao,(resultado.get('motivo') if acao=='silencio' else None),resultado.get('desfecho'),resultado.get('prompt_versao')


ESPERA_ANEXOS_S=20


def anexos_do_lote(itens):
    """ids dos documentos anexados às mensagens do lote (ARQ1-a1)."""
    ids=[]
    for i in itens:
        meta=i.get('meta') if hasattr(i,'get') else i['meta']
        meta=json.loads(meta) if isinstance(meta,str) else (meta or {})
        if isinstance(meta,dict) and meta.get('anexo_id'): ids.append(meta['anexo_id'])
    return ids


async def _aguardar_anexos(db,itens):
    ids=anexos_do_lote([dict(i) for i in itens])
    if not ids: return
    from nucleo import documentos
    try: await documentos.aguardar(db,ids,ESPERA_ANEXOS_S)
    except Exception: log.warning('Espera dos anexos do lote falhou; segue o turno')


async def _resposta_no_webhook(conn,tenant,conversa,conv,canal,cfg,texto,plano,rid,parte):
    """Canal api (ARQ1-a1, modo agente): cada balão do bot vai ao webhook de saída `conversa.resposta` da empresa (assinado),
    para o sistema que enviou (formulário, n8n) entregar ao cliente."""
    from nucleo.webhooks import emitir
    await emitir(conn,tenant,'conversa.resposta',{'conversation_id':conversa,'agente_id':cfg['id'],'canal_id':canal['id'],'contato':conv['telefone'],
                 'request_id':str(rid),'parte':parte,'texto':botoes.numerado(texto,plano[2]),
                 'opcoes':[{'id':o.get('id'),'titulo':o.get('titulo')} for o in plano[2] or [] if isinstance(o,dict)]})


# Mensagem do atendente pelo celular que devolve a conversa ao bot na hora (não vai para o agente).
COMANDO_BOT='#bot'
# SEG07 por contato: tempo sem chamar o agente depois de estourar o limite por minuto.
BLOQUEIO_CONTATO_MIN=5


async def encerrar_conversa(conn,tenant,conversa,desfecho,motivo='concluido'):
    """Encerra o atendimento aberto e marca a conversa como encerrada (primeira marcação vale). Mensagem nova do cliente abre outro."""
    marcada=bool(await conn.fetchval("""UPDATE conversas SET encerrada_em=now(),desfecho=$3,atualizado_em=now()
        WHERE tenant_id=$1 AND conversation_id=$2 AND encerrada_em IS NULL RETURNING true""",tenant,conversa,desfecho))
    return await atendimentos.encerrar(conn,tenant,conversa,motivo,desfecho) is not None or marcada


# Endereço do painel citado no alerta de transbordo (vazio = não cita).
PAINEL_URL=os.getenv('PAINEL_URL','https://painel.luxbrain.com.br').strip() or None
FIM_DE_FRASE=('?','.','!','…')
# Segundos de "digitando" pedidos ao canal por vez; o canal cancela sozinho ao enviar.
DIGITANDO_S=25
# AVS1: o "digitando" é renovado a cada RENOVAR_DIGITANDO_S enquanto o agente processa (antes de os 25 s acabarem).
RENOVAR_DIGITANDO_S=20
# AVS1, aviso em texto: só se o bot falou nesta conversa há no máximo ANDAMENTO_MIN (conversa em andamento, não retomada),
# e no máximo um aviso a cada ESPACO_ENTRE_AVISOS_MIN por conversa.
ANDAMENTO_MIN=10
ESPACO_ENTRE_AVISOS_MIN=5
# Refazer o turno (UX2/UX3.1): no máximo dois por turno; o segundo só enquanto o turno original começou há até 30 s.
MAX_REFAZER=2
TETO_REFAZER_S=30
# Ações do agente com efeito para fora: depois delas o turno não é refeito. Os módulos somam as deles (acoes_com_efeito).
ACOES_COM_EFEITO=('transferir_humano','encerrar')


# Palavras de quem só está encerrando ou agradecendo. Saudações ("oi", "bom dia", "tudo bem") e
# respostas a perguntas ("sim", "pode") ficam de fora: esperam resposta.
FECHAMENTOS=frozenset("""obrigado obrigada obrigadao obrigadão obg obgd brigado brigada brigadao brigadão valeu vlw vlww
ok okay okk oks blz beleza belezinha certo certinho ta tá tah bom otimo ótimo perfeito show top legal massa joia jóia
combinado fechado fechou entendi entendido tendi isso so só era nada de por enquanto muito muita mto mt demais
tchau ate até mais logo abraco abraço abracos abraços tmj flw falou agradeco agradeço grato grata e é eh""".split())
MAX_PALAVRAS_FECHAMENTO=6


def parece_fechamento(itens):
    """O lote é só um fechamento ("obrigado", "ok, valeu", "👍", "blz")? Texto curto, sem pergunta,
    só com palavras de encerramento ou emoji. Usado para não mandar aviso de demora antes de um
    provável silêncio; na dúvida devolve False (o aviso sai normalmente)."""
    palavras=[]
    for i in itens:
        if (i.get('tipo') or 'texto')!='texto': return False
        texto=str(i.get('conteudo') or '')
        if '?' in texto: return False
        # Pontuação e emoji viram espaço; sobra só o que tem letra ou número.
        palavras+=''.join(c if c.isalnum() else ' ' for c in texto.lower()).split()
    if len(palavras)>MAX_PALAVRAS_FECHAMENTO: return False
    # Sem nenhuma palavra = só emoji/pontuação ("👍", "🙏🙏").
    return all(p in FECHAMENTOS for p in palavras)


# Só cumprimento, sem pedido nenhum: nunca recebe aviso de espera (AVS1).
SAUDACOES=frozenset("""oi oie oii oiii ola olá opa eae eai ei hey hi hello alo alô salve bom dia boa tarde noite
tudo td bem bom blz beleza como vai vc voce você e ai aí tem alguem alguém""".split())
MAX_PALAVRAS_SAUDACAO=6


def parece_saudacao(itens):
    """O lote é só um cumprimento ("oi", "olá, boa tarde", "opa, tudo bem?")? Texto curto, só com palavras de
    saudação ou emoji. Na dúvida devolve False."""
    palavras=[]
    for i in itens:
        if (i.get('tipo') or 'texto')!='texto': return False
        meta=i.get('meta');meta=json.loads(meta) if isinstance(meta,str) else meta
        # Toque em botão é resposta a uma pergunta, não cumprimento.
        if isinstance(meta,dict) and meta.get('interacao'): return False
        palavras+=''.join(c if c.isalnum() else ' ' for c in str(i.get('conteudo') or '').lower()).split()
    return len(palavras)<=MAX_PALAVRAS_SAUDACAO and all(p in SAUDACOES for p in palavras)


# Palavra de ligação no fim = a frase continua na próxima mensagem ("quero um temaki e").
LIGACOES=frozenset('e mas que tipo com sem de do da para pra ou também tambem tb aí ai'.split())
CONTINUA=(',',':',';','-','–','—','…','...')


def forma_da_mensagem(cfg,m):
    """'incompleta' | 'completa' | 'normal' — como a última mensagem do lote termina (UX2, P1)."""
    if (m.get('tipo') or 'texto')!='texto': return 'normal'
    # Clique em botão é resposta decidida.
    if isinstance(m.get('meta'),dict) and m['meta'].get('interacao'): return 'completa'
    texto=str(m.get('texto') or '').strip()
    # Emoji e fechamentos depois da pontuação não contam ("tudo bem? 🙂").
    sem_enfeite=texto.rstrip(' \t\n)"\'*_~'+''.join(c for c in texto[-6:] if ord(c)>0x2000 and c not in FIM_DE_FRASE))
    if sem_enfeite.endswith(('…','...')): return 'incompleta'
    if sem_enfeite.endswith(FIM_DE_FRASE): return 'completa'
    palavras=sem_enfeite.split()
    ultima=palavras[-1].lower().strip('"\'()*_~') if palavras else ''
    if sem_enfeite.endswith(CONTINUA) or ultima in LIGACOES: return 'incompleta'
    return 'completa' if len(texto.split())>cfg['debounce_palavras'] else 'normal'


def debounce_da_mensagem(cfg,m):
    """Espera antes de chamar o agente, decidida pela última mensagem: a que parece completa espera
    menos (debounce_curto_s); a que parece incompleta espera mais (debounce_incompleta_s, se
    configurado); curta, mídia, localização e reação esperam o debounce normal. O teto max_wait_s
    é aplicado por quem enfileira."""
    normal=cfg['debounce_s'];forma=forma_da_mensagem(cfg,m)
    if forma=='completa': return min(normal,cfg['debounce_curto_s'])
    if forma=='incompleta' and cfg.get('debounce_incompleta_s'): return max(normal,float(cfg['debounce_incompleta_s']))
    return normal


def _meta(marca_meta,plano):
    """meta da mensagem enviada: marca do envio (ex.: sem id do provedor) + registro dos botões."""
    meta=json.loads(marca_meta) if marca_meta else {}
    if plano: meta['botoes']=botoes.registro(*plano)
    return json.dumps(meta,ensure_ascii=False) if meta else None


async def marcar_aguardando(conn,tenant,conversa,motivo,detalhe=None,cfg=None,equipe_ausente=False):
    """Marca a conversa como aguardando atendente; evento e alerta de transbordo só na primeira marcação."""
    novo=await conn.fetchval("""UPDATE conversas SET aguardando_atendente_desde=now(),aguardando_motivo=$3
        WHERE tenant_id=$1 AND conversation_id=$2 AND aguardando_atendente_desde IS NULL RETURNING true""",
        tenant,conversa,motivo)
    if novo:
        await auditar(conn,tenant,'sistema','conversa.aguardando_atendente',conversa)
        # Alerta de transbordo (Fase 4): aviso curto ao número da empresa, se houver um cadastrado.
        from nucleo.avisos import avisar_transbordo
        await avisar_transbordo(conn,tenant,conversa,motivo,detalhe,PAINEL_URL,cfg,equipe_ausente)
    return bool(novo)


async def insistir(operacao,limite_s=60,intervalo=1):
    """Liberações no Redis (trava, vaga, confirmação) insistem enquanto ele volta de uma queda.
    Sem isso a trava/vaga ficaria presa até a validade e a empresa pararia por mais de um minuto."""
    fim=time.monotonic()+limite_s
    while True:
        try: return await operacao()
        except Exception:
            if time.monotonic()>=fim: raise
            await asyncio.sleep(intervalo)


def sanitizar(valor):
    if isinstance(valor,dict):
        return {k:('***' if k.lower() in ('s','key') or any(s in k.lower() for s in ('token','secret','password','authorization','api_key','credencial'))
                   else sanitizar(v)) for k,v in valor.items()}
    if isinstance(valor,list): return [sanitizar(v) for v in valor]
    return valor


class RespostaPronta(Exception):
    """O gateway já tem a resposta do turno sem consultar o agente (fora do horário, modo "só avisar")."""
    def __init__(self,resultado): self.resultado=resultado


class Pipeline:
    def __init__(self,db,redis,configuracoes,cofre,http,prefixo='gw05',skyone_rpm=240,fila_duravel=False):
        self.db,self.redis,self.cfg,self.cofre,self.http=db,redis,configuracoes,cofre,http
        self.repo=Repositorio(db);self.prefixo=prefixo
        self.tarefas={};self.ativos={}
        self.controle=Controle(db,redis,skyone_rpm,prefixo)
        self.chamar=partial(skyone.chamar,controle=self.controle)
        self.saida_task=None
        # Com a fila durável ligada, este agendador em processo não processa nada (exclusividade).
        self.fila_duravel=fila_duravel
        # Por quanto tempo as liberações no Redis insistem durante uma queda.
        self.insistir_s=60

    def chave(self,tenant,conversa):
        return json.dumps([tenant,conversa],separators=(',',':'))

    async def enfileirar(self,tenant,conversa,cfg,debounce=None):
        chave=self.chave(tenant,conversa)
        # Uma transação Lua mantém first e vencimento coerentes sob concorrência.
        await self.redis.eval('''
local t=redis.call('TIME')
local agora=tonumber(t[1])+tonumber(t[2])/1000000
local primeira=tonumber(redis.call('GET',KEYS[1])) or agora
redis.call('SET',KEYS[1],primeira,'EX',86400)
redis.call('ZADD',KEYS[2],math.min(agora+tonumber(ARGV[2]),primeira+tonumber(ARGV[3])),ARGV[1])
return 1
''',2,self.prefixo+':first:'+chave,self.prefixo+':due',chave,cfg['debounce_s'] if debounce is None else debounce,cfg['max_wait_s'])

    async def contexto(self,tenant,canal_id,repo=None,configuracoes=None):
        repo=repo or self.repo
        canal=await repo.obter(tenant,'tenant_canais',canal_id)
        empresa=await repo.tenant(tenant)
        if not canal or not empresa: raise HTTPException(404,'NOT_FOUND')
        if canal['triagem'] is not None: raise HTTPException(501,'Triagem disponível na Fase 6.5')
        cfg=await (configuracoes or self.cfg).efetiva(tenant,canal['agente_id'])
        return canal,empresa,cfg

    async def receber(self,tenant,canal_id,m):
        canal,empresa,cfg=await self.contexto(tenant,canal_id)
        if not canal['ativo'] or empresa['status']!='ativo' or cfg['status']!='ativo':
            return {'ok':True,'ignorado':True}
        if m.get('via_api'): return {'ok':True,'ignorado':True}
        identificador=m['identificador']
        # Número interno da empresa (ex.: WhatsApp de pedidos de uma unidade, no módulo de pedidos) nunca é cliente:
        # a mensagem não vira conversa nem turno; o módulo decide o que fazer com ela.
        from modulos import ganchos
        for _,interno in ganchos('numero_interno'):
            resposta=await interno(self.db,tenant,identificador,m)
            if resposta is not None: return resposta
        conversa=f'{tenant}:{canal_id}:{identificador}'
        humano=m.get('humano',False)
        mensagem_id=f'{canal_id}:{m["message_id"]}' if m.get('message_id') else None
        async with self.db.acquire() as conn,conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',conversa)
            # Nome de perfil (senderName) a cada mensagem do cliente, não só na primeira (PED1-c). Mensagem do atendente vem sem nome.
            perfil=None if humano else nome_de_perfil(m.get('nome'))
            contato=await conn.fetchval('''INSERT INTO contatos(id,tenant_id,telefone,nome,nome_perfil) VALUES ($1,$2,$3,$4,$4)
                ON CONFLICT(tenant_id,telefone) DO UPDATE SET atualizado_em=now(),nome_perfil=coalesce(EXCLUDED.nome_perfil,contatos.nome_perfil)
                RETURNING id''','ct_'+uuid.uuid4().hex,tenant,identificador,perfil)
            anterior=await Repositorio(conn).obter(tenant,'conversas',conversa)
            if anterior and anterior['agente_id']!=cfg['id']:
                await auditar(conn,tenant,'sistema','conversa.roteada',conversa)
            await conn.execute('''INSERT INTO conversas(tenant_id,conversation_id,canal,canal_id,agente_id,telefone,nome,simulada)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(conversation_id) DO UPDATE
                SET agente_id=EXCLUDED.agente_id,nome=coalesce(EXCLUDED.nome,conversas.nome),atualizado_em=now()
                WHERE conversas.tenant_id=EXCLUDED.tenant_id''',tenant,conversa,canal['tipo'],canal_id,cfg['id'],identificador,perfil,bool(m.get('simulacao')))
            await conn.execute('UPDATE conversas SET contato_id=$3 WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,contato)
            reacao=m.get('tipo')=='reacao'
            comando_bot=humano and not reacao and m['texto'].strip().lower()==COMANDO_BOT
            if comando_bot: m={**m,'tipo':'comando'}
            msgid=await conn.fetchval('''INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,message_id,enviada_em,tipo,meta,processada_em,origem_recebimento)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,CASE WHEN $10 THEN now() END,$11) ON CONFLICT(tenant_id,message_id) DO NOTHING RETURNING id''',
                tenant,conversa,'saida' if humano else 'entrada','humano' if humano else 'cliente',m['texto'],
                mensagem_id,m.get('enviada_em') or datetime.now(timezone.utc),m.get('tipo') or 'texto',
                json.dumps(m['meta'],ensure_ascii=False) if m.get('meta') else None,reacao,m.get('origem_recebimento'))
            if msgid:
                await auditar(conn,tenant,'sistema','mensagem.humana' if humano else 'mensagem.recebida',conversa)
            # Reação fica no histórico, mas não abre turno nem pausa o bot (um 👍 do atendente não é assumir a conversa).
            if reacao: return {'ok':True,'reacao':True,'duplicada':msgid is None,'conversation_id':conversa,'mensagem_id':msgid}
            if comando_bot:
                # Atendente devolveu a conversa ao bot pelo celular: retoma na hora e não vai para o agente.
                if msgid:
                    await conn.execute('''UPDATE conversas SET bot_pausado_ate=NULL,aguardando_atendente_desde=NULL,aguardando_motivo=NULL,
                        atualizado_em=now() WHERE tenant_id=$1 AND conversation_id=$2''',tenant,conversa)
                    await conn.execute('UPDATE mensagens SET processada_em=now() WHERE tenant_id=$1 AND id=$2',tenant,msgid)
                    await auditar(conn,tenant,'atendente','conversa.devolvida',conversa)
                retomar=bool(msgid) and await conn.fetchval("SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='entrada' AND processada_em IS NULL AND origem IN ('cliente','sistema'))",tenant,conversa)
            elif humano and msgid:
                await conn.execute('UPDATE conversas SET bot_pausado_ate=now()+make_interval(mins=>$3) '
                    'WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,cfg['pause_minutes'])
            if humano and not comando_bot: return {'ok':True,'from_me':True}
            if msgid and not humano:
                # Cliente voltou a falar: conversa encerrada reabre.
                if await conn.fetchval('''UPDATE conversas SET encerrada_em=NULL,desfecho=NULL WHERE tenant_id=$1 AND conversation_id=$2
                        AND encerrada_em IS NOT NULL RETURNING true''',tenant,conversa):
                    await auditar(conn,tenant,'sistema','conversa.reaberta',conversa)
                # ENC1-a: mensagem do cliente sem atendimento aberto abre o próximo (o anterior nunca é reaproveitado).
                await atendimentos.abrir_se_preciso(conn,tenant,conversa,cfg['id'])
                # SEG07 por contato: mensagens demais em um minuto → registra, alerta e para de chamar o agente por um tempo.
                limitado=await conn.fetchval('SELECT limitado_ate>now() FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
                if not limitado and await conn.fetchval('''SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2
                        AND direcao='entrada' AND origem='cliente' AND criado_em>now()-make_interval(secs=>60)''',tenant,conversa)>cfg['max_msgs_por_minuto_contato']:
                    await conn.execute('UPDATE conversas SET limitado_ate=now()+make_interval(mins=>$3) WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,BLOQUEIO_CONTATO_MIN)
                    await auditar(conn,tenant,'sistema','contato.limite_excedido',conversa)
                    log.warning('Contato acima do limite de mensagens por minuto; agente suspenso por %s min',BLOQUEIO_CONTATO_MIN)
                    limitado=True
                if limitado:
                    await conn.execute('UPDATE mensagens SET processada_em=now() WHERE tenant_id=$1 AND id=$2',tenant,msgid)
                    return {'ok':True,'ignorado':True,'limitado':True,'conversation_id':conversa,'mensagem_id':msgid}
            if msgid and cfg['modo_piloto'] and identificador not in cfg['contatos_permitidos']:
                await conn.execute('UPDATE mensagens SET processada_em=now() WHERE tenant_id=$1 AND id=$2',tenant,msgid)
                return {'ok':True,'ignorado':True,'conversation_id':conversa,'mensagem_id':msgid}
        # Em retry de webhook duplicado, o agendamento é reparado se a primeira chamada falhou no Redis.
        if humano:
            # Só chega aqui o #bot: se havia mensagem do cliente esperando, o bot responde agora.
            if retomar: await self.enfileirar(tenant,conversa,cfg)
            return {'ok':True,'from_me':True,'comando':'bot','duplicada':msgid is None}
        # A última mensagem decide a espera: a que parece completa encurta, a picada mantém.
        await self.enfileirar(tenant,conversa,cfg,debounce_da_mensagem(cfg,m) if msgid else None)
        return {'ok':True,'duplicada':msgid is None,'conversation_id':conversa,'mensagem_id':msgid}

    async def permitido(self,tenant,conversa,agente_id,repo=None,configuracoes=None):
        repo=repo or self.repo
        conv=await repo.obter(tenant,'conversas',conversa)
        if not conv or conv['agente_id']!=agente_id: return False
        canal,empresa,cfg=await self.contexto(tenant,conv['canal_id'],repo,configuracoes)
        return (canal['ativo'] and canal['agente_id']==agente_id and empresa['status']=='ativo' and cfg['status']=='ativo'
            and not (conv['bot_pausado_ate'] and conv['bot_pausado_ate']>datetime.now(timezone.utc))
            and (not cfg['modo_piloto'] or conv['telefone'] in cfg['contatos_permitidos']))

    async def enviar(self,tenant,conversa,agente_id,texto,escolhas=None):
        # A mesma trava de assumir/devolver serializa a pausa e o envio final.
        async with self.db.acquire() as conn,conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',conversa)
            repo=Repositorio(conn);configuracoes=Configuracoes(repo)
            if not await self.permitido(tenant,conversa,agente_id,repo,configuracoes): return False
            conv=await repo.obter(tenant,'conversas',conversa)
            canal,empresa,cfg=await self.contexto(tenant,conv['canal_id'],repo,configuracoes)
            escolhas=await self._com_unidades(conn,tenant,escolhas)
            plano=botoes.planejar(escolhas,canal['tipo'],cfg['botoes_interativos']=='on')
            marca,plano=await self._despachar(conn,tenant,conversa,canal,cfg,conv['telefone'],texto,plano,botoes.titulo_da_lista(escolhas))
            await conn.execute("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,message_id,meta)
                VALUES ($1,$2,'saida','bot',$3,now(),$4,$5::jsonb) ON CONFLICT(tenant_id,message_id) DO NOTHING""",tenant,conversa,
                botoes.numerado(texto,plano[2]),marca[0],_meta(marca[1],plano if escolhas else None))
            if escolhas: await self._plano_b(conn,tenant,conversa,plano[1])
            await conn.execute('UPDATE conversas SET atualizado_em=now() WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
            await auditar(conn,tenant,'sistema','mensagem.enviada',conversa)
        return True

    async def enviar_transacional(self,tenant,conversa,texto,aviso,marca_meta,auditoria):
        """Mensagem que o gateway envia por conta própria (aviso de pedido, fora do horário, equipe ausente).
        Aviso de status do pedido ao cliente (PED1): não é resposta do bot, então a pausa por atendente não o segura
        e ele não mexe na pausa. Respeita empresa/agente/canal ativos, o piloto e o envio de teste.
        Devolve 'enviado', 'simulado' (envio de teste ou canal sem envio) ou 'descartado'. Erro do provedor sobe para quem chamou."""
        async with self.db.acquire() as conn,conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',conversa)
            repo=Repositorio(conn);configuracoes=Configuracoes(repo)
            conv=await repo.obter(tenant,'conversas',conversa)
            if not conv or not conv['telefone']: return 'descartado'   # conversa anonimizada não recebe aviso
            canal,empresa,cfg=await self.contexto(tenant,conv['canal_id'],repo,configuracoes)
            if not (canal['ativo'] and empresa['status']=='ativo' and cfg['status']=='ativo'): return 'descartado'
            if cfg['modo_piloto'] and conv['telefone'] not in cfg['contatos_permitidos']: return 'descartado'
            real=cfg['send_mode']=='live' and canal['tipo']=='uazapi'
            marca=uazapi.marca_do_envio(canal['id'],await uazapi.enviar(self.http,self.cofre,tenant,canal,conv['telefone'],texto)) if real else (None,None)
            meta=json.loads(marca[1]) if marca[1] else {}
            meta[marca_meta]=aviso
            # Entra no histórico como "sistema": o agente vê o que o cliente já recebeu; com o id do provedor, a varredura não o confunde com atendente.
            await conn.execute("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,message_id,meta)
                VALUES ($1,$2,'saida','sistema',$3,now(),$4,$5::jsonb) ON CONFLICT(tenant_id,message_id) DO NOTHING""",tenant,conversa,texto,marca[0],json.dumps(meta,ensure_ascii=False))
            await conn.execute('UPDATE conversas SET atualizado_em=now() WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
            await auditar(conn,tenant,'sistema',auditoria+('_enviado' if real else '_simulado'),conversa)
            return 'enviado' if real else 'simulado'

    async def processar(self,tenant,conversa):
        cfg=None
        conv=await self.repo.obter(tenant,'conversas',conversa)
        if not conv: return
        cfg=await self.cfg.efetiva(tenant,conv['agente_id'])
        chave=self.chave(tenant,conversa)
        lock=self.prefixo+':lock:'+hashlib.sha256(chave.encode()).hexdigest()
        token=uuid.uuid4().hex
        if not await self.redis.set(lock,token,nx=True,ex=cfg['timeout_s']+90): return
        try:
            await self.redis.zrem(self.prefixo+':due',chave)
            await self.redis.delete(self.prefixo+':first:'+chave)
            itens=await self.db.fetch("SELECT id,conteudo,criado_em,origem,evento,enviada_em,tipo,meta FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema') ORDER BY id LIMIT 100",tenant,conversa)
            if not itens: return
            # Não misturar eventos de sistema com mensagens do cliente nem entre si.
            origem=itens[0]['origem']
            if origem=='sistema': itens=itens[:1]
            else:
                corte=next((i for i,m in enumerate(itens) if m['origem']=='sistema'),len(itens))
                itens=itens[:corte]
            ids=[i['id'] for i in itens]
            inicio=datetime.now(timezone.utc);rid=str(uuid.uuid4())
            resultado=None;erro=None;status='ok';entregue=False;aviso=False;fim_agente=inicio;motivo=None
            if not await self.permitido(tenant,conversa,cfg['id']):
                status='ignorado'
            else:
                try:
                    if origem=='cliente': await self._antes_do_turno(tenant,conversa,cfg)
                    payload=await skyone.pacote(self.repo,tenant,conversa,min(ids),'\n'.join(i['conteudo'] for i in itens),cfg,rid,lote=itens)
                    if origem=='sistema':
                        payload.update(tarefa='notificar',evento=json.loads(itens[0]['evento']) if isinstance(itens[0]['evento'],str) else itens[0]['evento'])
                    tarefa=asyncio.create_task(self.consultar(tenant,cfg,payload))
                    try:
                        feito,_=await asyncio.wait({tarefa},timeout=cfg['slow_notice_s'])
                        # AVS1: texto de espera só no modo com texto e quando a conversa está em andamento.
                        texto=None if feito or cfg.get('aviso_espera_modo')!='presenca_e_texto_contextual' or origem!='cliente' else await self._texto_de_espera(tenant,conversa,cfg,itens,inicio)
                        if texto:
                            aviso=await self.enviar(tenant,conversa,cfg['id'],texto)
                            entregue=aviso
                        resultado=await tarefa
                        await converter_transferencia(self.db,tenant,conversa,cfg,resultado)
                    finally:
                        if not tarefa.done():
                            tarefa.cancel()
                            await asyncio.gather(tarefa,return_exceptions=True)
                except (CredencialRecusada,CircuitoAberto) as falha:
                    erro='SKYONE_CREDENTIAL_REJECTED' if isinstance(falha,CredencialRecusada) else 'SKYONE_CIRCUIT_OPEN'
                    status='erro';motivo=motivo_fallback(falha)
                    resultado={'resposta':[cfg['fallback']],'acao':'nenhuma'}
                except Exception as falha:
                    # O tipo e a mensagem de exceções de terceiros não são logados.
                    erro='AGENT_TIMEOUT' if isinstance(falha,TempoEsgotado) else 'AGENT_ERROR'
                    status='erro';motivo=motivo_fallback(falha)
                    resultado={'resposta':[cfg['fallback']],'acao':'nenhuma'}
                if resultado and resultado.get('silencio_sem_motivo'): status='erro';erro='SILENCE_WITHOUT_REASON'
                fim_agente=datetime.now(timezone.utc)
                try:
                    for n,texto in enumerate(resultado['resposta'],start=1):
                        if not await self.enviar(tenant,conversa,cfg['id'],texto,resultado.get('botoes') if n==len(resultado['resposta']) else None): status='ignorado';break
                        entregue=True
                    if resultado.get('acao')=='transferir_humano':
                        await self.db.execute('UPDATE conversas SET bot_pausado_ate=now()+make_interval(mins=>$3) WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,cfg['pause_minutes'])
                except Exception:
                    status='erro';erro='SEND_ERROR'
            fim=datetime.now(timezone.utc)
            async with self.db.acquire() as conn,conn.transaction():
                await conn.execute('''INSERT INTO turnos(tenant_id,conversation_id,agente_id,canal_id,request_id,n_mensagens,
                    primeira_msg_em,ultima_msg_em,agente_inicio,agente_fim,enviado_em,espera_ms,agente_ms,total_ms,modo_agente,status,erro,resposta,aviso_demora)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$18,$11,$12,$13,$14,$15,$16,$17::jsonb,$19)''',
                    tenant,conversa,cfg['id'],conv['canal_id'],uuid.UUID(rid),len(itens),itens[0]['criado_em'],itens[-1]['criado_em'],inicio,fim_agente,
                    int((inicio-itens[-1]['criado_em']).total_seconds()*1000),int((fim_agente-inicio).total_seconds()*1000),
                    int((fim-itens[-1]['criado_em']).total_seconds()*1000),cfg['agente_mode'],status,erro,json.dumps(resultado) if resultado else None,fim if entregue else None,aviso)
                await conn.execute('UPDATE turnos SET origem=$3,uso=$4::jsonb,acao=$5,motivo_silencio=$6,desfecho=$7,prompt_versao=$8 WHERE tenant_id=$1 AND request_id=$2',tenant,uuid.UUID(rid),origem,json.dumps(resultado.get('uso')) if resultado and resultado.get('uso') else None,*campos_do_retorno(resultado))
                if not motivo and status!='ignorado' and resultado and resultado.get('acao')=='transferir_humano':
                    motivo='silencio_sem_motivo' if resultado.get('silencio_sem_motivo') else 'transferido_pelo_agente'
                # Ordem de travas: consumo antes de auditoria em todos os turnos.
                from nucleo.consumo import registrar,atualizar_cache
                mes,total=await registrar(conn,tenant)
                if motivo: await marcar_aguardando(conn,tenant,conversa,motivo)
                if resultado and resultado.get('nome_cliente'): await gravar_informado(conn,tenant,conversa,resultado['nome_cliente'])
                if status=='ok' and resultado and resultado.get('acao')=='notificar_equipe': await registrar_solicitacao(conn,tenant,conversa,cfg,resultado,PAINEL_URL)
                if resultado and resultado.get('acao_original'): await conn.execute('UPDATE turnos SET acao_original=$3 WHERE tenant_id=$1 AND request_id=$2',tenant,uuid.UUID(rid),resultado['acao_original'])
                if origem=='sistema' and status=='ignorado':
                    await auditar(conn,tenant,'sistema','notificacao.nao_enviada',conversa)
                if status=='ok' and resultado and resultado.get('acao')=='encerrar':
                    await encerrar_conversa(conn,tenant,conversa,resultado.get('desfecho'))
                if status=='ok' and resultado and resultado.get('acao') in ('transferir_humano','encerrar'):
                    from nucleo.webhooks import emitir
                    await emitir(conn,tenant,'conversa.transferida' if resultado['acao']=='transferir_humano' else 'conversa.encerrada',{'conversation_id':conversa,'agente_id':cfg['id']})
                if status=='ok' and origem=='cliente': await self._encerramento_do_turno(conn,tenant,conversa,cfg,ids,resultado)
                await conn.execute('UPDATE mensagens SET processada_em=now() WHERE tenant_id=$1 AND conversation_id=$2 AND id=ANY($3::bigint[])',tenant,conversa,ids)
                await auditar(conn,tenant,'sistema','turno.'+status,conversa)
            await atualizar_cache(self.redis,self.prefixo,tenant,mes,total)
        finally:
            await self.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,lock,token)
            if await self.db.fetchval("SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema'))",tenant,conversa):
                await self.enfileirar(tenant,conversa,cfg)

    # ---------------- Fila durável (Fase 0.6): processamento por etapas ----------------

    def _trava(self,tenant,conversa):
        return self.prefixo+':lock:'+hashlib.sha256(self.chave(tenant,conversa).encode()).hexdigest()

    async def consultar(self,tenant,cfg,payload):
        """Chama o agente. Silêncio sem motivo (INV04): consulta mais uma vez e, persistindo,
        transfere para humano sem enviar nada ao cliente."""
        try: return await self.chamar(self.http,self.repo,self.cofre,tenant,cfg,payload)
        except SilencioSemMotivo: pass
        try: return await self.chamar(self.http,self.repo,self.cofre,tenant,cfg,payload)
        except SilencioSemMotivo:
            log.warning('Agente em silêncio sem motivo nas duas consultas; conversa vai para atendente')
            return {'resposta':[],'acao':'transferir_humano','motivo':'silencio_sem_motivo','silencio_sem_motivo':True}

    async def digitando(self,tenant,conv,cfg,presenca='composing'):
        """"Digitando..." no canal enquanto o agente processa. Só com envio real e canal que
        suporta; é cortesia: qualquer falha é ignorada e nunca atrasa nem derruba o turno."""
        try:
            if cfg['send_mode']!='live': return False
            canal=await self.repo.obter(tenant,'tenant_canais',conv['canal_id'])
            if not canal or canal['tipo']!='uazapi' or not canal['ativo']: return False
            await asyncio.wait_for(uazapi.digitando(self.http,self.cofre,tenant,canal,conv['telefone'],DIGITANDO_S*1000,presenca),timeout=5)
            return True
        except asyncio.CancelledError: raise
        except Exception:
            return False

    async def _manter_digitando(self,tenant,conv,cfg,parar):
        """AVS1: mantém o "digitando…" enquanto o agente processa, renovando antes de cada pedido acabar. Para quando `parar`
        é marcado, quando o canal não aceita (envio de teste, canal sem suporte) ou ao passar do limite de tempo do agente."""
        fim=time.monotonic()+float(cfg.get('timeout_s') or 60)+5
        while not parar.is_set() and time.monotonic()<fim:
            if not await self.digitando(tenant,conv,cfg): return
            try: await asyncio.wait_for(parar.wait(),timeout=RENOVAR_DIGITANDO_S)
            except asyncio.TimeoutError: pass

    async def _texto_de_espera(self,tenant,conversa,cfg,itens,inicio):
        """Texto do aviso de espera para este turno, ou None se ele não cabe (AVS1). Só no meio de algo: nunca em saudação,
        agradecimento, primeira mensagem de uma conversa nova ou retomada, nem logo depois de outro aviso. Na dúvida, não manda."""
        try:
            if parece_fechamento(itens) or parece_saudacao(itens) or await self._bot_falou_ha_pouco(tenant,conversa,cfg): return None
            ultima=await self.db.fetchrow("""SELECT conteudo,meta FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida'
                AND origem='bot' AND coalesce(tipo,'')<>'reacao' AND coalesce(parte,1)<>0 AND criado_em>now()-make_interval(mins=>$3)
                ORDER BY id DESC LIMIT 1""",tenant,conversa,ANDAMENTO_MIN)
            # O bot não falou nos últimos minutos: é conversa nova ou retomada.
            if ultima is None: return None
            meta=json.loads(ultima['meta']) if isinstance(ultima['meta'],str) else (ultima['meta'] or {})
            pendente=bool(meta.get('botoes')) or str(ultima['conteudo'] or '').rstrip().endswith('?')
            if not pendente:
                # Algo em andamento num módulo (ex.: carrinho com itens) ou Skill chamada neste turno.
                from modulos import ganchos
                for _,f in ganchos('em_andamento'):
                    if await f(self.db,tenant,conversa,'aviso_espera'): pendente=True;break
            if not pendente:
                pendente=await self.db.fetchval('SELECT EXISTS(SELECT 1 FROM skill_registro WHERE tenant_id=$1 AND conversation_id=$2 AND criado_em>=$3)',tenant,conversa,inicio)
            if not pendente: return None
            anterior=await self.db.fetchrow("""SELECT conteudo,criado_em>now()-make_interval(mins=>$3) AS recente FROM mensagens
                WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' AND origem='bot' AND parte=0 ORDER BY id DESC LIMIT 1""",tenant,conversa,ESPACO_ENTRE_AVISOS_MIN)
            if anterior and anterior['recente']: return None
            textos=[t for t in (cfg.get('aviso_espera_textos') or []) if isinstance(t,str) and t.strip()]
            if not textos: return None
            # Sorteia sem repetir o último aviso desta conversa.
            candidatos=[t for t in textos if not anterior or t!=anterior['conteudo']] or textos
            return random.choice(candidatos)
        except Exception:
            log.warning('Aviso de espera não avaliado; segue só com o "digitando"')
            return None

    async def _pendentes(self,conn,tenant,conversa):
        itens=await conn.fetch("SELECT id,conteudo,criado_em,origem,evento,enviada_em,tipo,meta FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema') ORDER BY id LIMIT 100",tenant,conversa)
        if not itens: return []
        # Não misturar eventos de sistema com mensagens do cliente nem entre si.
        if itens[0]['origem']=='sistema': return itens[:1]
        corte=next((i for i,m in enumerate(itens) if m['origem']=='sistema'),len(itens))
        return itens[:corte]

    async def processar_duravel(self,tenant,conversa,vaga=None):
        """Etapas gravadas antes de cada efeito: turno aberto → resposta → partes → conclusão.

        Retorna 'concluido', 'vazio', 'ocupado' (outra execução tem a trava da conversa
        e reagenda o que sobrar ao terminar) ou 'sem_vaga' (empresa ou agente no limite).

        `vaga` (da fila) reserva e devolve a vaga da empresa/agente. A reserva acontece DEPOIS de
        obter a trava da conversa e a devolução ANTES de soltá-la: só quem processa a conversa
        mexe na vaga dela (INV07). Um segundo item da mesma conversa sai como 'ocupado' sem tocar na vaga."""
        conv=await self.repo.obter(tenant,'conversas',conversa)
        if not conv: return 'vazio'
        cfg=await self.cfg.efetiva(tenant,conv['agente_id'])
        lock=self._trava(tenant,conversa);token=uuid.uuid4().hex
        if not await self.redis.set(lock,token,nx=True,ex=cfg['timeout_s']+90): return 'ocupado'
        reservada=False;sem_vaga=False
        try:
            if vaga is not None:
                if await vaga.reservar(cfg):
                    sem_vaga=True;return 'sem_vaga'
                reservada=True
            turno=await self.db.fetchrow("SELECT * FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 AND etapa IN ('processando','respondido') ORDER BY id DESC LIMIT 1",tenant,conversa)
            if turno and turno['etapa']=='processando':
                # Execução anterior caiu antes da resposta: nada chegou ao cliente; refaz.
                await self.db.execute("UPDATE turnos SET etapa='abandonado',status='abandonado' WHERE tenant_id=$1 AND id=$2",tenant,turno['id'])
                turno=None
            if turno is None:
                # O lote vai ser lido agora: a próxima mensagem começa uma espera (debounce) nova.
                # Sem isto a marca da primeira mensagem ficava por 24 h e as seguintes saíam sem debounce.
                await self.redis.delete(self.prefixo+':first:'+self.chave(tenant,conversa))
                turno=await self._responder(tenant,conversa,conv,cfg)
                if turno is None: return 'vazio'
                # UX2: chegou mensagem do cliente antes de a resposta sair → descarta e refaz com o lote completo.
                # Até MAX_REFAZER vezes (UX3.1); quem decide cada uma é _deve_refazer, que também registra a recusa:
                # a mensagem que sobrar vira o próximo turno.
                for _ in range(MAX_REFAZER+1):
                    if not await self._deve_refazer(tenant,conversa,cfg,turno): break
                    await self._descartar(tenant,conversa,turno)
                    # Mais uma consulta ao agente: a trava da conversa e a vaga ganham validade nova.
                    await self.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('EXPIRE',KEYS[1],ARGV[2]) else return 0 end",1,lock,token,cfg['timeout_s']+90)
                    if reservada: await vaga.renovar(cfg)
                    turno=await self._responder(tenant,conversa,conv,cfg)
                    if turno is None: return 'vazio'
                # Guardas do turno: uma só vez por turno, a resposta é descartada e o turno refeito com um recado no pacote.
                # Guardas da resposta dos módulos (PED2, pedidos: o agente disse que fechou o pedido sem fechar_pedido de sucesso).
                # HOR2: o agente transferiu por um motivo que exige recusa de Skill sem nenhuma Skill ter recusado nada.
                recado=coluna=None
                guarda=await self._guarda_da_resposta(tenant,conversa,cfg,turno)
                if guarda: recado,coluna=guarda['recado'],guarda['coluna']
                else:
                    recado=await self._transferencia_sem_recusa(tenant,conversa,cfg,turno)
                    if recado: coluna='transferencia_sem_recusa'
                if recado:
                    await self.db.execute(f"UPDATE turnos SET {coluna}='refeito' WHERE tenant_id=$1 AND id=$2",tenant,turno['id'])
                    await self._descartar(tenant,conversa,turno)
                    await self.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('EXPIRE',KEYS[1],ARGV[2]) else return 0 end",1,lock,token,cfg['timeout_s']+90)
                    if reservada: await vaga.renovar(cfg)
                    turno=await self._responder(tenant,conversa,conv,cfg,recado=recado)
                    if turno is None: return 'vazio'
            # Repetiu (ou o turno foi retomado depois de uma queda): a resposta barrada não sai; a transferência segue, com alerta.
            guarda=await self._guarda_da_resposta(tenant,conversa,cfg,turno)
            if guarda: turno=await self._segurar(tenant,conversa,cfg,turno,guarda)
            elif await self._transferencia_sem_recusa(tenant,conversa,cfg,turno): turno=await self._manter_transferencia(tenant,conversa,cfg,turno)
            await self._concluir(tenant,conversa,cfg,turno,await self._enviar_partes(tenant,conversa,cfg,turno))
            return 'concluido'
        finally:
            if reservada:
                # Devolve a vaga mesmo se o Redis caiu no meio do lote (insiste até ele voltar).
                try: await insistir(vaga.devolver,self.insistir_s)
                except Exception: log.warning('Vaga não devolvida; expira sozinha pela validade')
            await insistir(lambda: self.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,lock,token),self.insistir_s)
            # Sem vaga: quem chamou reagenda para logo (não passa pelo debounce de novo).
            if not sem_vaga and await self.db.fetchval("SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema'))",tenant,conversa):
                await insistir(lambda: self.enfileirar(tenant,conversa,cfg),self.insistir_s)

    async def _antes_do_turno(self,tenant,conversa,cfg):
        """Antes de montar o pacote, o que os módulos do agente fazem (pedidos: carrinho parado há mais de carrinho_expira_h
        é arquivado, UX3). Falha aqui não pode impedir a resposta ao cliente."""
        from modulos import ganchos
        for nome,f in ganchos('antes_do_turno',cfg.get('modulos') or ()):
            try: await f(self.db,tenant,conversa,cfg)
            except Exception: log.warning('Preparação do turno do módulo %s falhou; segue o turno',nome)

    async def _bot_falou_ha_pouco(self,tenant,conversa,cfg):
        """O bot enviou alguma mensagem nesta conversa dentro da janela do aviso de demora?"""
        janela=cfg.get('aviso_demora_janela_s') or 0
        if janela<=0: return False
        return await self.db.fetchval("""SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida'
            AND origem='bot' AND tipo<>'reacao' AND criado_em>now()-make_interval(secs=>$3))""",tenant,conversa,float(janela))

    async def _deve_refazer(self,tenant,conversa,cfg,turno):
        """Há mensagem nova do cliente fora do lote e nada da resposta saiu ainda? Nunca refaz quando o turno já teve
        efeito para fora (pedido fechado/cancelado, transferência, encerramento). O segundo refazer (UX3.1) só acontece
        se o turno refeito não chamou Skill nem enviou balão, e se o turno original começou há no máximo TETO_REFAZER_S;
        terceiro, nunca."""
        if cfg.get('refazer_turno')!='on' or turno['origem']!='cliente' or turno['status']!='ok': return False
        if not await self.db.fetchval("""SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND processada_em IS NULL
                AND direcao='entrada' AND origem='cliente' AND NOT (id=ANY($3::bigint[])))""",tenant,conversa,turno['mensagem_ids']): return False
        resultado=json.loads(turno['resposta']) if isinstance(turno['resposta'],str) else (turno['resposta'] or {})
        from modulos import declarados
        motivo=None
        if turno['refeito_de'] is not None:
            original=await self.db.fetchrow('SELECT refeito_de,agente_inicio FROM turnos WHERE tenant_id=$1 AND id=$2',tenant,turno['refeito_de'])
            if original is None or original['refeito_de'] is not None: motivo='ja_refeito'
            elif turno['aviso_demora'] or await self.db.fetchval("""SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2
                    AND direcao='saida' AND criado_em>=$3)""",tenant,conversa,turno['agente_inicio']): motivo='balao_enviado'
            elif await self.db.fetchval('SELECT EXISTS(SELECT 1 FROM skill_chamadas WHERE tenant_id=$1 AND conversation_id=$2 AND criado_em>=$3)',
                    tenant,conversa,turno['agente_inicio']): motivo='skill_chamada'
            elif await self.db.fetchval('SELECT extract(epoch FROM now()-$1::timestamptz)>$2',original['agente_inicio'],float(TETO_REFAZER_S)): motivo='teto_de_espera'
        if motivo: pass
        elif resultado.get('acao') in ACOES_COM_EFEITO+declarados('acoes_com_efeito'): motivo='acao_com_efeito'
        elif await self.db.fetchval("""SELECT EXISTS(SELECT 1 FROM skill_chamadas WHERE tenant_id=$1 AND conversation_id=$2 AND criado_em>=$3
                AND resultado='ok' AND skill=ANY($4::text[]))""",tenant,conversa,turno['agente_inicio'],list(declarados('skills_com_efeito'))): motivo='skill_com_efeito'
        if motivo:
            # A resposta segue e a mensagem nova vira o próximo turno.
            await self.db.execute('UPDATE turnos SET refazer_recusado=$3 WHERE tenant_id=$1 AND id=$2',tenant,turno['id'],motivo)
            return False
        return True

    async def _guarda_da_resposta(self,tenant,conversa,cfg,turno):
        """A resposta do turno cai numa guarda de algum módulo do agente (pedidos: anuncia pedido fechado sem fechar_pedido de
        sucesso)? Devolve a guarda, ou None. Falha na conferência não segura a resposta."""
        if turno['origem']!='cliente' or turno['status']!='ok': return None
        import importlib
        from modulos import REGISTRO
        resultado=json.loads(turno['resposta']) if isinstance(turno['resposta'],str) else (turno['resposta'] or {})
        for nome in cfg.get('modulos') or ():
            if not REGISTRO[nome].ganchos: continue
            for guarda in getattr(importlib.import_module(REGISTRO[nome].ganchos),'GUARDAS_DA_RESPOSTA',()):
                if turno[guarda['coluna']]=='segurado': continue
                try:
                    # Turno refeito: vale o que foi feito desde a primeira tentativa.
                    if await guarda['avaliar'](self.db,tenant,conversa,resultado,await self._inicio_da_cadeia(tenant,turno)): return guarda
                except Exception:
                    log.warning('Guarda da resposta do módulo %s não conferida; a resposta segue',nome)
        return None

    async def _inicio_da_cadeia(self,tenant,turno):
        """Início do primeiro turno quando este é refeito de outro(s): as Skills chamadas desde lá contam para este."""
        desde=turno['agente_inicio'];anterior=turno['refeito_de']
        for _ in range(MAX_REFAZER+2):
            if anterior is None: break
            o=await self.db.fetchrow('SELECT refeito_de,agente_inicio FROM turnos WHERE tenant_id=$1 AND id=$2',tenant,anterior)
            if o is None: break
            desde=min(desde,o['agente_inicio']);anterior=o['refeito_de']
        return desde

    async def _transferencia_sem_recusa(self,tenant,conversa,cfg,turno):
        """O turno transfere por um motivo que um módulo do agente declara "com recusa" (pedidos: pedido_para_confirmar) sem que
        nenhuma Skill tenha recusado nada nele (HOR2)? Esse motivo existe para o que o gateway recusou (item sem preço…); sem
        recusa, o agente não tentou. Devolve o recado ao agente, ou None. Falha na conferência não segura a resposta."""
        if turno['origem']!='cliente' or turno['status']!='ok' or turno['transferencia_sem_recusa']=='mantida': return None
        try:
            resultado=json.loads(turno['resposta']) if isinstance(turno['resposta'],str) else (turno['resposta'] or {})
            if resultado.get('acao')!='transferir_humano': return None
            recado=self._recados_de_recusa(cfg).get(resultado.get('motivo'))
            if not recado: return None
            if await self.db.fetchval('''SELECT EXISTS(SELECT 1 FROM skill_registro WHERE tenant_id=$1 AND conversation_id=$2
                AND status>=400 AND criado_em>=$3)''',tenant,conversa,await self._inicio_da_cadeia(tenant,turno)): return None
            return recado
        except Exception:
            log.warning('Transferência não conferida; a resposta segue')
            return None

    def _recados_de_recusa(self,cfg):
        """{motivo: recado ao agente} dos motivos "com recusa" dos módulos do agente."""
        import importlib
        from modulos import REGISTRO
        saida={}
        for nome in cfg.get('modulos') or ():
            m=REGISTRO[nome]
            recados=getattr(importlib.import_module(m.ganchos),'RECADOS_DE_RECUSA',{}) if m.ganchos else {}
            saida.update({motivo:recados[motivo] for motivo in m.motivos_com_recusa if motivo in recados})
        return saida

    async def _manter_transferencia(self,tenant,conversa,cfg,turno):
        """O agente repetiu a transferência sem recusa de Skill: ela segue (pode haver razão que o gateway não vê) e a operação é avisada."""
        async with self.db.acquire() as conn,conn.transaction():
            turno=await conn.fetchrow("UPDATE turnos SET transferencia_sem_recusa='mantida' WHERE tenant_id=$1 AND id=$2 RETURNING *",tenant,turno['id'])
            await auditar(conn,tenant,'sistema','transbordo.sem_recusa',conversa)
        motivo=str((json.loads(turno['resposta']) if isinstance(turno['resposta'],str) else (turno['resposta'] or {})).get('motivo') or '')
        log.warning('Transferência por %s sem recusa de Skill, mantida depois de refazer (empresa %s, turno %s)',motivo,tenant,turno['id'])
        try:
            from nucleo.alertas import Alertas
            await Alertas(self.redis,self.http,self.db,self.prefixo).avisar('aviso','transferência sem tentativa',
                f'Empresa: {tenant} · agente: {cfg["id"]} · turno {turno["id"]} · conversa de final {conversa[-4:]}\n'
                f'O agente transferiu por "{motivo.replace("_"," ")}" sem nenhuma Skill ter recusado nada, mesmo depois de refazer o turno.')
        except Exception: log.warning('Alerta de transferência sem tentativa não enviado')
        return turno

    async def _segurar(self,tenant,conversa,cfg,turno,guarda):
        """Troca a resposta do turno pela resposta segura da guarda (pedidos: texto fixo com o botão "Confirmar pedido") e avisa
        a operação. O que o agente respondeu fica guardado no turno (campo `segurado`) para auditoria."""
        original=json.loads(turno['resposta']) if isinstance(turno['resposta'],str) else (turno['resposta'] or {})
        nova=guarda['resposta_segura'](original)
        async with self.db.acquire() as conn,conn.transaction():
            turno=await conn.fetchrow(f"""UPDATE turnos SET resposta=$3::jsonb,acao='nenhuma',desfecho=NULL,motivo_silencio=NULL,{guarda['coluna']}='segurado'
                WHERE tenant_id=$1 AND id=$2 RETURNING *""",tenant,turno['id'],json.dumps(nova,ensure_ascii=False))
            await auditar(conn,tenant,'sistema',guarda['auditoria'],conversa)
        log.warning(guarda['log']+' (empresa %s, turno %s)',tenant,turno['id'])
        try:
            from nucleo.alertas import Alertas
            titulo,texto=guarda['alerta']
            await Alertas(self.redis,self.http,self.db,self.prefixo).avisar('aviso',titulo,
                f'Empresa: {tenant} · agente: {cfg["id"]} · turno {turno["id"]} · conversa de final {conversa[-4:]}\n'+texto)
        except Exception: log.warning('Alerta de resposta segurada não enviado')
        return turno

    async def _descartar(self,tenant,conversa,turno):
        """Conclui o turno como 'refeito': a resposta fica guardada para auditoria e não é enviada.
        As mensagens do lote continuam pendentes e entram no turno seguinte, junto com as novas."""
        async with self.db.acquire() as conn,conn.transaction():
            await conn.execute('''UPDATE turnos SET etapa='concluido',status='refeito',
                espera_ms=(extract(epoch FROM agente_inicio-ultima_msg_em)*1000)::int,
                agente_ms=(extract(epoch FROM coalesce(agente_fim,now())-agente_inicio)*1000)::int,
                total_ms=(extract(epoch FROM now()-ultima_msg_em)*1000)::int WHERE tenant_id=$1 AND id=$2''',tenant,turno['id'])
            # O agente foi consultado: conta no consumo, como qualquer turno.
            from nucleo.consumo import registrar,atualizar_cache
            mes,total=await registrar(conn,tenant)
            await auditar(conn,tenant,'sistema','resposta.descartada',conversa)
            await auditar(conn,tenant,'sistema','turno.refeito',conversa)
        await atualizar_cache(self.redis,self.prefixo,tenant,mes,total)
        log.info('Turno refeito: chegou mensagem do cliente antes de a resposta sair')

    async def _refeito_de(self,tenant,conversa,ids):
        """Turno descartado cujo lote está (em parte) nestas mensagens pendentes: este turno é o refazer dele.
        Lido do banco a cada vez, então vale também depois de queda do worker no meio."""
        return await self.db.fetchrow("""SELECT t.id,t.agente_fim,t.aviso_demora,
                coalesce((SELECT o.agente_inicio FROM turnos o WHERE o.tenant_id=t.tenant_id AND o.id=t.refeito_de AND o.status='refeito'),t.agente_inicio) agente_inicio
            FROM turnos t WHERE t.tenant_id=$1 AND t.conversation_id=$2
            AND t.status='refeito' AND t.mensagem_ids && $3::bigint[] ORDER BY t.id DESC LIMIT 1""",tenant,conversa,ids)

    async def _responder(self,tenant,conversa,conv,cfg,recado=None):
        itens=await self._pendentes(self.db,tenant,conversa)
        if not itens: return None
        origem=itens[0]['origem'];ids=[i['id'] for i in itens];rid=uuid.uuid4()
        descartado=await self._refeito_de(tenant,conversa,ids) if origem=='cliente' else None
        turno=await self.db.fetchrow('''INSERT INTO turnos(tenant_id,conversation_id,agente_id,canal_id,request_id,n_mensagens,
            primeira_msg_em,ultima_msg_em,agente_inicio,modo_agente,status,origem,etapa,mensagem_ids,refeito_de,perfil_versao)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,now(),$9,'em_andamento',$10,'processando',$11,$12,$13) RETURNING *''',
            tenant,conversa,cfg['id'],conv['canal_id'],rid,len(itens),itens[0]['criado_em'],itens[-1]['criado_em'],
            cfg['agente_mode'],origem,ids,descartado['id'] if descartado else None,cfg.get('perfil_versao'))
        resultado=None;erro=None;status='ok';aviso=False;presenca=None
        acoes=None
        if origem=='cliente': await self._antes_do_turno(tenant,conversa,cfg)
        if descartado:
            # O que a tentativa descartada já fez nas Skills dos módulos.
            from modulos import ganchos
            fim=descartado['agente_fim'] or datetime.now(timezone.utc);acoes=[]
            for _,f in ganchos('acoes_do_turno'): acoes+=await f(self.db,tenant,conversa,descartado['agente_inicio'],fim)
        if not await self.permitido(tenant,conversa,cfg['id']):
            status='ignorado'
        else:
            try:
                # H1-a, modo "só avisar": empresa fechada → o gateway responde sozinho (uma vez por período) e não chama o agente.
                if origem=='cliente':
                    pronta=await self._so_avisar(tenant,conversa,conv,cfg)
                    if pronta is not None: raise RespostaPronta(pronta)
                # ARQ1-a1: documento do lote ainda em leitura → espera até 20 s (vai como `processando` se não terminar).
                await _aguardar_anexos(self.db,itens)
                payload=await skyone.pacote(self.repo,tenant,conversa,min(ids),'\n'.join(i['conteudo'] for i in itens),cfg,str(rid),lote=itens,acoes=acoes,aviso=recado)
                if origem=='sistema':
                    payload.update(tarefa='notificar',evento=json.loads(itens[0]['evento']) if isinstance(itens[0]['evento'],str) else itens[0]['evento'])
                tarefa=asyncio.create_task(self.consultar(tenant,cfg,payload))
                # AVS1: "digitando…" pelo turno inteiro (renovado a cada 20 s); o texto de espera só no modo com texto.
                modo=cfg.get('aviso_espera_modo') or 'presenca';parar_presenca=asyncio.Event()
                if modo!='off': presenca=asyncio.create_task(self._manter_digitando(tenant,conv,cfg,parar_presenca))
                try:
                    if modo=='presenca_e_texto_contextual' and cfg['slow_notice_s'] and origem=='cliente' and not descartado:
                        feito,_=await asyncio.wait({tarefa},timeout=cfg['slow_notice_s'])
                        if not feito:
                            texto=await self._texto_de_espera(tenant,conversa,cfg,itens,turno['agente_inicio'])
                            if texto:
                                # O aviso é a parte 0 do turno: nunca sai duas vezes no mesmo turno.
                                aviso=await self._enviar_parte(tenant,conversa,cfg,rid,0,texto)=='enviada'
                                # Enviar mensagem cancela o "digitando" no canal: pede de novo.
                                if aviso: await self.digitando(tenant,conv,cfg)
                    resultado=await tarefa
                    await converter_transferencia(self.db,tenant,conversa,cfg,resultado)
                    parar_presenca.set()
                    if presenca: await asyncio.gather(presenca,return_exceptions=True)
                    # Nada vai ser enviado (silêncio ou transferência sem texto): tira o "digitando".
                    if not resultado.get('resposta'): await self.digitando(tenant,conv,cfg,'paused')
                finally:
                    if not tarefa.done():
                        tarefa.cancel()
                        await asyncio.gather(tarefa,return_exceptions=True)
            except RespostaPronta as pronta:
                resultado=pronta.resultado
            except (CredencialRecusada,CircuitoAberto) as falha:
                erro='SKYONE_CREDENTIAL_REJECTED' if isinstance(falha,CredencialRecusada) else 'SKYONE_CIRCUIT_OPEN'
                status='erro';resultado={'resposta':[cfg['fallback']],'acao':'nenhuma'}
            except Exception as falha:
                erro='AGENT_TIMEOUT' if isinstance(falha,TempoEsgotado) else 'AGENT_ERROR'
                status='erro';resultado={'resposta':[cfg['fallback']],'acao':'nenhuma'}
            finally:
                # Para de renovar e espera o pedido de "digitando" em curso terminar (é curto e tem limite próprio) em vez de cortá-lo no meio.
                if presenca:
                    parar_presenca.set()
                    await asyncio.gather(presenca,return_exceptions=True)
        # A resposta fica gravada antes de qualquer envio: se o worker cair, só falta enviar.
        if resultado and resultado.get('silencio_sem_motivo'): status='erro';erro='SILENCE_WITHOUT_REASON'
        return await self.db.fetchrow('''UPDATE turnos SET etapa='respondido',status=$3,erro=$4,resposta=$5::jsonb,
            agente_fim=now(),aviso_demora=$6,uso=$7::jsonb,acao=$8,motivo_silencio=$9,desfecho=$10,prompt_versao=$11 WHERE tenant_id=$1 AND id=$2 RETURNING *''',
            tenant,turno['id'],status,erro,json.dumps(resultado) if resultado else None,aviso,
            json.dumps(resultado.get('uso')) if resultado and resultado.get('uso') else None,*campos_do_retorno(resultado))

    async def _com_unidades(self,conn,tenant,escolhas):
        """Lista (B3): opção cujo id é o código de uma unidade da empresa ganha como descrição a situação real
        da unidade agora. Só quando as opções já iriam como lista; falha aqui não segura a resposta."""
        if not botoes.candidata_a_lista(escolhas): return escolhas
        try:
            from nucleo.horarios import resumos_das_unidades
            return botoes.com_descricoes(escolhas,await resumos_das_unidades(conn,tenant,[o.get('id') for o in escolhas['opcoes'] if isinstance(o,dict) and o.get('id')]))
        except Exception:
            log.warning('Situação das unidades não lida para a lista; segue sem descrição')
            return escolhas

    async def _despachar(self,conn,tenant,conversa,canal,efetiva,telefone,texto,plano,botao_da_lista=None):
        """Envia um balão pelo canal (se o envio for real) na forma planejada. Devolve (marca, plano final).
        Menu recusado pelo provedor → o mesmo balão em texto numerado, com aviso. A conversa não falha por botão."""
        forma,motivo,opcoes=plano;marca=(None,None)
        # ARQ1-a1: o canal api não tem provedor; a resposta vai pelo webhook de saída `conversa.resposta` (gravada como enviada).
        if efetiva['send_mode']=='live' and canal['tipo']!='api':
            if canal['tipo']!='uazapi': raise ValueError('Envio live deste canal ainda não implementado')
            if forma in ('botao','lista'):
                try:
                    if forma=='lista': enviado=await uazapi.enviar_lista(self.http,self.cofre,tenant,canal,telefone,texto,opcoes,botao_da_lista or botoes.BOTAO_DA_LISTA)
                    else: enviado=await uazapi.enviar_menu(self.http,self.cofre,tenant,canal,telefone,texto,opcoes)
                    return uazapi.marca_do_envio(canal['id'],enviado),plano
                except uazapi.MenuRecusado:
                    forma,motivo='texto','provedor_recusou'
            marca=uazapi.marca_do_envio(canal['id'],await uazapi.enviar(self.http,self.cofre,tenant,canal,telefone,botoes.numerado(texto,opcoes)))
        return marca,(forma,motivo,opcoes)

    async def _plano_b(self,conn,tenant,conversa,motivo):
        """Opções que saíram (ou sairiam) em texto no lugar de botão: aviso sem telefone e sem texto do cliente."""
        if not motivo: return
        log.warning('Botões enviados como texto numerado: %s',motivo)
        await auditar(conn,tenant,'sistema','botoes.fallback',conversa)

    async def _enviar_parte(self,tenant,conversa,cfg,rid,parte,texto,escolhas=None):
        """Grava 'enviando' antes do canal e 'enviada' depois. Parte já presa em 'enviando'
        (worker caiu no meio) vira 'incerta' e nunca é reenviada."""
        async with self.db.acquire() as conn:
            # Trava de sessão: a mesma chave de assumir/devolver, mantida durante o envio.
            await conn.execute('SELECT pg_advisory_lock(hashtextextended($1,0))',conversa)
            try:
                atual=await conn.fetchval('SELECT entrega FROM mensagens WHERE tenant_id=$1 AND turno_request_id=$2 AND parte=$3',tenant,rid,parte)
                if atual in ('enviada','incerta'): return atual
                if atual=='enviando':
                    await self._incerta(conn,tenant,conversa,rid,parte);return 'incerta'
                repo=Repositorio(conn);configuracoes=Configuracoes(repo)
                if not await self.permitido(tenant,conversa,cfg['id'],repo,configuracoes): return 'ignorada'
                conv=await repo.obter(tenant,'conversas',conversa)
                canal,empresa,efetiva=await self.contexto(tenant,conv['canal_id'],repo,configuracoes)
                escolhas=await self._com_unidades(conn,tenant,escolhas)
                plano=botoes.planejar(escolhas,canal['tipo'],efetiva['botoes_interativos']=='on')
                # No histórico o balão fica sempre com as opções numeradas, saiam elas como botão ou como texto.
                await conn.execute('''INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,turno_request_id,parte,entrega,meta)
                    VALUES ($1,$2,'saida','bot',$3,now(),$4,$5,'enviando',$6::jsonb)''',tenant,conversa,botoes.numerado(texto,plano[2]),rid,parte,
                    _meta(None,plano if escolhas else None))
                try:
                    marca,plano=await self._despachar(conn,tenant,conversa,canal,efetiva,conv['telefone'],texto,plano,botoes.titulo_da_lista(escolhas))
                except Exception:
                    # Não se sabe se o canal entregou: não reenviar, revisão humana.
                    if escolhas:
                        await conn.execute("UPDATE mensagens SET meta=$4::jsonb WHERE tenant_id=$1 AND turno_request_id=$2 AND parte=$3",tenant,rid,parte,
                                           _meta(None,(plano[0],'provedor_incerto',plano[2])))
                    await self._incerta(conn,tenant,conversa,rid,parte);raise
                async with conn.transaction():
                    await conn.execute("UPDATE mensagens SET entrega='enviada',enviada_em=now(),message_id=$4,meta=$5::jsonb WHERE tenant_id=$1 AND turno_request_id=$2 AND parte=$3",tenant,rid,parte,
                                       marca[0],_meta(marca[1],plano if escolhas else None))
                    if escolhas: await self._plano_b(conn,tenant,conversa,plano[1])
                    await conn.execute('UPDATE conversas SET atualizado_em=now() WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
                    await auditar(conn,tenant,'sistema','mensagem.enviada',conversa)
                    if canal['tipo']=='api': await _resposta_no_webhook(conn,tenant,conversa,conv,canal,cfg,texto,plano,rid,parte)
                return 'enviada'
            finally:
                await conn.execute('SELECT pg_advisory_unlock(hashtextextended($1,0))',conversa)

    async def _incerta(self,conn,tenant,conversa,rid,parte):
        async with conn.transaction():
            await conn.execute("UPDATE mensagens SET entrega='incerta' WHERE tenant_id=$1 AND turno_request_id=$2 AND parte=$3",tenant,rid,parte)
            await auditar(conn,tenant,'sistema','mensagem.entrega_incerta',conversa)
            await marcar_aguardando(conn,tenant,conversa,'entrega_incerta')
        log.warning('Entrega incerta: parte não reenviada, revisão humana')

    async def _enviar_partes(self,tenant,conversa,cfg,turno):
        """Envia as partes que faltam; devolve (status final, entregue)."""
        status=turno['status'];entregue=bool(turno['aviso_demora'])
        resultado=json.loads(turno['resposta']) if isinstance(turno['resposta'],str) else turno['resposta']
        if status=='ignorado' or not resultado: return status,entregue
        # R1: a reação sai antes dos balões e nunca muda o resultado do turno.
        await self._reagir(tenant,conversa,cfg,turno,resultado)
        try:
            ultima=len(resultado['resposta'])
            for i,texto in enumerate(resultado['resposta'],start=1):
                # Os botões pertencem ao último balão.
                r=await self._enviar_parte(tenant,conversa,cfg,turno['request_id'],i,texto,resultado.get('botoes') if i==ultima else None)
                if r=='ignorada': status='ignorado';break
                if r=='enviada': entregue=True
            if resultado.get('acao')=='transferir_humano' and status!='ignorado':
                # T1: fora do horário da equipe o cliente é avisado e o bot NÃO é pausado (segue atendendo); dentro, pausa como sempre.
                if await self._equipe_ausente(tenant,conversa,cfg,turno): pass
                else: await self.db.execute('UPDATE conversas SET bot_pausado_ate=now()+make_interval(mins=>$3) WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,cfg['pause_minutes'])
        except Exception:
            status='erro'
            await self.db.execute("UPDATE turnos SET erro='SEND_ERROR' WHERE tenant_id=$1 AND id=$2",tenant,turno['id'])
        return status,entregue

    async def _so_avisar(self,tenant,conversa,conv,cfg):
        """H1-a: no modo "só avisar", com todas as unidades fechadas, devolve a resposta pronta do turno (sem agente).
        None = segue o caminho normal (modo "atender", empresa aberta, ou conversa com pedido ativo)."""
        if cfg.get('fora_do_horario')!='so_avisar': return None
        from nucleo import expediente
        from modulos import ganchos
        empresa=await self.repo.tenant(tenant)
        s=await expediente.da_empresa(self.db,tenant,empresa['timezone'])
        if not s['fora']:
            # Reabriu: o período fechado acabou; se fechar de novo, o cliente é avisado de novo.
            await self.db.execute('UPDATE conversas SET fora_do_horario_avisado_ate=NULL WHERE tenant_id=$1 AND conversation_id=$2 AND fora_do_horario_avisado_ate IS NOT NULL',tenant,conversa)
            return None
        # Quem tem algo em andamento (pedidos: pedido ativo) precisa conseguir perguntar por ele: o agente atende.
        for _,f in ganchos('em_andamento'):
            if await f(self.db,tenant,conversa,'fora_do_horario'): return None
        silencio={'resposta':[],'acao':'silencio','motivo':'fora_do_horario'}
        if await self.db.fetchval('SELECT fora_do_horario_avisado_ate>now() FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa):
            return {**silencio,'fora_do_horario':'sem_resposta'}
        texto=expediente.renderizar(cfg.get('fora_do_horario_mensagem') or '',{'proxima_abertura':expediente.em_palavras(s['proxima'],s['fuso']),
            'abre':expediente.em_palavras(s['proxima'],s['fuso']),'nome_empresa':(cfg.get('perfil_atendimento') or {}).get('nome_empresa') or empresa['nome']})
        if texto: await self.enviar_transacional(tenant,conversa,texto,{'ate':s['proxima']},'fora_do_horario','fora_do_horario.aviso')
        # Um aviso por período fechado: vale até a próxima abertura (ou 12 h, se ela não é conhecida).
        await self.db.execute("""UPDATE conversas SET fora_do_horario_avisado_ate=coalesce($3::timestamptz,now()+make_interval(hours=>$4))
            WHERE tenant_id=$1 AND conversation_id=$2""",tenant,conversa,datetime.fromisoformat(s['proxima']) if s['proxima'] else None,expediente.SEM_PERIODO_H)
        return {**silencio,'fora_do_horario':'avisado'}

    async def _equipe_ausente(self,tenant,conversa,cfg,turno):
        """T1: a transferência caiu fora do horário da equipe? Registra no turno e, na primeira vez enquanto a conversa
        não está aguardando, envia a mensagem de equipe ausente. Qualquer falha aqui vale como "equipe disponível"."""
        try:
            from nucleo import expediente
            empresa=await self.repo.tenant(tenant)
            eq=await expediente.da_equipe(self.db,tenant,cfg,empresa['timezone'])
            ausente=not eq['disponivel']
            await self.db.execute('UPDATE turnos SET equipe_fora_do_horario=$3 WHERE tenant_id=$1 AND id=$2',tenant,turno['id'],ausente)
            if not ausente: return False
            ja_aguardava=await self.db.fetchval('SELECT aguardando_atendente_desde IS NOT NULL FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
            volta=expediente.em_palavras(eq['retorno'],eq['fuso'])
            texto=expediente.renderizar(cfg.get('transbordo_fora_do_horario') or '',{'retorno_equipe':volta,'horario':volta})
            if texto and not ja_aguardava: await self.enviar_transacional(tenant,conversa,texto,{'retorno':eq['retorno']},'equipe_ausente','transbordo.equipe_ausente')
            return True
        except Exception:
            log.warning('Horário da equipe não avaliado; transferência segue como dentro do horário')
            return False

    async def _reagir(self,tenant,conversa,cfg,turno,resultado):
        """R1: reage à última mensagem de texto do cliente no lote, se o agente pediu e as travas deixarem.
        O que aconteceu fica em turnos.reacao. Nunca levanta erro e nunca é tentada de novo."""
        emoji=resultado.get('reacao')
        if not emoji or turno['reacao'] is not None: return
        from nucleo import reacoes
        registro={'emoji':emoji,'resultado':'bloqueada','motivo':None}
        try:
            conv=await self.repo.obter(tenant,'conversas',conversa)
            canal,empresa,efetiva=await self.contexto(tenant,conv['canal_id'])
            motivo,alvo=await reacoes.avaliar(self.db,tenant,conversa,efetiva,resultado,turno['mensagem_ids'],empresa['timezone'])
            if not motivo and canal['tipo']!='uazapi': motivo='canal'
            if not motivo and not await self.permitido(tenant,conversa,cfg['id']): motivo='nao_permitido'
            if motivo: registro['motivo']=motivo
            else:
                # Marca antes de falar com o provedor: se o worker cair no meio, a retomada não reage de novo.
                await self.db.execute('UPDATE turnos SET reacao=$3::jsonb WHERE tenant_id=$1 AND id=$2',tenant,turno['id'],json.dumps({**registro,'resultado':'enviando'},ensure_ascii=False))
                alvo_id=alvo['message_id'].split(':',1)[1];marca=(None,None);registro['resultado']='simulada'
                if efetiva['send_mode']=='live':
                    marca=uazapi.marca_do_envio(canal['id'],await uazapi.reagir(self.http,self.cofre,tenant,canal,alvo_id,emoji));registro['resultado']='aplicada'
                meta=json.loads(marca[1]) if marca[1] else {}
                meta.update(reacao_a=alvo_id,emoji=emoji)
                await self.db.execute("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,tipo,processada_em,message_id,meta)
                    VALUES ($1,$2,'saida','bot',$3,'reacao',now(),$4,$5::jsonb) ON CONFLICT(tenant_id,message_id) DO NOTHING""",
                    tenant,conversa,f'[reação: {emoji}]',marca[0],json.dumps(meta,ensure_ascii=False))
        except Exception as erro:
            registro.update(resultado='bloqueada',motivo='falha_provedor')
            log.warning('Reação não enviada (%s); o turno segue',type(erro).__name__)
        try: await self.db.execute('UPDATE turnos SET reacao=$3::jsonb WHERE tenant_id=$1 AND id=$2',tenant,turno['id'],json.dumps(registro,ensure_ascii=False))
        except Exception: log.warning('Resultado da reação não registrado')

    async def _concluir(self,tenant,conversa,cfg,turno,envio):
        status,entregue=envio
        resultado=json.loads(turno['resposta']) if isinstance(turno['resposta'],str) else turno['resposta']
        motivo={'SKYONE_CIRCUIT_OPEN':'circuito_aberto','AGENT_TIMEOUT':'tempo_esgotado',
                'AGENT_ERROR':'erro_agente','SKYONE_CREDENTIAL_REJECTED':'erro_agente'}.get(turno['erro'])
        if not motivo and status!='ignorado' and resultado and resultado.get('acao')=='transferir_humano':
            motivo='silencio_sem_motivo' if resultado.get('silencio_sem_motivo') else 'transferido_pelo_agente'
        async with self.db.acquire() as conn,conn.transaction():
            await conn.execute('''UPDATE turnos SET etapa='concluido',status=$3,enviado_em=CASE WHEN $4 THEN now() END,
                espera_ms=(extract(epoch FROM agente_inicio-ultima_msg_em)*1000)::int,
                agente_ms=(extract(epoch FROM coalesce(agente_fim,now())-agente_inicio)*1000)::int,
                total_ms=(extract(epoch FROM now()-ultima_msg_em)*1000)::int
                WHERE tenant_id=$1 AND id=$2''',tenant,turno['id'],status,entregue)
            # Ordem de travas: consumo antes de auditoria em todos os turnos.
            from nucleo.consumo import registrar,atualizar_cache
            mes,total=await registrar(conn,tenant)
            if motivo:
                ausente=bool(await conn.fetchval('SELECT equipe_fora_do_horario FROM turnos WHERE tenant_id=$1 AND id=$2',tenant,turno['id']))
                await marcar_aguardando(conn,tenant,conversa,motivo,resultado.get('motivo') if resultado and motivo=='transferido_pelo_agente' else None,cfg,ausente)
            if resultado and resultado.get('nome_cliente'): await gravar_informado(conn,tenant,conversa,resultado['nome_cliente'])
            # SOL1: avisa a equipe e segue atendendo (sem pausa, sem "aguardando atendente"). Só a resposta que saiu registra.
            if status=='ok' and resultado and resultado.get('acao')=='notificar_equipe': await registrar_solicitacao(conn,tenant,conversa,cfg,resultado,PAINEL_URL)
            if resultado and resultado.get('acao_original'): await conn.execute('UPDATE turnos SET acao_original=$3 WHERE tenant_id=$1 AND id=$2',tenant,turno['id'],resultado['acao_original'])
            if turno['origem']=='sistema' and status=='ignorado':
                await auditar(conn,tenant,'sistema','notificacao.nao_enviada',conversa)
            # INV03 antes do envio: o agente respondeu, mas a conversa foi pausada (ou saiu do piloto)
            # antes de a resposta sair. A resposta é descartada e fica registrado.
            if status=='ignorado' and turno['status']!='ignorado' and resultado and resultado.get('resposta'):
                await auditar(conn,tenant,'sistema','resposta.descartada',conversa)
            if status=='ok' and resultado and resultado.get('acao')=='encerrar':
                await encerrar_conversa(conn,tenant,conversa,resultado.get('desfecho'))
            if status=='ok' and resultado and resultado.get('acao') in ('transferir_humano','encerrar'):
                from nucleo.webhooks import emitir
                await emitir(conn,tenant,'conversa.transferida' if resultado['acao']=='transferir_humano' else 'conversa.encerrada',{'conversation_id':conversa,'agente_id':cfg['id']})
            if status=='ok' and turno['origem']=='cliente': await self._encerramento_do_turno(conn,tenant,conversa,cfg,turno['mensagem_ids'],resultado)
            await conn.execute('UPDATE mensagens SET processada_em=now() WHERE tenant_id=$1 AND conversation_id=$2 AND id=ANY($3::bigint[])',tenant,conversa,turno['mensagem_ids'])
            await auditar(conn,tenant,'sistema','turno.'+status,conversa)
        await atualizar_cache(self.redis,self.prefixo,tenant,mes,total)

    async def _encerramento_do_turno(self,conn,tenant,conversa,cfg,mensagem_ids,resultado):
        """ENC1-a (E3, decisão 4): o lote do cliente é só agradecimento/fechamento ("obrigado", "valeu", 👍; a mensagem inteira, como
        no AVS1), o bot não deixou botões esperando resposta e um módulo diz que o atendimento terminou (pedidos: pedido fechado nele,
        sem carrinho aberto com itens) → encerra na hora, qualquer que tenha sido a resposta (texto, silêncio ou reação). Sem despedida (E1)."""
        if resultado and resultado.get('acao') in ('transferir_humano','encerrar','notificar_equipe'): return
        a=await atendimentos.atual(conn,tenant,conversa)
        if not a or a['estado']!='aberto': return
        itens=await conn.fetch('SELECT conteudo,tipo FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND id=ANY($3::bigint[]) ORDER BY id',tenant,conversa,mensagem_ids)
        if not itens or not parece_fechamento([dict(i) for i in itens]): return
        # Corrida: o cliente já mandou outra coisa depois deste lote ("obrigado" … "e o horário?") → não encerra.
        if await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='entrada'
            AND origem='cliente' AND processada_em IS NULL AND NOT (id=ANY($3::bigint[])))""",tenant,conversa,mensagem_ids): return
        # Botões ou lista do bot à espera de escolha: o "ok" pode ser resposta a eles.
        ultima=await conn.fetchval("""SELECT meta FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' AND origem='bot'
            AND coalesce(tipo,'')<>'reacao' AND coalesce(parte,1)<>0 AND id<$3 ORDER BY id DESC LIMIT 1""",tenant,conversa,min(mensagem_ids))
        if atendimentos.com_botoes_pendentes(ultima): return
        from modulos import ganchos
        for _,f in ganchos('encerra_no_turno',cfg.get('modulos') or ()):
            fim=await f(conn,tenant,conversa,a['id'])
            if fim:
                motivo,desfecho=fim
                await encerrar_conversa(conn,tenant,conversa,(resultado or {}).get('desfecho') or desfecho,motivo)
                return

    async def encerrar_sem_retorno(self):
        """ENC1-a (E3): atendimento com pedido fechado e nenhuma mensagem do cliente nos 30 min seguintes → encerra (pedido_sem_retorno).
        Conversa com atendente (aguardando ou pausada) fica como está. Confere de novo sob a trava da conversa."""
        from modulos import ganchos
        encerrados=0
        for _,f in ganchos('sem_retorno'):
            for tenant,conversa,desfecho in await f(self.db):
                async with self.db.acquire() as conn,conn.transaction():
                    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',conversa)
                    if not await f(conn,tenant,conversa): continue
                    if await conn.fetchval("""SELECT aguardando_atendente_desde IS NOT NULL OR coalesce(bot_pausado_ate>now(),false) FROM conversas
                        WHERE tenant_id=$1 AND conversation_id=$2""",tenant,conversa): continue
                    if await encerrar_conversa(conn,tenant,conversa,desfecho,'pedido_sem_retorno'): encerrados+=1
        return encerrados

    async def recuperar(self):
        # Enumeração de trabalho com tenant retornado explicitamente; cada processamento usa o escopo.
        rows=await self.db.fetch("SELECT DISTINCT tenant_id,conversation_id FROM mensagens WHERE processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema')")
        for row in rows:
            conv=await self.repo.obter(row['tenant_id'],'conversas',row['conversation_id'])
            cfg=await self.cfg.efetiva(row['tenant_id'],conv['agente_id'])
            await self.enfileirar(row['tenant_id'],row['conversation_id'],cfg)

    async def encerrar_vazias(self,limite=200):
        """Encerra com desfecho "vazia" a conversa em que o cliente só mandou o que o gateway não lê
        (anexo, figurinha, reação) e ninguém mais falou depois de encerrar_vazia_min."""
        candidatas=await self.db.fetch("""SELECT c.tenant_id,c.conversation_id,c.agente_id,
              (SELECT max(criado_em) FROM mensagens m WHERE m.tenant_id=c.tenant_id AND m.conversation_id=c.conversation_id) AS ultima
            FROM conversas c WHERE c.encerrada_em IS NULL AND c.aguardando_atendente_desde IS NULL
              AND (c.bot_pausado_ate IS NULL OR c.bot_pausado_ate<now())
              AND EXISTS(SELECT 1 FROM mensagens m WHERE m.tenant_id=c.tenant_id AND m.conversation_id=c.conversation_id AND m.origem='cliente')
              AND NOT EXISTS(SELECT 1 FROM mensagens m WHERE m.tenant_id=c.tenant_id AND m.conversation_id=c.conversation_id
                    AND (m.origem='humano' OR (m.origem='cliente' AND (m.tipo IN ('texto','localizacao') OR m.meta ? 'anexo_id'))
                         OR (m.direcao='entrada' AND m.processada_em IS NULL)))
            ORDER BY c.atualizado_em LIMIT $1""",limite)
        encerradas=0;agora=datetime.now(timezone.utc)
        for c in candidatas:
            try: cfg=await self.cfg.efetiva(c['tenant_id'],c['agente_id'])
            except LookupError: continue
            if not c['ultima'] or agora-c['ultima']<timedelta(minutes=cfg['encerrar_vazia_min']): continue
            async with self.db.acquire() as conn,conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',c['conversation_id'])
                # Confere de novo sob a trava: o cliente pode ter escrito neste instante.
                if await conn.fetchval("""SELECT max(criado_em) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2""",c['tenant_id'],c['conversation_id'])!=c['ultima']: continue
                if await encerrar_conversa(conn,c['tenant_id'],c['conversation_id'],'vazia','vazio'):
                    await auditar(conn,c['tenant_id'],'sistema','conversa.encerrada',c['conversation_id']);encerradas+=1
        return encerradas

    async def limpeza(self):
        # Retenção de webhook por tenant; sem logs de conteúdo ou telefones.
        for row in await self.db.fetch('SELECT id,retencao_dias FROM tenants'):
            await self.db.execute('DELETE FROM webhook_raw WHERE tenant_id=$1 AND recebido_em < now()-make_interval(days=>$2)',row['id'],row['retencao_dias'])
            await self.db.execute('DELETE FROM skill_chamadas WHERE tenant_id=$1 AND criado_em < now()-make_interval(days=>$2)',row['id'],row['retencao_dias'])
            await self.db.execute('DELETE FROM skill_registro WHERE tenant_id=$1 AND criado_em < now()-make_interval(days=>$2)',row['id'],row['retencao_dias'])
            await self.db.execute("DELETE FROM catalogo_vistos WHERE tenant_id=$1 AND visto_em < now()-interval '2 days'",row['id'])

    async def agendador(self):
        if self.fila_duravel: return
        proxima_limpeza=0
        while True:
            try:
                if time.monotonic()>=proxima_limpeza:
                    await self.limpeza();proxima_limpeza=time.monotonic()+21600
                from nucleo.webhooks import despachar
                if self.saida_task is None or self.saida_task.done():
                    if self.saida_task and not self.saida_task.cancelled() and self.saida_task.exception(): log.warning('Entrega externa temporariamente indisponível')
                    self.saida_task=asyncio.create_task(despachar(self.db,self.http,self.cofre))
                for chave in await self.redis.zrangebyscore(self.prefixo+':due',0,time.time(),start=0,num=100):
                    if chave not in self.tarefas:
                        tenant,conversa=json.loads(chave)
                        conv=await self.repo.obter(tenant,'conversas',conversa)
                        if not conv:
                            await self.redis.zrem(self.prefixo+':due',chave)
                            continue
                        cfg=await self.cfg.efetiva(tenant,conv['agente_id'])
                        if sum(t==tenant for t,a in self.ativos.values())>=cfg['max_lotes_simultaneos']: continue
                        if sum(t==tenant and a==cfg['id'] for t,a in self.ativos.values())>=cfg['max_concorrencia']: continue
                        self.ativos[chave]=(tenant,cfg['id'])
                        tarefa=asyncio.create_task(self.processar(tenant,conversa))
                        self.tarefas[chave]=tarefa
                for chave,tarefa in list(self.tarefas.items()):
                    if tarefa.done():
                        if not tarefa.cancelled() and tarefa.exception(): log.warning('Falha interna no processamento')
                        del self.tarefas[chave]
                        self.ativos.pop(chave,None)
            except Exception: log.warning('Agendador temporariamente indisponível')
            await asyncio.sleep(.25)

    async def fechar(self,espera_s=None):
        """Parada graciosa: espera os lotes em andamento terminarem (até espera_s) antes de cancelar.
        Evita cortar um lote no meio na troca para a fila durável ou num reinício."""
        if self.saida_task:
            self.saida_task.cancel()
            await asyncio.gather(self.saida_task,return_exceptions=True)
        if espera_s is None: espera_s=float(os.getenv('PARADA_GRACIOSA_S','75') or 0)
        pendentes=[t for t in self.tarefas.values() if not t.done()]
        if pendentes and espera_s>0:
            await asyncio.wait(pendentes,timeout=espera_s)
        for tarefa in self.tarefas.values(): tarefa.cancel()
        await asyncio.gather(*self.tarefas.values(),return_exceptions=True)
