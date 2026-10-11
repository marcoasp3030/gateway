"""Recepção com tenant/canal e debounce; um agendador até a Fase 0.6."""
import asyncio
from functools import partial
from agentes.controle import Controle, CredencialRecusada, CircuitoAberto
from agentes.erros import TempoEsgotado, SilencioSemMotivo
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime,timezone,timedelta
from fastapi import HTTPException
from agentes import skyone
from canais import uazapi
from nucleo import botoes
from nucleo.contatos import nome_de_perfil,gravar_informado
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from admin.servico import auditar

log=logging.getLogger('gateway.pipeline')


def motivo_fallback(falha):
    """Motivo de "aguardando atendente" para uma falha do agente."""
    if isinstance(falha,CircuitoAberto): return 'circuito_aberto'
    if isinstance(falha,TempoEsgotado): return 'tempo_esgotado'
    return 'erro_agente'


def campos_do_retorno(resultado):
    """(acao, motivo_silencio, desfecho) do Retorno do agente para gravar no turno."""
    if not resultado: return None,None,None
    acao=resultado.get('acao')
    return acao,(resultado.get('motivo') if acao=='silencio' else None),resultado.get('desfecho')


# Mensagem do atendente pelo celular que devolve a conversa ao bot na hora (não vai para o agente).
COMANDO_BOT='#bot'
# SEG07 por contato: tempo sem chamar o agente depois de estourar o limite por minuto.
BLOQUEIO_CONTATO_MIN=5


async def encerrar_conversa(conn,tenant,conversa,desfecho):
    """Marca a conversa como encerrada (primeira marcação vale). Mensagem nova do cliente reabre."""
    return bool(await conn.fetchval("""UPDATE conversas SET encerrada_em=now(),desfecho=$3,atualizado_em=now()
        WHERE tenant_id=$1 AND conversation_id=$2 AND encerrada_em IS NULL RETURNING true""",tenant,conversa,desfecho))


# Endereço do painel citado no alerta de transbordo (vazio = não cita).
PAINEL_URL=os.getenv('PAINEL_URL','https://painel.luxbrain.com.br').strip() or None
FIM_DE_FRASE=('?','.','!','…')
# Segundos de "digitando" pedidos ao canal por vez; o canal cancela sozinho ao enviar.
DIGITANDO_S=25
# Refazer o turno (UX2/UX3.1): no máximo dois por turno; o segundo só enquanto o turno original começou há até 30 s.
MAX_REFAZER=2
TETO_REFAZER_S=30


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


async def marcar_aguardando(conn,tenant,conversa,motivo,detalhe=None):
    """Marca a conversa como aguardando atendente; evento e alerta de transbordo só na primeira marcação."""
    novo=await conn.fetchval("""UPDATE conversas SET aguardando_atendente_desde=now(),aguardando_motivo=$3
        WHERE tenant_id=$1 AND conversation_id=$2 AND aguardando_atendente_desde IS NULL RETURNING true""",
        tenant,conversa,motivo)
    if novo:
        await auditar(conn,tenant,'sistema','conversa.aguardando_atendente',conversa)
        # Alerta de transbordo (Fase 4): aviso curto ao número da empresa, se houver um cadastrado.
        from modulos.pedidos.avisos import avisar_transbordo
        await avisar_transbordo(conn,tenant,conversa,motivo,detalhe,PAINEL_URL)
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
        # WhatsApp de pedidos de uma unidade e número de alerta nunca são clientes (Fase 4): a mensagem
        # não vira conversa nem turno; se for "OK <numero>" / "NÃO <numero> motivo", decide o pedido.
        from modulos.pedidos import servico as pedidos_servico
        unidades=await pedidos_servico.numero_interno(self.db,tenant,identificador)
        if unidades is not None:
            if m.get('tipo') not in (None,'texto'): return {'ok':True,'ignorado':True,'interno':True}
            from modulos.pedidos.rotas import mensagem_de_numero_interno
            async with self.db.acquire() as conn,conn.transaction():
                return await mensagem_de_numero_interno(conn,tenant,identificador,unidades,m['texto'],m.get('message_id'))
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

    async def enviar_transacional(self,tenant,conversa,texto,aviso):
        """Aviso de status do pedido ao cliente (PED1): não é resposta do bot, então a pausa por atendente não o segura
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
            meta['aviso_pedido']=aviso
            # Entra no histórico como "sistema": o agente vê o que o cliente já recebeu; com o id do provedor, a varredura não o confunde com atendente.
            await conn.execute("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,message_id,meta)
                VALUES ($1,$2,'saida','sistema',$3,now(),$4,$5::jsonb) ON CONFLICT(tenant_id,message_id) DO NOTHING""",tenant,conversa,texto,marca[0],json.dumps(meta,ensure_ascii=False))
            await conn.execute('UPDATE conversas SET atualizado_em=now() WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
            await auditar(conn,tenant,'sistema','pedido.aviso_enviado' if real else 'pedido.aviso_simulado',conversa)
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
                    if origem=='cliente': await self._expirar_carrinho(tenant,conversa,cfg)
                    payload=await skyone.pacote(self.repo,tenant,conversa,min(ids),'\n'.join(i['conteudo'] for i in itens),cfg,rid,lote=itens)
                    if origem=='sistema':
                        payload.update(tarefa='notificar',evento=json.loads(itens[0]['evento']) if isinstance(itens[0]['evento'],str) else itens[0]['evento'])
                    tarefa=asyncio.create_task(self.consultar(tenant,cfg,payload))
                    try:
                        feito,_=await asyncio.wait({tarefa},timeout=cfg['slow_notice_s'])
                        if not feito and cfg['aviso_demora'] and not parece_fechamento(itens):
                            aviso=await self.enviar(tenant,conversa,cfg['id'],cfg['aviso_demora'])
                            entregue=aviso
                        resultado=await tarefa
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
                await conn.execute('UPDATE turnos SET origem=$3,uso=$4::jsonb,acao=$5,motivo_silencio=$6,desfecho=$7 WHERE tenant_id=$1 AND request_id=$2',tenant,uuid.UUID(rid),origem,json.dumps(resultado.get('uso')) if resultado and resultado.get('uso') else None,*campos_do_retorno(resultado))
                if not motivo and status!='ignorado' and resultado and resultado.get('acao')=='transferir_humano':
                    motivo='silencio_sem_motivo' if resultado.get('silencio_sem_motivo') else 'transferido_pelo_agente'
                # Ordem de travas: consumo antes de auditoria em todos os turnos.
                from nucleo.consumo import registrar,atualizar_cache
                mes,total=await registrar(conn,tenant)
                if motivo: await marcar_aguardando(conn,tenant,conversa,motivo)
                if resultado and resultado.get('nome_cliente'): await gravar_informado(conn,tenant,conversa,resultado['nome_cliente'])
                if origem=='sistema' and status=='ignorado':
                    await auditar(conn,tenant,'sistema','notificacao.nao_enviada',conversa)
                if status=='ok' and resultado and resultado.get('acao')=='encerrar':
                    await encerrar_conversa(conn,tenant,conversa,resultado.get('desfecho'))
                if status=='ok' and resultado and resultado.get('acao') in ('transferir_humano','encerrar'):
                    from nucleo.webhooks import emitir
                    await emitir(conn,tenant,'conversa.transferida' if resultado['acao']=='transferir_humano' else 'conversa.encerrada',{'conversation_id':conversa,'agente_id':cfg['id']})
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

    async def _expirar_carrinho(self,tenant,conversa,cfg):
        """Antes de montar o pacote: carrinho parado há mais de carrinho_expira_h é arquivado (UX3).
        Falha aqui não pode impedir a resposta ao cliente."""
        try:
            from modulos.pedidos import servico as pedidos
            async with self.db.acquire() as conn,conn.transaction():
                if await pedidos.expirar(conn,tenant,conversa,cfg.get('carrinho_expira_h')):
                    await auditar(conn,tenant,'sistema','carrinho.expirado',conversa)
        except Exception:
            log.warning('Carrinho não conferido para expiração; segue o turno')

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
        from modulos.pedidos import servico as pedidos
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
        elif resultado.get('acao') in ('transferir_humano','encerrar','pedido_fechado'): motivo='acao_com_efeito'
        elif await self.db.fetchval("""SELECT EXISTS(SELECT 1 FROM skill_chamadas WHERE tenant_id=$1 AND conversation_id=$2 AND criado_em>=$3
                AND resultado='ok' AND skill=ANY($4::text[]))""",tenant,conversa,turno['agente_inicio'],list(pedidos.SKILLS_COM_EFEITO)): motivo='skill_com_efeito'
        if motivo:
            # A resposta segue e a mensagem nova vira o próximo turno.
            await self.db.execute('UPDATE turnos SET refazer_recusado=$3 WHERE tenant_id=$1 AND id=$2',tenant,turno['id'],motivo)
            return False
        return True

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

    async def _responder(self,tenant,conversa,conv,cfg):
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
        if origem=='cliente': await self._expirar_carrinho(tenant,conversa,cfg)
        if descartado:
            from modulos.pedidos import servico as pedidos
            acoes=await pedidos.acoes_do_turno(self.db,tenant,conversa,descartado['agente_inicio'],descartado['agente_fim'] or datetime.now(timezone.utc))
        if not await self.permitido(tenant,conversa,cfg['id']):
            status='ignorado'
        else:
            try:
                payload=await skyone.pacote(self.repo,tenant,conversa,min(ids),'\n'.join(i['conteudo'] for i in itens),cfg,str(rid),lote=itens,acoes=acoes)
                if origem=='sistema':
                    payload.update(tarefa='notificar',evento=json.loads(itens[0]['evento']) if isinstance(itens[0]['evento'],str) else itens[0]['evento'])
                tarefa=asyncio.create_task(self.consultar(tenant,cfg,payload))
                presenca=asyncio.create_task(self.digitando(tenant,conv,cfg))
                try:
                    feito,_=await asyncio.wait({tarefa},timeout=cfg['slow_notice_s'])
                    # Lote que parece só fechamento não recebe aviso: se a resposta for silêncio, nada é enviado.
                    # Aviso de demora não sai em turno refeito nem se o bot falou há pouco nesta conversa (UX3).
                    if not feito and cfg['aviso_demora'] and not parece_fechamento(itens) and not descartado and not await self._bot_falou_ha_pouco(tenant,conversa,cfg):
                        # Aviso de demora é a parte 0 do turno: nunca sai duas vezes no mesmo turno.
                        aviso=await self._enviar_parte(tenant,conversa,cfg,rid,0,cfg['aviso_demora'])=='enviada'
                        # Enviar mensagem cancela o "digitando" no canal: pede de novo.
                        if aviso: await self.digitando(tenant,conv,cfg)
                    resultado=await tarefa
                    await asyncio.gather(presenca,return_exceptions=True)
                    # Nada vai ser enviado (silêncio ou transferência sem texto): tira o "digitando".
                    if not resultado.get('resposta'): await self.digitando(tenant,conv,cfg,'paused')
                finally:
                    if not tarefa.done():
                        tarefa.cancel()
                        await asyncio.gather(tarefa,return_exceptions=True)
            except (CredencialRecusada,CircuitoAberto) as falha:
                erro='SKYONE_CREDENTIAL_REJECTED' if isinstance(falha,CredencialRecusada) else 'SKYONE_CIRCUIT_OPEN'
                status='erro';resultado={'resposta':[cfg['fallback']],'acao':'nenhuma'}
            except Exception as falha:
                erro='AGENT_TIMEOUT' if isinstance(falha,TempoEsgotado) else 'AGENT_ERROR'
                status='erro';resultado={'resposta':[cfg['fallback']],'acao':'nenhuma'}
            finally:
                # Espera o pedido de "digitando" terminar (é curto e tem limite próprio) em vez de cortá-lo no meio.
                if presenca: await asyncio.gather(presenca,return_exceptions=True)
        # A resposta fica gravada antes de qualquer envio: se o worker cair, só falta enviar.
        if resultado and resultado.get('silencio_sem_motivo'): status='erro';erro='SILENCE_WITHOUT_REASON'
        return await self.db.fetchrow('''UPDATE turnos SET etapa='respondido',status=$3,erro=$4,resposta=$5::jsonb,
            agente_fim=now(),aviso_demora=$6,uso=$7::jsonb,acao=$8,motivo_silencio=$9,desfecho=$10 WHERE tenant_id=$1 AND id=$2 RETURNING *''',
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
        if efetiva['send_mode']=='live':
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
        try:
            ultima=len(resultado['resposta'])
            for i,texto in enumerate(resultado['resposta'],start=1):
                # Os botões pertencem ao último balão.
                r=await self._enviar_parte(tenant,conversa,cfg,turno['request_id'],i,texto,resultado.get('botoes') if i==ultima else None)
                if r=='ignorada': status='ignorado';break
                if r=='enviada': entregue=True
            if resultado.get('acao')=='transferir_humano':
                await self.db.execute('UPDATE conversas SET bot_pausado_ate=now()+make_interval(mins=>$3) WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,cfg['pause_minutes'])
        except Exception:
            status='erro'
            await self.db.execute("UPDATE turnos SET erro='SEND_ERROR' WHERE tenant_id=$1 AND id=$2",tenant,turno['id'])
        return status,entregue

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
            if motivo: await marcar_aguardando(conn,tenant,conversa,motivo,resultado.get('motivo') if resultado and motivo=='transferido_pelo_agente' else None)
            if resultado and resultado.get('nome_cliente'): await gravar_informado(conn,tenant,conversa,resultado['nome_cliente'])
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
            await conn.execute('UPDATE mensagens SET processada_em=now() WHERE tenant_id=$1 AND conversation_id=$2 AND id=ANY($3::bigint[])',tenant,conversa,turno['mensagem_ids'])
            await auditar(conn,tenant,'sistema','turno.'+status,conversa)
        await atualizar_cache(self.redis,self.prefixo,tenant,mes,total)

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
                    AND (m.origem='humano' OR (m.origem='cliente' AND m.tipo IN ('texto','localizacao'))
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
                if await encerrar_conversa(conn,c['tenant_id'],c['conversation_id'],'vazia'):
                    await auditar(conn,c['tenant_id'],'sistema','conversa.encerrada',c['conversation_id']);encerradas+=1
        return encerradas

    async def limpeza(self):
        # Retenção de webhook por tenant; sem logs de conteúdo ou telefones.
        for row in await self.db.fetch('SELECT id,retencao_dias FROM tenants'):
            await self.db.execute('DELETE FROM webhook_raw WHERE tenant_id=$1 AND recebido_em < now()-make_interval(days=>$2)',row['id'],row['retencao_dias'])
            await self.db.execute('DELETE FROM skill_chamadas WHERE tenant_id=$1 AND criado_em < now()-make_interval(days=>$2)',row['id'],row['retencao_dias'])

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
