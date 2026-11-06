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
