from contextlib import asynccontextmanager
from cryptography.fernet import Fernet
import httpx
import pytest
from plataforma import Infra, criar_app
from test_migrations import banco,migrar,DSN

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


class Pool:
    def __init__(self,conn): self.conn=conn
    def __getattr__(self,nome): return getattr(self.conn,nome)
    @asynccontextmanager
    async def acquire(self): yield self.conn


class Redis:
    async def eval(self,*args): return 1
    async def ping(self): return True


def preparar(conn):
    infra=Infra('a'*32,'b'*32,'c'*32,Fernet.generate_key().decode())
    app=criar_app(infra,Pool(conn),Redis())
    client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url='http://teste')
    # Quem monta o cenário é a plataforma; os testes de papel passam X-Role explícito.
    client.headers.update({'Authorization':'Bearer '+infra.panel_key,'X-Actor':'teste','X-Role':'plataforma'})
    return app,client


MODELO_DE_TESTE='generico_teste'


async def modelo_de_teste(client):
    """Modelo genérico para os testes (o assistente_generico ficou inativo para criação no MULTI1-a). Mesma configuração dele."""
    r=await client.post('/admin/v1/modelos-agente',headers={'X-Role':'plataforma'},json={'id':MODELO_DE_TESTE,'nome':'Genérico de teste','tipo':'interno',
        'segmento':'geral','modulos_permitidos':[],'comportamento_padrao':{'send_mode':'dry','agente_mode':'mock','modo_piloto':True}})
    assert r.status_code in (201,409),r.text
    return MODELO_DE_TESTE


async def criar(client,tenant='alpha'):
    r=await client.post('/admin/v1/tenants',json={'id':tenant,'nome':tenant})
    assert r.status_code==201,r.text
    r=await client.post(f'/admin/v1/tenants/{tenant}/agentes',json={'nome':'A','modelo_id':await modelo_de_teste(client)})
    assert r.status_code==201,r.text
    agente=r.json()['id']
    r=await client.post(f'/admin/v1/tenants/{tenant}/canais',json={'nome':'C','tipo':'api','agente_id':agente,'credenciais':{'token':'segredo-fixture'}})
    assert r.status_code==201,r.text
    return agente,r.json()['id']


def test_INV08_INV17_admin_isolamento_auditoria_e_segredos():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            a,ca=await criar(client)
            b,cb=await criar(client,'bravo')
            for entidade,identidade in [('agentes',b),('canais',cb)]:
                url=f'/admin/v1/tenants/alpha/{entidade}/{identidade}'
                assert (await client.get(url)).status_code==404
                assert (await client.patch(url,json={'nome':'invasao'})).status_code==404
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'errado','tipo':'api','agente_id':b})
            assert r.status_code==404
            canal=(await client.get(f'/admin/v1/tenants/alpha/canais/{ca}')).json()
            assert 'segredo-fixture' not in str(canal) and 'fernet' not in str(canal)
            stored=await conn.fetchval('SELECT credenciais_enc::text FROM tenant_canais WHERE tenant_id=$1 AND id=$2','alpha',ca)
            assert 'segredo-fixture' not in stored
            assert (await client.put('/admin/v1/tenants/alpha/config',json={'history_turns':3})).status_code==200
            got=(await client.get(f'/admin/v1/tenants/alpha/agentes/{a}')).json()
            assert got['configuracao_efetiva']['history_turns']==3
            assert (await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'history_turns':2})).status_code==200
            assert (await client.get(f'/admin/v1/tenants/alpha/agentes/{a}')).json()['configuracao_efetiva']['history_turns']==2
            audit=(await client.get('/admin/v1/tenants/alpha/auditoria')).json()['itens']
            assert all(r['tenant_id']=='alpha' for r in audit)
            assert 'segredo-fixture' not in str(audit)
            assert len(audit)==5
            r=await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'nome':'nao-confirmar','debounce_s':15,'max_wait_s':1})
            assert r.status_code==400
            assert (await client.get(f'/admin/v1/tenants/alpha/agentes/{a}')).json()['nome']=='A'
            assert len((await client.get('/admin/v1/tenants/alpha/auditoria')).json()['itens'])==5
    banco(cenario)


def test_SEG02_SEG12_admin_rede_rotacao_actor():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            for chave in ('a'*32,'b'*32):
                assert (await client.get('/admin/v1/tenants',headers={'Authorization':'Bearer '+chave})).status_code==200
            assert (await client.get('/admin/v1/tenants',headers={'Authorization':'Bearer '+'d'*32})).status_code==401
            for cabecalho in ('X-Forwarded-For','X-Real-IP','Forwarded'):
                assert (await client.get('/admin/v1/tenants',headers={cabecalho:'127.0.0.1'})).status_code==404
                assert (await client.get('/admin/v1/tenants',headers={cabecalho:''})).status_code==404
            assert (await client.post('/admin/v1/tenants',json={'id':'teste','nome':'X'},headers={'X-Actor':''})).status_code==400
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('8.8.8.8',123)),base_url='http://teste') as externo:
            assert (await externo.get('/admin/v1/tenants',headers={'Authorization':'Bearer '+'a'*32})).status_code==404
    banco(cenario)


def test_SEG11_chaves_escopo_validade_ip_revogacao_e_agente():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            a,ca=await criar(client)
            b,cb=await criar(client,'bravo')
            conv=f'alpha:{ca}:teste'
            await conn.execute('INSERT INTO conversas(conversation_id,canal,tenant_id,agente_id,canal_id) VALUES ($1,$2,$3,$4,$5)',conv,'api','alpha',a,ca)
            r=await client.post('/admin/v1/tenants/alpha/api-keys',json={'nome':'K','escopos':['eventos:ler'],'agente_id':a})
            assert r.status_code==201,r.text
            key=r.json(); h={'X-API-Key':key['chave']}
            url=f'/api/v1/conversas/{conv}/mensagens'
            assert (await client.get(url,headers=h)).status_code==200
            outra=f'bravo:{cb}:teste'
            await conn.execute('INSERT INTO conversas(conversation_id,canal,tenant_id,agente_id,canal_id) VALUES ($1,$2,$3,$4,$5)',outra,'api','bravo',b,cb)
            assert (await client.get(f'/api/v1/conversas/{outra}/mensagens',headers=h)).status_code==404
            assert key['chave'] not in (await client.get('/admin/v1/tenants/alpha/api-keys')).text
            kurl=f'/admin/v1/tenants/alpha/api-keys/{key["id"]}'
            assert (await client.patch(kurl,json={'escopos':['conversar']})).status_code==200
            assert (await client.get(url,headers=h)).status_code==403
            assert (await client.patch(kurl,json={'escopos':['eventos:ler'],'ips_permitidos':['8.8.8.0/24']})).status_code==200
            assert (await client.get(url,headers=h)).status_code==403
            assert (await client.patch(kurl,json={'ips_permitidos':[],'expira_em':'2020-01-01T00:00:00Z'})).status_code==200
            assert (await client.get(url,headers=h)).status_code==401
            assert (await client.patch(kurl,json={'expira_em':None})).status_code==200
            # Outro agente do mesmo tenant também é restrito.
            novo=(await client.post('/admin/v1/tenants/alpha/agentes',json={'nome':'X','modelo_id':await modelo_de_teste(client)})).json()['id']
            assert (await client.patch(kurl,json={'agente_id':novo})).status_code==200
            assert (await client.get(url,headers=h)).status_code==404
            assert (await client.patch(f'/admin/v1/tenants/bravo/api-keys/{key["id"]}',json={'nome':'errado'})).status_code==404
            assert (await client.delete(kurl)).status_code==200
            assert (await client.get(url,headers=h)).status_code==401
    banco(cenario)
