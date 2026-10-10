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


FIM_DE_FRASE=('?','.','!','…')
# Segundos de "digitando" pedidos ao canal por vez; o canal cancela sozinho ao enviar.
DIGITANDO_S=25


def debounce_da_mensagem(cfg,m):
    """Espera antes de chamar o agente. Texto que parece completo (termina com pontuação de
    fim de frase ou tem mais de debounce_palavras palavras) espera menos; mensagem curta ou
    picada, mídia, localização e reação esperam o debounce normal."""
    normal=cfg['debounce_s']
    if (m.get('tipo') or 'texto')!='texto': return normal
    texto=str(m.get('texto') or '').strip()
    # Emoji e fechamentos depois da pontuação não contam ("tudo bem? 🙂").
    sem_enfeite=texto.rstrip(' \t\n)"\'*_~'+''.join(c for c in texto[-6:] if ord(c)>0x2000 and c not in FIM_DE_FRASE))
    completa=sem_enfeite.endswith(FIM_DE_FRASE) or len(texto.split())>cfg['debounce_palavras']
    return min(normal,cfg['debounce_curto_s']) if completa else normal


async def marcar_aguardando(conn,tenant,conversa,motivo):
    """Marca a conversa como aguardando atendente; evento só na primeira marcação."""
    novo=await conn.fetchval("""UPDATE conversas SET aguardando_atendente_desde=now(),aguardando_motivo=$3
        WHERE tenant_id=$1 AND conversation_id=$2 AND aguardando_atendente_desde IS NULL RETURNING true""",
        tenant,conversa,motivo)
    if novo: await auditar(conn,tenant,'sistema','conversa.aguardando_atendente',conversa)
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
            reacao=m.get('tipo')=='reacao'
            msgid=await conn.fetchval('''INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,message_id,enviada_em,tipo,meta,processada_em,origem_recebimento)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,CASE WHEN $10 THEN now() END,$11) ON CONFLICT(tenant_id,message_id) DO NOTHING RETURNING id''',
                tenant,conversa,'saida' if humano else 'entrada','humano' if humano else 'cliente',m['texto'],
                mensagem_id,m.get('enviada_em') or datetime.now(timezone.utc),m.get('tipo') or 'texto',
                json.dumps(m['meta'],ensure_ascii=False) if m.get('meta') else None,reacao,m.get('origem_recebimento'))
            if msgid:
                await auditar(conn,tenant,'sistema','mensagem.humana' if humano else 'mensagem.recebida',conversa)
            # Reação fica no histórico, mas não abre turno nem pausa o bot (um 👍 do atendente não é assumir a conversa).
            if reacao: return {'ok':True,'reacao':True,'duplicada':msgid is None,'conversation_id':conversa,'mensagem_id':msgid}
            if humano and msgid:
                await conn.execute('UPDATE conversas SET bot_pausado_ate=now()+make_interval(mins=>$3) '
                    'WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,cfg['pause_minutes'])
            if humano: return {'ok':True,'from_me':True}
            if msgid and cfg['modo_piloto'] and identificador not in cfg['contatos_permitidos']:
                await conn.execute('UPDATE mensagens SET processada_em=now() WHERE tenant_id=$1 AND id=$2',tenant,msgid)
                return {'ok':True,'ignorado':True,'conversation_id':conversa,'mensagem_id':msgid}
        # Em retry de webhook duplicado, o agendamento é reparado se a primeira chamada falhou no Redis.
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

    async def enviar(self,tenant,conversa,agente_id,texto):
        # A mesma trava de assumir/devolver serializa a pausa e o envio final.
        async with self.db.acquire() as conn,conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',conversa)
            repo=Repositorio(conn);configuracoes=Configuracoes(repo)
            if not await self.permitido(tenant,conversa,agente_id,repo,configuracoes): return False
            conv=await repo.obter(tenant,'conversas',conversa)
            canal,empresa,cfg=await self.contexto(tenant,conv['canal_id'],repo,configuracoes)
            marca=(None,None)
            if cfg['send_mode']=='live':
                if canal['tipo']!='uazapi': raise ValueError('Envio live deste canal ainda não implementado')
                marca=uazapi.marca_do_envio(canal['id'],await uazapi.enviar(self.http,self.cofre,tenant,canal,conv['telefone'],texto))
            await conn.execute("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,message_id,meta)
                VALUES ($1,$2,'saida','bot',$3,now(),$4,$5::jsonb) ON CONFLICT(tenant_id,message_id) DO NOTHING""",tenant,conversa,texto,*marca)
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
            itens=await self.db.fetch("SELECT id,conteudo,criado_em,origem,evento,enviada_em,tipo FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema') ORDER BY id LIMIT 100",tenant,conversa)
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
                    payload=await skyone.pacote(self.repo,tenant,conversa,min(ids),'\n'.join(i['conteudo'] for i in itens),cfg,rid,lote=itens)
                    if origem=='sistema':
                        payload.update(tarefa='notificar',evento=json.loads(itens[0]['evento']) if isinstance(itens[0]['evento'],str) else itens[0]['evento'])
                    tarefa=asyncio.create_task(self.consultar(tenant,cfg,payload))
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
                await conn.execute('UPDATE turnos SET origem=$3,uso=$4::jsonb,acao=$5,motivo_silencio=$6,desfecho=$7 WHERE tenant_id=$1 AND request_id=$2',tenant,uuid.UUID(rid),origem,json.dumps(resultado.get('uso')) if resultado and resultado.get('uso') else None,*campos_do_retorno(resultado))
                if not motivo and status!='ignorado' and resultado and resultado.get('acao')=='transferir_humano':
                    motivo='silencio_sem_motivo' if resultado.get('silencio_sem_motivo') else 'transferido_pelo_agente'
                # Ordem de travas: consumo antes de auditoria em todos os turnos.
                from nucleo.consumo import registrar,atualizar_cache
                mes,total=await registrar(conn,tenant)
                if motivo: await marcar_aguardando(conn,tenant,conversa,motivo)
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
        itens=await conn.fetch("SELECT id,conteudo,criado_em,origem,evento,enviada_em,tipo FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema') ORDER BY id LIMIT 100",tenant,conversa)
        if not itens: return []
        # Não misturar eventos de sistema com mensagens do cliente nem entre si.
        if itens[0]['origem']=='sistema': return itens[:1]
        corte=next((i for i,m in enumerate(itens) if m['origem']=='sistema'),len(itens))
        return itens[:corte]

    async def processar_duravel(self,tenant,conversa):
        """Etapas gravadas antes de cada efeito: turno aberto → resposta → partes → conclusão.

        Retorna 'concluido', 'vazio' ou 'ocupado' (outra execução tem a trava da conversa
        e reagenda o que sobrar ao terminar)."""
        conv=await self.repo.obter(tenant,'conversas',conversa)
        if not conv: return 'vazio'
        cfg=await self.cfg.efetiva(tenant,conv['agente_id'])
        lock=self._trava(tenant,conversa);token=uuid.uuid4().hex
        if not await self.redis.set(lock,token,nx=True,ex=cfg['timeout_s']+90): return 'ocupado'
        try:
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
            await self._concluir(tenant,conversa,cfg,turno,await self._enviar_partes(tenant,conversa,cfg,turno))
            return 'concluido'
        finally:
            await insistir(lambda: self.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,lock,token),self.insistir_s)
            if await self.db.fetchval("SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema'))",tenant,conversa):
                await insistir(lambda: self.enfileirar(tenant,conversa,cfg),self.insistir_s)

    async def _responder(self,tenant,conversa,conv,cfg):
        itens=await self._pendentes(self.db,tenant,conversa)
        if not itens: return None
        origem=itens[0]['origem'];ids=[i['id'] for i in itens];rid=uuid.uuid4()
        turno=await self.db.fetchrow('''INSERT INTO turnos(tenant_id,conversation_id,agente_id,canal_id,request_id,n_mensagens,
            primeira_msg_em,ultima_msg_em,agente_inicio,modo_agente,status,origem,etapa,mensagem_ids)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,now(),$9,'em_andamento',$10,'processando',$11) RETURNING *''',
            tenant,conversa,cfg['id'],conv['canal_id'],rid,len(itens),itens[0]['criado_em'],itens[-1]['criado_em'],
            cfg['agente_mode'],origem,ids)
        resultado=None;erro=None;status='ok';aviso=False;presenca=None
        if not await self.permitido(tenant,conversa,cfg['id']):
            status='ignorado'
        else:
            try:
                payload=await skyone.pacote(self.repo,tenant,conversa,min(ids),'\n'.join(i['conteudo'] for i in itens),cfg,str(rid),lote=itens)
                if origem=='sistema':
                    payload.update(tarefa='notificar',evento=json.loads(itens[0]['evento']) if isinstance(itens[0]['evento'],str) else itens[0]['evento'])
                tarefa=asyncio.create_task(self.consultar(tenant,cfg,payload))
                presenca=asyncio.create_task(self.digitando(tenant,conv,cfg))
                try:
                    feito,_=await asyncio.wait({tarefa},timeout=cfg['slow_notice_s'])
                    if not feito and cfg['aviso_demora']:
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
                if presenca and not presenca.done(): presenca.cancel()
        # A resposta fica gravada antes de qualquer envio: se o worker cair, só falta enviar.
        if resultado and resultado.get('silencio_sem_motivo'): status='erro';erro='SILENCE_WITHOUT_REASON'
        return await self.db.fetchrow('''UPDATE turnos SET etapa='respondido',status=$3,erro=$4,resposta=$5::jsonb,
            agente_fim=now(),aviso_demora=$6,uso=$7::jsonb,acao=$8,motivo_silencio=$9,desfecho=$10 WHERE tenant_id=$1 AND id=$2 RETURNING *''',
            tenant,turno['id'],status,erro,json.dumps(resultado) if resultado else None,aviso,
            json.dumps(resultado.get('uso')) if resultado and resultado.get('uso') else None,*campos_do_retorno(resultado))

    async def _enviar_parte(self,tenant,conversa,cfg,rid,parte,texto):
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
                await conn.execute('''INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,turno_request_id,parte,entrega)
                    VALUES ($1,$2,'saida','bot',$3,now(),$4,$5,'enviando')''',tenant,conversa,texto,rid,parte)
                marca=(None,None)
                try:
                    if efetiva['send_mode']=='live':
                        if canal['tipo']!='uazapi': raise ValueError('Envio live deste canal ainda não implementado')
                        marca=uazapi.marca_do_envio(canal['id'],await uazapi.enviar(self.http,self.cofre,tenant,canal,conv['telefone'],texto))
                except Exception:
                    # Não se sabe se o canal entregou: não reenviar, revisão humana.
                    await self._incerta(conn,tenant,conversa,rid,parte);raise
                async with conn.transaction():
                    await conn.execute("UPDATE mensagens SET entrega='enviada',enviada_em=now(),message_id=$4,meta=$5::jsonb WHERE tenant_id=$1 AND turno_request_id=$2 AND parte=$3",tenant,rid,parte,*marca)
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
            for i,texto in enumerate(resultado['resposta'],start=1):
                r=await self._enviar_parte(tenant,conversa,cfg,turno['request_id'],i,texto)
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
            if motivo: await marcar_aguardando(conn,tenant,conversa,motivo)
            if turno['origem']=='sistema' and status=='ignorado':
                await auditar(conn,tenant,'sistema','notificacao.nao_enviada',conversa)
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

    async def limpeza(self):
        # Retenção de webhook por tenant; sem logs de conteúdo ou telefones.
        for row in await self.db.fetch('SELECT id,retencao_dias FROM tenants'):
            await self.db.execute('DELETE FROM webhook_raw WHERE tenant_id=$1 AND recebido_em < now()-make_interval(days=>$2)',row['id'],row['retencao_dias'])

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
