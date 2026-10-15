from datetime import datetime,timezone,timedelta
import json
from urllib.parse import urlsplit,parse_qs
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar,modelo_de_teste

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


class RedisFila:
    def __init__(self): self.dados={};self.due={};self.agendados=[]
    async def eval(self,script,n,*args):
        if n==2:
            self.agendados.append(args)
            self.due[args[2]]=0
            return 1
        if "redis.call('GET'" in script:
            if self.dados.get(args[0])==args[1]: self.dados.pop(args[0],None);return 1
            return 0
        return 1
    async def set(self,key,value,nx=False,ex=None):
        if nx and key in self.dados:return None
        self.dados[key]=value;return True
    async def zrem(self,key,value): self.due.pop(value,None)
    async def delete(self,key): self.dados.pop(key,None)
    async def zrangebyscore(self,*args,**kwargs): return list(self.due)
    async def ping(self): return True


async def ativo(client,tenant,agente,canal,**cfg):
    assert (await client.patch(f'/admin/v1/tenants/{tenant}/agentes/{agente}',json={'status':'ativo','modo_piloto':False,**cfg})).status_code==200
    assert (await client.patch(f'/admin/v1/tenants/{tenant}/canais/{canal}',json={'ativo':True})).status_code==200


def test_INV08_INV17_pipeline_dois_tenants_tres_agentes_e_debounce():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        chamadas=[]
        async def agente(http,repo,cofre,tenant,cfg,payload):
            chamadas.append((tenant,cfg['id'],payload));return {'resposta':['teste '+cfg['id']],'acao':'nenhuma'}
        app.state.pipeline.chamar=agente
        async with client:
            a,ca=await criar(client)
            b,cb=await criar(client,'bravo')
            a2=(await client.post('/admin/v1/tenants/alpha/agentes',json={'nome':'A2','modelo_id':await modelo_de_teste(client)})).json()['id']
            ca2=(await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'C2','tipo':'api','agente_id':a2})).json()['id']
            for tenant,ag,can in [('alpha',a,ca),('alpha',a2,ca2),('bravo',b,cb)]:
                await ativo(client,tenant,ag,can,debounce_s=2,max_wait_s=8)
                for i in range(2):
                    r=await client.post(f'/admin/v1/tenants/{tenant}/agentes/{ag}/simular',json={
                        'canal_id':can,'identificador':'usuario','texto':f'{ag}-{i}','message_id':f'm{i}'})
                    assert r.status_code==200,r.text
                conv=r.json()['conversation_id']
                # Repetido não duplica a mensagem.
                r=await client.post(f'/admin/v1/tenants/{tenant}/agentes/{ag}/simular',json={
                        'canal_id':can,'identificador':'usuario','texto':'repetido','message_id':'m1'})
                assert r.json()['duplicada']
                await app.state.pipeline.processar(tenant,conv)
                assert (await client.get(f'/admin/v1/tenants/{tenant}/metricas',params={'agente':ag})).json()['turnos']==1
                assert await conn.fetchval('SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND origem=$3',tenant,conv,'bot')==1
            assert len(chamadas)==3
            assert all(x[2]['message']==f'{x[1]}-0\n{x[1]}-1' for x in chamadas)
            assert all(args[-2:]==(2,8) for args in fila.agendados)
            assert await conn.fetchval('SELECT count(*) FROM turnos')==3
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE origem='cliente' AND processada_em IS NULL")==0
    banco(cenario)


def test_webhook_segredo_inativos_e_sanitizacao():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,ca=await criar(client)
            # Segredo só obtido pela criação de um canal.
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'Webhook','tipo':'api','agente_id':a})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            url=f'/webhooks/api/{canal}'
            body={'identificador':'u','texto':'oi'}
            assert (await client.post(url,json=body)).status_code==401
            assert (await client.post(url,params={'s':segredo},json=body)).json()['ignorado']
            await ativo(client,'alpha',a,canal)
            assert (await client.post(url,params={'s':segredo},json=body)).status_code==200
            assert len(fila.agendados)==1
            await client.patch('/admin/v1/tenants/alpha',json={'status':'suspenso'})
            assert (await client.post(url,params={'s':segredo},json=body)).json()['ignorado']
            assert len(fila.agendados)==1
            await client.patch('/admin/v1/tenants/alpha',json={'status':'ativo'})
            await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'status':'pausado'})
            assert (await client.post(url,params={'s':segredo},json=body)).json()['ignorado']
            assert len(fila.agendados)==1
    banco(cenario)


def test_INV03_pausa_durante_agente_e_falha_envio_nao_registra_entrega():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,ca=await criar(client);await ativo(client,'alpha',a,ca)
            r=await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':ca,'identificador':'u','texto':'oi'})
            conv=r.json()['conversation_id']
            async def pausar(http,repo,cofre,tenant,cfg,payload):
                await conn.execute('UPDATE conversas SET bot_pausado_ate=now()+interval \'1 hour\' WHERE tenant_id=$1 AND conversation_id=$2',tenant,conv)
                return {'resposta':['nao enviar'],'acao':'nenhuma'}
            app.state.pipeline.chamar=pausar
            await app.state.pipeline.processar('alpha',conv)
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE origem='bot'")==0
            assert await conn.fetchval('SELECT status FROM turnos')=='ignorado'
            await conn.execute('UPDATE conversas SET bot_pausado_ate=NULL WHERE tenant_id=$1 AND conversation_id=$2','alpha',conv)
            await app.state.pipeline.receber('alpha',ca,{'identificador':'u','texto':'outra'})
            async def responder(*args): return {'resposta':['resposta'],'acao':'nenhuma'}
            async def falhar(*args): raise ValueError('erro com token-secreto')
            app.state.pipeline.chamar=responder;app.state.pipeline.enviar=falhar
            await app.state.pipeline.processar('alpha',conv)
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE origem='bot'")==0
            turno=await conn.fetchrow('SELECT status,erro FROM turnos ORDER BY id DESC LIMIT 1')
            assert tuple(turno)==('erro','SEND_ERROR')
    banco(cenario)


def test_INV17_nova_mensagem_durante_chamada_vai_para_proximo_lote():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,ca=await criar(client);await ativo(client,'alpha',a,ca)
            r=await app.state.pipeline.receber('alpha',ca,{'identificador':'u','texto':'primeira'})
            conv=r['conversation_id'];chamadas=[]
            async def agente(http,repo,cofre,tenant,cfg,payload):
                chamadas.append(payload)
                if len(chamadas)==1: await app.state.pipeline.receber('alpha',ca,{'identificador':'u','texto':'segunda'})
                return {'resposta':['ok'],'acao':'nenhuma'}
            app.state.pipeline.chamar=agente
            await app.state.pipeline.processar('alpha',conv)
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE origem='cliente' AND processada_em IS NULL")==1
            await app.state.pipeline.processar('alpha',conv)
            assert [p['message'] for p in chamadas]==['primeira','segunda']
            assert await conn.fetchval('SELECT count(*) FROM turnos')==2
    banco(cenario)


def test_SEG03_canal_saida_valida_url_e_propaga_http(monkeypatch):
    from canais import uazapi
    from nucleo.credenciais import Cofre
    from cryptography.fernet import Fernet
    import asyncio,httpx
    cofre=Cofre(Fernet.generate_key())
    canal={'credenciais_enc':{'fernet':cofre.cifrar('alpha',{'base_url':'https://canal.example','token':'token-fixture'})}}
    chamadas=[]
    async def validar(url): chamadas.append(url)
    monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',validar)
    async def executar():
        def responder(req):
            assert chamadas==['https://canal.example/send/text']
            assert req.headers['token']=='token-fixture'
            return httpx.Response(401,text='token-fixture')
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as http:
            with pytest.raises(ValueError) as erro: await uazapi.enviar(http,cofre,'alpha',canal,'55119999','oi')
            assert 'token-fixture' not in str(erro.value)
    asyncio.run(executar())


def test_fila_lua_em_redis_real():
    import asyncio,os,uuid
    import redis.asyncio as redis
    from nucleo.pipeline import Pipeline
    async def executar():
        client=redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
        prefixo='teste05:'+uuid.uuid4().hex
        pipeline=Pipeline(None,client,None,None,None,prefixo)
        cfg={'debounce_s':2,'max_wait_s':8}
        chave=pipeline.chave('alpha','alpha:canal:u')
        try:
            await pipeline.enfileirar('alpha','alpha:canal:u',cfg)
            primeira=float(await client.get(prefixo+':first:'+chave))
            due=await client.zscore(prefixo+':due',chave)
            assert 1.9 <= due-primeira <= 2.1
            await pipeline.enfileirar('alpha','alpha:canal:u',cfg)
            assert float(await client.get(prefixo+':first:'+chave))==primeira
        finally:
            await client.delete(prefixo+':first:'+chave,prefixo+':due')
            await client.aclose()
    asyncio.run(executar())
