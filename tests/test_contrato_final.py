import httpx,pytest
from fastapi import HTTPException
import redis.exceptions
from erros_api import MENSAGENS
from test_admin import preparar,criar,Pool,modelo_de_teste
from test_pipeline_multi import RedisFila,ativo
from test_migrations import banco,migrar,DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


def test_codigos_fixos_envelope_validacao_e_middlewares(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        @app.get('/diagnostico/{status}')
        async def erro(status:int,credencial:bool=False):
            raise HTTPException(status,'credencial do Skyone recusada' if credencial else 'segredo-nao-ecoar')
        async with c:
            casos=[(400,'VALIDATION_ERROR'),(401,'UNAUTHORIZED'),(403,'FORBIDDEN'),(404,'NOT_FOUND'),
                   (409,'CONFLICT'),(429,'RATE_LIMITED'),(502,'UPSTREAM_ERROR'),(501,'NOT_IMPLEMENTED'),(503,'UNAVAILABLE')]
            for status,code in casos:
                r=await c.get('/diagnostico/'+str(status))
                assert r.status_code==status
                assert r.json()['erro']['codigo']==code and r.json()['erro']['mensagem']==MENSAGENS[code]
                assert 'segredo-nao-ecoar' not in r.text
                if status==429: assert r.headers['Retry-After']=='1'
            r=await c.get('/diagnostico/502?credencial=true')
            assert r.json()['erro']['codigo']=='CREDENTIAL_REJECTED'
            r=await c.get('/rota-inexistente')
            assert r.status_code==404 and r.json()['erro']['codigo']=='NOT_FOUND'
            r=await c.get('/admin/v1/tenants',headers={'X-Forwarded-For':'127.0.0.1'})
            assert r.status_code==404 and r.json()['erro']['codigo']=='NOT_FOUND'
            r=await c.post('/admin/v1/tenants',json={'id':'alpha','nome':123})
            assert r.status_code==400 and r.json()['erro']['detalhes']['campo']=='nome'
            r=await c.get('/admin/v1/tenants?limite=invalido')
            assert r.json()['erro']['detalhes']['campo']=='limite'
            r=await c.post('/admin/v1/tenants',content=b'x'*(app.state.infra.max_body_bytes+1))
            assert r.status_code==413 and r.json()['erro']['codigo']=='VALIDATION_ERROR'
            assert r.json()['erro']['detalhes']['campo']=='corpo'
            async def chunks():
                yield b'x'*(app.state.infra.max_body_bytes//2+1)
                yield b'x'*(app.state.infra.max_body_bytes//2+1)
            r=await c.post('/admin/v1/tenants',content=chunks())
            assert r.status_code==413 and r.json()['erro']['codigo']=='VALIDATION_ERROR'
            async def recusar(*args):return False
            monkeypatch.setattr('limites.permitir',recusar)
            r=await c.get('/api/v1/qualquer')
            assert r.status_code==429 and r.headers['Retry-After']=='1'
            assert r.json()['erro']['codigo']=='RATE_LIMITED'
            async def indisponivel(*args):raise redis.exceptions.ConnectionError('senha-nao-expor')
            monkeypatch.setattr('limites.permitir',indisponivel)
            r=await c.get('/api/v1/qualquer')
            assert r.status_code==503 and r.json()['erro']['codigo']=='UNAVAILABLE'
            assert 'senha-nao-expor' not in r.text
    banco(cenario)


def test_INV08_historico_paginado_sem_lacunas_e_insercao_concorrente():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        app.state.pipeline.redis=RedisFila()
        async with c:
            a,can=await criar(c);b,cb=await criar(c,'bravo')
            await ativo(c,'alpha',a,can);await ativo(c,'bravo',b,cb)
            conv=(await app.state.pipeline.receber('alpha',can,{'identificador':'cliente','texto':'primeira'}))['conversation_id']
            other=(await app.state.pipeline.receber('bravo',cb,{'identificador':'cliente','texto':'alheia'}))['conversation_id']
            await conn.executemany("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('alpha',$1,'entrada','cliente',$2)",[(conv,str(i)) for i in range(105)])
            ids=[r['id'] for r in await conn.fetch("SELECT id FROM mensagens WHERE tenant_id='alpha' AND conversation_id=$1 ORDER BY id",conv)]
            base='/admin/v1/tenants/alpha/conversas/'+conv+'/mensagens'
            default=(await c.get(base)).json()
            assert len(default['itens'])==100 and default['tem_mais']
            assert [r['id'] for r in default['itens']]==ids[-100:]
            first=(await c.get(base,params={'limite':40})).json()
            await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('alpha',$1,'entrada','cliente','nova')",conv)
            second=(await c.get(base,params={'limite':40,'antes':first['itens'][0]['id']})).json()
            third=(await c.get(base,params={'limite':40,'antes':second['itens'][0]['id']})).json()
            assert [r['id'] for r in third['itens']+second['itens']+first['itens']]==ids
            assert not third['tem_mais']
            assert (await c.get('/admin/v1/tenants/alpha/conversas/'+other+'/mensagens')).status_code==404
            for params,campo in [({'antes':0},'antes'),({'limite':101},'limite'),({'limite':0},'limite')]:
                r=await c.get(base,params=params)
                assert r.status_code==400 and r.json()['erro']['detalhes']['campo']==campo
            key=(await c.post('/admin/v1/tenants/alpha/api-keys',json={'nome':'hist','escopos':['eventos:ler'],'agente_id':a})).json()['chave']
            result=await c.get('/api/v1/conversas/'+conv+'/mensagens',params={'limite':1},headers={'X-API-Key':key})
            assert result.status_code==200 and len(result.json()['itens'])==1 and result.json()['tem_mais']
    banco(cenario)


def test_INV08_lista_agentes_canais_circuito_sem_n_mais_um():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,can=await criar(c);b,cb=await criar(c,'bravo')
            a2=(await c.post('/admin/v1/tenants/alpha/agentes',json={'nome':'Sem canal','modelo_id':await modelo_de_teste(c)})).json()['id']
            await conn.execute("INSERT INTO agente_circuitos(tenant_id,agente_id,aberto_ate) VALUES ('alpha',$1,now()+interval '1 minute')",a)
            class Contador(Pool):
                def __init__(self,conn):super().__init__(conn);self.n=0
                async def fetch(self,*args):self.n+=1;return await self.conn.fetch(*args)
                async def fetchrow(self,*args):self.n+=1;return await self.conn.fetchrow(*args)
            db=Contador(conn);app.state.db=db
            r=await c.get('/admin/v1/tenants/alpha/agentes')
            assert r.status_code==200,r.text
            assert db.n==2  # uma validação de tenant e uma consulta agregada, independente do número de agentes
            itens={x['id']:x for x in r.json()['itens']}
            assert itens[a]['circuito_estado']=='aberto' and itens[a]['canais']==[{'id':can,'tipo':'api','ativo':False}]
            assert itens[a2]['circuito_estado']=='fechado' and itens[a2]['canais']==[]
            assert b not in r.text and cb not in r.text and 'segredo-fixture' not in r.text
            await conn.execute("UPDATE agente_circuitos SET aberto_ate=now()-interval '1 second' WHERE tenant_id='alpha' AND agente_id=$1",a)
            assert {x['id']:x for x in (await c.get('/admin/v1/tenants/alpha/agentes')).json()['itens']}[a]['circuito_estado']=='meio_aberto'
    banco(cenario)
