"""/testar com usar_provedor_real: chama o Skyone sem mudar o agente."""
import base64,json
from functools import partial
import httpx,pytest
from agentes import skyone
from agentes.controle import CredencialRecusada
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')

URL='https://skyone.example/flow'


class Espiao:
    """Controle de admissão observado: cota por URL e circuito passam por aqui."""
    def __init__(self):self.urls=[];self.terminados=[]
    async def esperar(self,url):self.urls.append(url)
    async def iniciar(self,tenant,agente,timeout):return 1
    async def terminar(self,tenant,agente,geracao,erro=None):self.terminados.append(erro)


def montar(app,monkeypatch,resposta=None):
    async def dns(*args):pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',dns)
    chamadas=[]
    async def responder(req):
        chamadas.append(req)
        if resposta: return resposta
        p=json.loads(req.content)
        return httpx.Response(200,json={**p,'resposta':'ok do fluxo','status':'success','tipo':'text','acao':'nenhuma'})
    app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(responder))
    espiao=Espiao();app.state.pipeline.chamar=partial(skyone.chamar,controle=espiao)
    return chamadas,espiao


async def agente_skyone_em_mock(c,tenant='alpha'):
    a,_=await criar(c,tenant)
    r=await c.patch(f'/admin/v1/tenants/{tenant}/agentes/{a}',json={
        'provedor':'skyone','skyone_webhook_url':URL,'skyone_auth_tipo':'basic',
        'skyone_auth_usuario':'flow-fixture@luxtia','skyone_auth':'senha-fixture'})
    assert r.status_code==200,r.text
    assert (await c.get(f'/admin/v1/tenants/{tenant}/agentes/{a}')).json()['configuracao_efetiva']['agente_mode']=='mock'
    return a


def test_testar_com_provedor_real_sem_mudar_o_agente(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a=await agente_skyone_em_mock(c)
            url=f'/admin/v1/tenants/alpha/agentes/{a}'
            chamadas,espiao=montar(app,monkeypatch)
            antes=(await c.get(url)).json()
            # Sem a opção: mock, nenhuma chamada externa.
            r=await c.post(url+'/testar')
            assert r.status_code==200 and r.json()['resposta']==['Resposta simulada.'] and not r.json()['provedor_real']
            assert chamadas==[] and espiao.urls==[]
            # Com a opção: uma chamada real, Basic no header, cota pela URL do agente.
            r=await c.post(url+'/testar',json={'usar_provedor_real':True})
            assert r.status_code==200,r.text
            assert r.json()['resposta']==['ok do fluxo'] and r.json()['provedor_real'] is True
            assert len(chamadas)==1 and str(chamadas[0].url)==URL
            assert chamadas[0].headers['Authorization']=='Basic '+base64.b64encode(b'flow-fixture@luxtia:senha-fixture').decode()
            assert espiao.urls==[URL] and espiao.terminados==[None]
            # O agente continua igual, inclusive em mock.
            assert (await c.get(url)).json()==antes
            acoes=[x['acao'] for x in (await c.get('/admin/v1/tenants/alpha/auditoria')).json()['itens']]
            assert 'agente.testado_provedor_real' in acoes and 'agente.testado' in acoes
            assert 'senha-fixture' not in r.text
            # Isolamento: outro tenant não alcança o agente.
            await c.post('/admin/v1/tenants',json={'id':'bravo','nome':'bravo'})
            r=await c.post(f'/admin/v1/tenants/bravo/agentes/{a}/testar',json={'usar_provedor_real':True})
            assert r.status_code==404 and len(chamadas)==1
            await app.state.http.aclose()
    banco(cenario)


def test_testar_provedor_real_recusado_e_credencial_incompleta(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            chamadas,_=montar(app,monkeypatch)
            a,_=await criar(c)
            url=f'/admin/v1/tenants/alpha/agentes/{a}'
            def campo(r):return r.status_code,r.json()['erro']['codigo'],r.json()['erro']['detalhes'].get('campo')
            r=await c.post(url+'/testar',json={'usar_provedor_real':True})
            assert campo(r)==(400,'VALIDATION_ERROR','provedor')
            await c.patch(url,json={'provedor':'skyone'})
            r=await c.post(url+'/testar',json={'usar_provedor_real':True})
            assert campo(r)==(400,'VALIDATION_ERROR','skyone_webhook_url')
            await c.patch(url,json={'skyone_webhook_url':URL})
            # Sem autenticação, fora de empresa de teste: falta a credencial (SEG13).
            r=await c.post(url+'/testar',json={'usar_provedor_real':True})
            assert campo(r)==(400,'VALIDATION_ERROR','skyone_auth')
            r=await c.post(url+'/testar',json={'usar_provedor_real':'sim'})
            assert campo(r)==(400,'VALIDATION_ERROR','usar_provedor_real')
            assert chamadas==[]
            # Empresa de teste aceita sem autenticação.
            await c.patch('/admin/v1/tenants/alpha',json={'tenant_teste':True})
            r=await c.post(url+'/testar',json={'usar_provedor_real':True})
            assert r.status_code==200,r.text
            assert len(chamadas)==1 and 'Authorization' not in chamadas[0].headers
            await app.state.http.aclose()
        app2,c2=preparar(conn)
        async with c2:
            a2=await agente_skyone_em_mock(c2,'carlos')
            montar(app2,monkeypatch,httpx.Response(401))
            r=await c2.post(f'/admin/v1/tenants/carlos/agentes/{a2}/testar',json={'usar_provedor_real':True})
            assert r.status_code==502 and r.json()['erro']['codigo']=='CREDENTIAL_REJECTED'
            acoes=[x['acao'] for x in (await c2.get('/admin/v1/tenants/carlos/auditoria')).json()['itens']]
            assert 'agente.teste_provedor_real_falhou' in acoes
            assert (await c2.get(f'/admin/v1/tenants/carlos/agentes/{a2}')).json()['configuracao_efetiva']['agente_mode']=='mock'
            await app2.state.http.aclose()
    banco(cenario)
