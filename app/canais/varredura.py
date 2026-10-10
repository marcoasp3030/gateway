"""Varredura de reconciliação do uazapi (Fase 1).

O webhook é o caminho normal. Como o provedor não reenvia o que falhou, o agendador
confere a cada intervalo as mensagens recentes de cada instância e recebe as que o
webhook não entregou, pelo mesmo caminho de recepção (mesma deduplicação por messageid).
Também confere o estado da conexão da instância.
"""
import json
import logging
import time
from datetime import datetime,timezone

from admin.servico import auditar
from canais import uazapi

log=logging.getLogger('gateway.varredura')


async def registrar_estado(db,tenant,canal_id,novo):
    """Grava status_conexao e alerta nas mudanças (auditoria da empresa + log)."""
    async with db.acquire() as conn,conn.transaction():
        atual=await conn.fetchval('SELECT status_conexao FROM tenant_canais WHERE tenant_id=$1 AND id=$2 FOR UPDATE',tenant,canal_id)
        if atual==novo: return False
        await conn.execute('UPDATE tenant_canais SET status_conexao=$3 WHERE tenant_id=$1 AND id=$2',tenant,canal_id,novo)
        if novo=='desconectado':
            await auditar(conn,tenant,'sistema','canal.desconectado',canal_id)
        elif novo=='conectado' and atual in ('desconectado','conectando'):
            await auditar(conn,tenant,'sistema','canal.reconectado',canal_id)
    if novo=='desconectado': log.warning('Canal desconectado do provedor')
    return True


class Varredura:
    def __init__(self,pipeline,janela_s=600,carencia_s=20,pagina=100,max_paginas=20):
        self.p=pipeline;self.db=pipeline.db;self.redis=pipeline.redis
        self.janela_s=janela_s
        # Dá tempo de o webhook chegar antes de considerar a mensagem perdida.
        self.carencia_s=carencia_s
        self.pagina=pagina;self.max_paginas=max_paginas

    @property
    def k_ultima(self): return f'{self.p.prefixo}:varredura:ultima'
    @property
    def k_total(self): return f'{self.p.prefixo}:varredura:recuperadas'

    async def rodar(self):
        canais=await self.db.fetch("""SELECT k.* FROM tenant_canais k JOIN tenants t ON t.id=k.tenant_id
            WHERE k.tipo='uazapi' AND k.ativo AND t.status='ativo' AND k.credenciais_enc ? 'fernet' ORDER BY k.tenant_id,k.id""")
        resumo={'em':datetime.now(timezone.utc).isoformat(timespec='seconds'),'canais':len(canais),'recuperadas':0,'sem_telefone':0,'do_atendente':0,'ambiguas':0,'erros':0}
        for canal in canais:
            canal=dict(canal)
            if isinstance(canal['credenciais_enc'],str): canal['credenciais_enc']=json.loads(canal['credenciais_enc'])
            try:
                r=await self.canal(canal)
                for k in ('recuperadas','sem_telefone','do_atendente','ambiguas'): resumo[k]+=r[k]
            except Exception as erro:
                # Sem detalhes: mensagens do cliente HTTP podem conter o endereço da instância.
                resumo['erros']+=1;log.warning('Varredura do canal falhou (%s)',type(erro).__name__)
        try: await self.redis.set(self.k_ultima,json.dumps(resumo),ex=86400)
        except Exception: pass
        return resumo

    async def canal(self,canal):
        tenant,canal_id=canal['tenant_id'],canal['id']
        await registrar_estado(self.db,tenant,canal_id,await uazapi.estado(self.p.http,self.p.cofre,tenant,canal))
        agora_ms=time.time()*1000
        mensagens,completo=await uazapi.recentes(self.p.http,self.p.cofre,tenant,canal,agora_ms-self.janela_s*1000,self.pagina,self.max_paginas)
        if not completo: log.warning('Varredura não alcançou o início da janela (volume alto)')
        candidatas={}
        for msg in mensagens:
            if msg.get('isGroup') or str(msg.get('chatid') or '').endswith('@g.us'): continue
            # Registro de ligação aparece na busca como mensagem "minha"; não é conversa.
            if msg.get('messageType')=='call' or msg.get('source')=='call_log': continue
            ident=str(msg.get('messageid') or '')
            try: quando=float(msg.get('messageTimestamp') or 0)
            except (TypeError,ValueError): continue
            if quando<1e11: quando*=1000
            if ident and quando<=agora_ms-self.carencia_s*1000: candidatas[ident]=(quando,msg)
        resultado={'recuperadas':0,'sem_telefone':0,'do_atendente':0,'ambiguas':0}
        if not candidatas: return resultado
        chaves=[f'{canal_id}:{i}'[:len(canal_id)+201] for i in candidatas]
        ja={r[0] for r in await self.db.fetch('SELECT message_id FROM mensagens WHERE tenant_id=$1 AND message_id=ANY($2::text[])',tenant,chaves)}
        faltando=sorted(((q,m) for i,(q,m) in candidatas.items() if f'{canal_id}:{i}' not in ja),key=lambda par: par[0])
        from canais.rotas import Entrada
        for quando,msg in faltando:
            norm=uazapi.normalizar({'EventType':'messages','message':msg})
            if norm is None:
                resultado['sem_telefone']+=1;continue
            try: m=Entrada.model_validate({'identificador':norm['telefone'],'texto':norm['texto'],
                    'message_id':norm['message_id'],'enviada_em':norm['enviada_em']}).model_dump()
            except ValueError:
                resultado['sem_telefone']+=1;continue
            if norm['from_me'] and await self.envio_sem_id(tenant,f'{tenant}:{canal_id}:{norm["telefone"]}',quando/1000):
                # Pode ser do bot: não gravar como atendente (pausaria a conversa por engano).
                resultado['ambiguas']+=1;continue
            # A busca não traz wasSentByApi. Mensagem "minha" que chegou até aqui não tem o id de
            # nenhum envio do gateway (bot ou painel): foi o atendente, pelo celular (INV03).
            m.update(humano=norm['from_me'],via_api=False,nome=norm['nome'],tipo=norm['tipo'],meta=norm['meta'],origem_recebimento='varredura')
            r=await self.p.receber(tenant,canal_id,m)
            if r.get('mensagem_id') or (norm['from_me'] and r.get('from_me')):
                resultado['recuperadas']+=1;resultado['do_atendente']+=int(norm['from_me'])
        if resultado['recuperadas']:
            log.warning('Varredura recuperou %s mensagem(ns) que o webhook não entregou',resultado['recuperadas'])
            async with self.db.acquire() as conn,conn.transaction():
                await auditar(conn,tenant,'sistema','canal.varredura_recuperou',canal_id)
            # Contador é só indicador: Redis fora não pode desfazer a recuperação.
            try: await self.redis.incrby(self.k_total,resultado['recuperadas'])
            except Exception: pass
        if resultado['ambiguas']:
            log.warning('Varredura achou %s mensagem(ns) enviada(s) pela instância sem origem certa; não gravadas',resultado['ambiguas'])
        if resultado['sem_telefone']:
            log.warning('Varredura achou %s mensagem(ns) sem telefone identificável; não recuperadas',resultado['sem_telefone'])
        return resultado

    async def envio_sem_id(self,tenant,conversa,quando_s,folga_s=180):
        """Há envio do gateway nessa conversa, perto desse instante, sem o id do provedor
        (resposta sem id, envio em andamento ou entrega incerta)?"""
        return await self.db.fetchval("""SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2
            AND direcao='saida' AND criado_em BETWEEN to_timestamp($3)-make_interval(secs=>$4) AND to_timestamp($3)+make_interval(secs=>$4)
            AND (entrega IN ('enviando','incerta') OR meta->>'envio_sem_id'='true'))""",tenant,conversa,float(quando_s),float(folga_s))

    async def indicador(self):
        try:
            ultima=await self.redis.get(self.k_ultima)
            return {**(json.loads(ultima) if ultima else {}),'recuperadas_total':int(await self.redis.get(self.k_total) or 0)}
        except Exception:
            return {}
