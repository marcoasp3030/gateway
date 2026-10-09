"""Recepção com tenant/canal e debounce; um agendador até a Fase 0.6."""
import asyncio
from functools import partial
from agentes.controle import Controle, CredencialRecusada, CircuitoAberto
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime,timezone,timedelta
from fastapi import HTTPException
from agentes import skyone
from canais import uazapi
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from admin.servico import auditar

log=logging.getLogger('gateway.pipeline')


def sanitizar(valor):
    if isinstance(valor,dict):
        return {k:('***' if k.lower() in ('s','key') or any(s in k.lower() for s in ('token','secret','password','authorization','api_key','credencial'))
                   else sanitizar(v)) for k,v in valor.items()}
    if isinstance(valor,list): return [sanitizar(v) for v in valor]
    return valor


class Pipeline:
    def __init__(self,db,redis,configuracoes,cofre,http,prefixo='gw05',skyone_rpm=240):
        self.db,self.redis,self.cfg,self.cofre,self.http=db,redis,configuracoes,cofre,http
        self.repo=Repositorio(db);self.prefixo=prefixo
        self.tarefas={};self.ativos={}
        self.controle=Controle(db,redis,skyone_rpm,prefixo)
        self.chamar=partial(skyone.chamar,controle=self.controle)
        self.saida_task=None

    def chave(self,tenant,conversa):
        return json.dumps([tenant,conversa],separators=(',',':'))

    async def enfileirar(self,tenant,conversa,cfg):
        chave=self.chave(tenant,conversa)
        # Uma transação Lua mantém first e vencimento coerentes sob concorrência.
        await self.redis.eval('''
local t=redis.call('TIME')
local agora=tonumber(t[1])+tonumber(t[2])/1000000
local primeira=tonumber(redis.call('GET',KEYS[1])) or agora
redis.call('SET',KEYS[1],primeira,'EX',86400)
redis.call('ZADD',KEYS[2],math.min(agora+tonumber(ARGV[2]),primeira+tonumber(ARGV[3])),ARGV[1])
return 1
''',2,self.prefixo+':first:'+chave,self.prefixo+':due',chave,cfg['debounce_s'],cfg['max_wait_s'])

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
        conversa=f'{tenant}:{canal_id}:{identificador}'
        humano=m.get('humano',False)
        mensagem_id=f'{canal_id}:{m["message_id"]}' if m.get('message_id') else None
        async with self.db.acquire() as conn,conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',conversa)
            contato=await conn.fetchval('INSERT INTO contatos(id,tenant_id,telefone,nome) VALUES ($1,$2,$3,$4) ON CONFLICT(tenant_id,telefone) DO UPDATE SET atualizado_em=now() RETURNING id','ct_'+uuid.uuid4().hex,tenant,identificador,m.get('nome'))
            anterior=await Repositorio(conn).obter(tenant,'conversas',conversa)
            if anterior and anterior['agente_id']!=cfg['id']:
                await auditar(conn,tenant,'sistema','conversa.roteada',conversa)
            await conn.execute('''INSERT INTO conversas(tenant_id,conversation_id,canal,canal_id,agente_id,telefone,nome)
                VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(conversation_id) DO UPDATE
                SET agente_id=EXCLUDED.agente_id,atualizado_em=now()
                WHERE conversas.tenant_id=EXCLUDED.tenant_id''',tenant,conversa,canal['tipo'],canal_id,cfg['id'],identificador,m.get('nome'))
            await conn.execute('UPDATE conversas SET contato_id=$3 WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,contato)
            msgid=await conn.fetchval('''INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,message_id,enviada_em)
                VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(tenant_id,message_id) DO NOTHING RETURNING id''',
                tenant,conversa,'saida' if humano else 'entrada','humano' if humano else 'cliente',m['texto'],
                mensagem_id,m.get('enviada_em') or datetime.now(timezone.utc))
            if msgid:
                await auditar(conn,tenant,'sistema','mensagem.humana' if humano else 'mensagem.recebida',conversa)
            if humano and msgid:
                await conn.execute('UPDATE conversas SET bot_pausado_ate=now()+make_interval(mins=>$3) '
                    'WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,cfg['pause_minutes'])
            if humano: return {'ok':True,'from_me':True}
            if msgid and cfg['modo_piloto'] and identificador not in cfg['contatos_permitidos']:
                await conn.execute('UPDATE mensagens SET processada_em=now() WHERE tenant_id=$1 AND id=$2',tenant,msgid)
                return {'ok':True,'ignorado':True,'conversation_id':conversa,'mensagem_id':msgid}
        # Em retry de webhook duplicado, o agendamento é reparado se a primeira chamada falhou no Redis.
        await self.enfileirar(tenant,conversa,cfg)
        return {'ok':True,'duplicada':msgid is None,'conversation_id':conversa,'mensagem_id':msgid}

    async def permitido(self,tenant,conversa,agente_id,repo=None,configuracoes=None):
        repo=repo or self.repo
        conv=await repo.obter(tenant,'conversas',conversa)
        if not conv or conv['agente_id']!=agente_id: return False
        canal,empresa,cfg=await self.contexto(tenant,conv['canal_id'],repo,configuracoes)
        return (canal['ativo'] and canal['agente_id']==agente_id and empresa['status']=='ativo' and cfg['status']=='ativo'
            and not (conv['bot_pausado_ate'] and conv['bot_pausado_ate']>datetime.now(timezone.utc))
            and (not cfg['modo_piloto'] or conv['telefone'] in cfg['contatos_permitidos']))

    async def enviar(self,tenant,conversa,agente_id,texto):
        # A mesma trava de assumir/devolver serializa a pausa e o envio final.
        async with self.db.acquire() as conn,conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',conversa)
            repo=Repositorio(conn);configuracoes=Configuracoes(repo)
            if not await self.permitido(tenant,conversa,agente_id,repo,configuracoes): return False
            conv=await repo.obter(tenant,'conversas',conversa)
            canal,empresa,cfg=await self.contexto(tenant,conv['canal_id'],repo,configuracoes)
            if cfg['send_mode']=='live':
                if canal['tipo']!='uazapi': raise ValueError('Envio live deste canal ainda não implementado')
                await uazapi.enviar(self.http,self.cofre,tenant,canal,conv['telefone'],texto)
            await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em) VALUES ($1,$2,'saida','bot',$3,now())",tenant,conversa,texto)
            await conn.execute('UPDATE conversas SET atualizado_em=now() WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
            await auditar(conn,tenant,'sistema','mensagem.enviada',conversa)
        return True

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
            itens=await self.db.fetch("SELECT id,conteudo,criado_em,origem,evento FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema') ORDER BY id LIMIT 100",tenant,conversa)
            if not itens: return
            # Não misturar eventos de sistema com mensagens do cliente nem entre si.
            origem=itens[0]['origem']
            if origem=='sistema': itens=itens[:1]
            else:
                corte=next((i for i,m in enumerate(itens) if m['origem']=='sistema'),len(itens))
                itens=itens[:corte]
            ids=[i['id'] for i in itens]
            inicio=datetime.now(timezone.utc);rid=str(uuid.uuid4())
            resultado=None;erro=None;status='ok';entregue=False;aviso=False;fim_agente=inicio
            if not await self.permitido(tenant,conversa,cfg['id']):
                status='ignorado'
            else:
                try:
                    payload=await skyone.pacote(self.repo,tenant,conversa,min(ids),'\n'.join(i['conteudo'] for i in itens),cfg,rid)
                    if origem=='sistema':
                        payload.update(tarefa='notificar',evento=json.loads(itens[0]['evento']) if isinstance(itens[0]['evento'],str) else itens[0]['evento'])
                    tarefa=asyncio.create_task(self.chamar(self.http,self.repo,self.cofre,tenant,cfg,payload))
                    try:
                        feito,_=await asyncio.wait({tarefa},timeout=cfg['slow_notice_s'])
                        if not feito and cfg['aviso_demora']:
                            aviso=await self.enviar(tenant,conversa,cfg['id'],cfg['aviso_demora'])
                            entregue=aviso
                        resultado=await tarefa
                    finally:
                        if not tarefa.done():
                            tarefa.cancel()
                            await asyncio.gather(tarefa,return_exceptions=True)
                except (CredencialRecusada,CircuitoAberto) as falha:
                    erro='SKYONE_CREDENTIAL_REJECTED' if isinstance(falha,CredencialRecusada) else 'SKYONE_CIRCUIT_OPEN'
                    status='erro'
                    resultado={'resposta':[cfg['fallback']],'acao':'nenhuma'}
                except Exception:
                    # O tipo e a mensagem de exceções de terceiros não são logados.
                    erro='AGENT_ERROR';status='erro'
                    resultado={'resposta':[cfg['fallback']],'acao':'nenhuma'}
                fim_agente=datetime.now(timezone.utc)
                try:
                    for texto in resultado['resposta']:
                        if not await self.enviar(tenant,conversa,cfg['id'],texto): status='ignorado';break
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
                await conn.execute('UPDATE turnos SET origem=$3,uso=$4::jsonb WHERE tenant_id=$1 AND request_id=$2',tenant,uuid.UUID(rid),origem,json.dumps(resultado.get('uso')) if resultado and resultado.get('uso') else None)
                # Ordem de travas: consumo antes de auditoria em todos os turnos.
                from nucleo.consumo import registrar,atualizar_cache
                mes,total=await registrar(conn,tenant)
                if origem=='sistema' and status=='ignorado':
                    await auditar(conn,tenant,'sistema','notificacao.nao_enviada',conversa)
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

    async def recuperar(self):
        # Enumeração de trabalho com tenant retornado explicitamente; cada processamento usa o escopo.
        rows=await self.db.fetch("SELECT DISTINCT tenant_id,conversation_id FROM mensagens WHERE processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema')")
        for row in rows:
            conv=await self.repo.obter(row['tenant_id'],'conversas',row['conversation_id'])
            cfg=await self.cfg.efetiva(row['tenant_id'],conv['agente_id'])
            await self.enfileirar(row['tenant_id'],row['conversation_id'],cfg)

    async def limpeza(self):
        # Retenção de webhook por tenant; sem logs de conteúdo ou telefones.
        for row in await self.db.fetch('SELECT id,retencao_dias FROM tenants'):
            await self.db.execute('DELETE FROM webhook_raw WHERE tenant_id=$1 AND recebido_em < now()-make_interval(days=>$2)',row['id'],row['retencao_dias'])

    async def agendador(self):
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

    async def fechar(self):
        if self.saida_task:
            self.saida_task.cancel()
            await asyncio.gather(self.saida_task,return_exceptions=True)
        for tarefa in self.tarefas.values(): tarefa.cancel()
        await asyncio.gather(*self.tarefas.values(),return_exceptions=True)
