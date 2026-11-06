"""IFOOD1-a (docs/ESPEC_IFOOD1.md): token único do aplicativo centralizado e lojas do iFood ligadas às empresas.
iFood simulado (httpx.MockTransport): nenhum teste chama o iFood de verdade."""
import json
import httpx
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
T='bykoji'
SEGREDO='segredo-ficticio-do-teste-0123456789'
GESTOR={'X-Role':'gestor','X-Actor':'gestor@exemplo.test'}
SUP={'X-Role':'supervisor','X-Actor':'sup@exemplo.test'}


class RedisSimples:
    def __init__(self): self.d={}
    async def get(self,k): return self.d.get(k)
    async def set(self,k,v,nx=False,ex=None):
        if nx and k in self.d: return None
        self.d[k]=v;return True
    async def delete(self,k): self.d.pop(k,None)
    async def eval(self,*a): return 1
    async def ping(self): return True


class IfoodFalso:
    """Lojas autorizadas, contagem de tokens pedidos e respostas forçadas por caminho."""
    def __init__(self):
        self.tokens=0;self.chamadas=[];self.forcar={};self.lojas=[{'id':'loja-a-1','name':'Loja Teste A','corporateName':'Empresa Teste A Ltda'},
                                                                  {'id':'loja-b-2','name':'Loja Teste B','corporateName':'Empresa Teste B Ltda'}]
        self.validos=set()

    def __call__(self,req):
        self.chamadas.append((req.method,req.url.path))
        if req.url.path=='/authentication/v1.0/oauth/token':
            corpo=dict(x.split('=',1) for x in req.content.decode().split('&'))
            assert corpo['grantType']=='client_credentials' and corpo['clientId']=='app-teste'
            if corpo['clientSecret']!=SEGREDO: return httpx.Response(401,json={'error':'invalid_client'})
            self.tokens+=1;tok=f'tok-{self.tokens}';self.validos={tok}
            return httpx.Response(200,json={'accessToken':tok,'type':'bearer','expiresIn':21600})
        if req.headers.get('authorization','').removeprefix('Bearer ') not in self.validos: return httpx.Response(401)
        if req.url.path in self.forcar: return httpx.Response(self.forcar[req.url.path])
        if req.url.path=='/merchant/v1.0/merchants': return httpx.Response(200,json=self.lojas)
        if req.url.path.startswith('/merchant/v1.0/merchants/'):
            m=req.url.path.rsplit('/',1)[1];l=next((x for x in self.lojas if x['id']==m),None)
            return httpx.Response(200,json=l) if l else httpx.Response(404)
        return httpx.Response(404)


def test_lojas_do_ifood_token_unico_e_so_plataforma_liga(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);falso=IfoodFalso()
        app.state.redis=RedisSimples();app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(falso))
        await conn.execute("INSERT INTO tenant_unidades(id,tenant_id,nome,codigo) VALUES ('u1',$1,'Pátio','patio_guedala')",T)
        async with client:
            assert (await client.post('/admin/v1/tenants',json={'id':'outra_empresa','nome':'Outra'})).status_code==201
            # Sem credenciais no .env: recusa clara, nada chamado.
            r=await client.get('/admin/v1/ifood/lojas-autorizadas')
            assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(503,'nao_configurado') and falso.chamadas==[]
            assert (await client.get(f'/admin/v1/tenants/{T}/ifood/lojas',headers=GESTOR)).json()=={'configurado':False,'itens':[]}
            monkeypatch.setenv('IFOOD_CLIENT_ID','app-teste');monkeypatch.setenv('IFOOD_CLIENT_SECRET',SEGREDO)
            # Só plataforma lista e liga.
            for h in (GESTOR,SUP):
                assert (await client.get('/admin/v1/ifood/lojas-autorizadas',headers=h)).status_code==403
                assert (await client.put(f'/admin/v1/tenants/{T}/ifood/lojas/loja-a-1',json={},headers=h)).status_code==403
            assert (await client.get(f'/admin/v1/tenants/{T}/ifood/lojas',headers=SUP)).status_code==403
            r=await client.get('/admin/v1/ifood/lojas-autorizadas');assert r.status_code==200,r.text
            assert [(l['merchant_id'],l['nome'],l['ligada_a']) for l in r.json()['itens']]==[('loja-a-1','Loja Teste A',None),('loja-b-2','Loja Teste B',None)]
            # Liga: loja não autorizada, unidade de outra empresa e loja já ligada a outra empresa são recusadas.
            q=f'/admin/v1/tenants/{T}/ifood/lojas'
            r=await client.put(q+'/loja-x-9',json={});assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(409,'nao_autorizada')
            r=await client.put(q+'/loja-a-1',json={'unidade':'nao_existe'});assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(400,'unidade')
            r=await client.put(q+'/loja-a-1',json={'unidade':'patio_guedala'});assert r.status_code==200,r.text
            assert (r.json()['nome'],r.json()['unidade'],r.json()['situacao'],r.json()['ativo'])==('Loja Teste A','patio_guedala','conectada',True)
            r=await client.put('/admin/v1/tenants/outra_empresa/ifood/lojas/loja-a-1',json={})
            assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(409,'ligada_a_outra_empresa')
            assert (await client.get('/admin/v1/tenants/outra_empresa/ifood/lojas',headers=GESTOR)).json()['itens']==[]
            g=(await client.get(q,headers=GESTOR)).json();assert g['configurado'] and [l['merchant_id'] for l in g['itens']]==['loja-a-1']
            assert (await client.get('/admin/v1/ifood/lojas-autorizadas')).json()['itens'][0]['ligada_a']=={'tenant_id':T,'unidade':'patio_guedala','ativo':True}
            # Um token só para tudo; guardado cifrado; o segredo e o token não aparecem em resposta nem na auditoria.
            assert falso.tokens==1
            assert all('tok-1' not in v and SEGREDO not in v for v in app.state.redis.d.values())
            auditoria=str([dict(x) for x in await conn.fetch("SELECT acao,alvo,dados FROM audit_log WHERE acao LIKE 'ifood.%'")])
            assert 'ifood.loja_ligada' in auditoria and SEGREDO not in auditoria and 'tok-' not in auditoria
            # Token vencido no iFood (401): renova uma vez e segue.
            falso.validos=set()
            assert (await client.post(q+'/loja-a-1/conferir',headers=GESTOR)).json()['situacao']=='conectada' and falso.tokens==2
            # Loja tirou a permissão: conferir marca sem_permissao; iFood fora do ar não muda nada.
            falso.forcar['/merchant/v1.0/merchants/loja-a-1']=403
            assert (await client.post(q+'/loja-a-1/conferir',headers=GESTOR)).json()['situacao']=='sem_permissao'
            falso.forcar['/merchant/v1.0/merchants/loja-a-1']=503
            r=await client.post(q+'/loja-a-1/conferir',headers=GESTOR);assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(502,'indisponivel')
            assert (await client.get(q,headers=GESTOR)).json()['itens'][0]['situacao']=='sem_permissao'
            # Credencial recusada pelo iFood: motivo claro, sem expor o segredo.
            falso.validos=set();app.state.redis.d.clear();monkeypatch.setenv('IFOOD_CLIENT_SECRET','outro-segredo-ficticio')
            r=await client.get('/admin/v1/ifood/lojas-autorizadas')
            assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(502,'autenticacao') and 'segredo' not in r.text
            # Desligar: só plataforma e com o nome da loja.
            assert (await client.request('DELETE',q+'/loja-a-1',json={'confirmacao':'Loja Teste A'},headers=GESTOR)).status_code==403
            r=await client.request('DELETE',q+'/loja-a-1',json={'confirmacao':'outra'});assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(400,'confirmacao')
            assert (await client.request('DELETE',q+'/loja-a-1',json={'confirmacao':' Loja Teste A '})).json()=={'ok':True}
            assert await conn.fetchval('SELECT count(*) FROM ifood_lojas')==0
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='ifood.loja_desligada'")==1
            await app.state.http.aclose()
    banco(cenario)


def test_credenciais_pelo_painel_so_com_a_chave_de_plataforma(monkeypatch):
    """Decisão do Marco (06/11/2026): Client ID e Secret entram pelo painel, só com a PANEL_PLATFORM_KEY; testados no iFood antes de
    gravar; cifrados; nunca devolvidos; valem sobre o .env; trocar não reaproveita o token das antigas."""
    from cryptography.fernet import Fernet
    from plataforma import Infra,criar_app
    from test_admin import Pool
    PLAT,EMPRESA='d'*32,'a'*32
    monkeypatch.delenv('IFOOD_CLIENT_ID',raising=False);monkeypatch.delenv('IFOOD_CLIENT_SECRET',raising=False)
    async def cenario(conn):
        await migrar.aplicar(conn)
        infra=Infra(EMPRESA,'b'*32,'c'*32,Fernet.generate_key().decode(),panel_platform_key=PLAT);infra.validar()
        app=criar_app(infra,Pool(conn),RedisSimples());falso=IfoodFalso()
        app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(falso))
        c=httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url='http://teste')
        def h(chave,papel='plataforma'): return {'Authorization':'Bearer '+chave,'X-Actor':'marco@exemplo.test','X-Role':papel}
        q='/admin/v1/ifood/credenciais'
        async with c:
            assert (await c.get(q,headers=h(PLAT))).json()['configurado'] is False
            certo={'client_id':'app-teste','client_secret':SEGREDO}
            # Papel de plataforma com a chave de empresa: recusado nesta rota, mesmo na fase A; gestor também.
            r=await c.put(q,json=certo,headers=h(EMPRESA));assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(403,'papel_sem_credencial')
            assert (await c.put(q,json=certo,headers=h(EMPRESA,'gestor'))).status_code==403
            assert (await c.get(q,headers=h(EMPRESA,'gestor'))).status_code==403
            # Formato e credencial recusada pelo iFood: nada gravado, valor nunca repetido.
            for corpo,campo in (({'client_id':'app-teste'},'corpo'),({**certo,'x':1},'corpo'),({**certo,'client_id':'curto'},'client_id'),
                                ({**certo,'client_secret':'com espaco no meio'},'client_secret')):
                r=await c.put(q,json=corpo,headers=h(PLAT));assert (r.status_code,r.json()['erro']['detalhes']['campo'])==(400,campo),corpo
            r=await c.put(q,json={'client_id':'app-teste','client_secret':'segredo-errado-ficticio'},headers=h(PLAT))
            assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(400,'credenciais_recusadas') and 'segredo-errado' not in r.text
            assert await conn.fetchval('SELECT count(*) FROM integracao_credenciais')==0
            # Grava: testado no iFood, cifrado, só o fim do Client ID volta.
            r=await c.put(q,json=certo,headers=h(PLAT));assert r.status_code==200,r.text
            assert {k:r.json()[k] for k in ('configurado','origem','client_id_final','atualizado_por')}=={'configurado':True,'origem':'painel','client_id_final':'este','atualizado_por':'marco@exemplo.test'}
            assert SEGREDO not in r.text and 'app-teste' not in r.text
            linha=await conn.fetchrow('SELECT * FROM integracao_credenciais');assert SEGREDO not in linha['valor_enc'] and 'app-teste' not in linha['valor_enc']
            a=await conn.fetchrow("SELECT tenant_id,actor,dados FROM audit_log WHERE acao='ifood.credenciais_alteradas'")
            assert a['tenant_id'] is None and a['actor']=='marco@exemplo.test' and SEGREDO not in a['dados'] and 'app-teste' not in a['dados']
            # Valem para as outras rotas, sem .env, e o token pedido no teste é reaproveitado.
            r=await c.get('/admin/v1/ifood/lojas-autorizadas',headers=h(PLAT));assert r.status_code==200 and len(r.json()['itens'])==2
            assert falso.tokens==1
            # Gravadas pelo painel valem sobre o .env.
            monkeypatch.setenv('IFOOD_CLIENT_ID','outro-app-do-env');monkeypatch.setenv('IFOOD_CLIENT_SECRET','outro-segredo-do-env-ficticio')
            assert (await c.get('/admin/v1/ifood/lojas-autorizadas',headers=h(PLAT))).status_code==200 and falso.tokens==1
            assert (await c.get(q,headers=h(PLAT))).json()['origem']=='painel'
            await app.state.http.aclose()
    banco(cenario)
