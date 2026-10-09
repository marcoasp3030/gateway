"""Admissão compartilhada por URL e circuito persistente por agente."""
import asyncio
import hashlib
import logging
import uuid
from urllib.parse import urlsplit,urlunsplit
from fastapi import APIRouter,Depends,Request,HTTPException
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from admin.servico import auditar
from nucleo.webhooks import emitir

from .erros import CredencialRecusada, CircuitoAberto

LUA='''
local tm=redis.call('TIME')
local now=tonumber(tm[1])*1000+tonumber(tm[2])/1000
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now-tonumber(ARGV[2]))
if redis.call('ZCARD',KEYS[1])<tonumber(ARGV[1]) then
 redis.call('ZADD',KEYS[1],now,ARGV[3])
 redis.call('PEXPIRE',KEYS[1],ARGV[2])
 return 0
end
local first=redis.call('ZRANGE',KEYS[1],0,0,'WITHSCORES')
return math.max(1,math.ceil(tonumber(first[2])+tonumber(ARGV[2])-now))
'''

class Controle:
    def __init__(self,db,redis,limite=240,prefixo='gw05',janela_ms=60000):
        self.db,self.redis,self.limite,self.prefixo,self.janela_ms=db,redis,limite,prefixo,janela_ms

    def chave_url(self,url):
        u=urlsplit(url)
        normal=urlunsplit(('https',u.hostname.lower(),u.path or '/',u.query,''))
        return self.prefixo+':skyone:url:'+hashlib.sha256(normal.encode()).hexdigest()

    async def esperar(self,url):
        key=self.chave_url(url)
        while True:
            espera=await self.redis.eval(LUA,1,key,self.limite,self.janela_ms,uuid.uuid4().hex)
            if not espera:return
            await asyncio.sleep(espera/1000)

    async def iniciar(self,t,a,timeout):
        async with self.db.acquire() as c,c.transaction():
            await c.execute('INSERT INTO agente_circuitos(tenant_id,agente_id) VALUES ($1,$2) ON CONFLICT DO NOTHING',t,a)
            r=await c.fetchrow('SELECT *,now() AS agora FROM agente_circuitos WHERE tenant_id=$1 AND agente_id=$2 FOR UPDATE',t,a)
            if r['aberto_ate']:
                if r['aberto_ate']>r['agora'] or (r['sonda_ate'] and r['sonda_ate']>r['agora']): raise CircuitoAberto()
                return await c.fetchval('UPDATE agente_circuitos SET geracao=geracao+1,sonda_ate=now()+make_interval(secs=>$3) WHERE tenant_id=$1 AND agente_id=$2 RETURNING geracao',t,a,timeout+5)
            return r['geracao']

    async def terminar(self,t,a,geracao,erro=None):
        alerta=False
        async with self.db.acquire() as c,c.transaction():
            if erro is None:
                await c.execute('UPDATE agente_circuitos SET falhas=0,aberto_ate=NULL,sonda_ate=NULL,janela_inicio=now() WHERE tenant_id=$1 AND agente_id=$2 AND geracao=$3',t,a,geracao)
                return
            r=await c.fetchrow('SELECT *,now() AS agora FROM agente_circuitos WHERE tenant_id=$1 AND agente_id=$2 FOR UPDATE',t,a)
            n=r['falhas']+1 if (r['agora']-r['janela_inicio']).total_seconds()<300 else 1
            abrir=n>=3 or r['sonda_ate'] is not None
            await c.execute('''UPDATE agente_circuitos SET falhas=$3,
                janela_inicio=CASE WHEN $3=1 THEN now() ELSE janela_inicio END,
                aberto_ate=CASE WHEN $4 THEN now()+interval '60 seconds' ELSE aberto_ate END,
                geracao=geracao+CASE WHEN $4 THEN 1 ELSE 0 END,sonda_ate=NULL
                WHERE tenant_id=$1 AND agente_id=$2''',t,a,n,abrir)
            if isinstance(erro,CredencialRecusada) and (not r['ultimo_alerta'] or (r['agora']-r['ultimo_alerta']).total_seconds()>=10800):
                alerta=True
                await c.execute('UPDATE agente_circuitos SET ultimo_alerta=now() WHERE tenant_id=$1 AND agente_id=$2',t,a)
                await auditar(c,t,'sistema','agente.credencial_recusada',a)
                await emitir(c,t,'agente.credencial_recusada',{'agente_id':a,'motivo':'credencial do Skyone recusada'})
        if alerta: logging.getLogger('gateway').warning('credencial do Skyone recusada; tenant=%s agente=%s',t,a)

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
@router.get('/tenants/{tenant}/agentes/{agente}/circuito')
async def estado(request:Request,tenant:str,agente:str):
    if not await Repositorio(request.app.state.db).obter(tenant,'agentes',agente):raise HTTPException(404,'NOT_FOUND')
    row=await request.app.state.db.fetchrow('''SELECT falhas,aberto_ate,ultimo_alerta,
      CASE WHEN aberto_ate>now() THEN 'aberto' WHEN aberto_ate IS NOT NULL THEN 'meio_aberto' ELSE 'fechado' END AS estado
      FROM agente_circuitos WHERE tenant_id=$1 AND agente_id=$2''',tenant,agente)
    return dict(row) if row else {'estado':'fechado','falhas':0,'aberto_ate':None,'ultimo_alerta':None}
