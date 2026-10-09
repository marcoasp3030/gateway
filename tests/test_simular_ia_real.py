"""/simular com IA real quando o envio está em teste (dry); envio real continua recusado."""
import json
from functools import partial
import httpx,pytest
from agentes import skyone
from test_admin import preparar,criar
from test_gestao import fila
from test_migrations import banco,migrar,DSN
from test_pipeline_multi import ativo
from test_testar_provedor_real import Espiao,URL
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


def test_simular_com_ia_real_em_dry_e_recusa_envio_real(monkeypatch):
    async def dns(*args):pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',dns)
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn);fila(app)
        chamadas=[]
        async def responder(req):
            chamadas.append(req);p=json.loads(req.content)
            return httpx.Response(200,json={**p,'resposta':'resposta do fluxo','status':'success','tipo':'text','acao':'nenhuma'})
        app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(responder))
        app.state.pipeline.http=app.state.http  # o pipeline guarda o próprio cliente HTTP
        espiao=Espiao();app.state.pipeline.chamar=partial(skyone.chamar,controle=espiao)
        async with c:
            a,can=await criar(c)
            await ativo(c,'alpha',a,can,debounce_s=1,max_wait_s=1)
            r=await c.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={
                'provedor':'skyone','skyone_webhook_url':URL,'skyone_auth_tipo':'basic',
                'skyone_auth_usuario':'flow-fixture@luxtia','skyone_auth':'senha-fixture',
                'agente_mode':'live','send_mode':'dry'})
            assert r.status_code==200,r.text
            r=await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',
                           json={'canal_id':can,'identificador':'marco','texto':'olá'})
            assert r.status_code==200,r.text
            conv=r.json()['conversation_id']
            await app.state.pipeline.processar('alpha',conv)
            assert len(chamadas)==1 and espiao.urls==[URL]
            msgs=(await c.get(f'/admin/v1/tenants/alpha/conversas/{conv}/mensagens')).json()['itens']
            assert [m['direcao'] for m in msgs]==['entrada','saida'] and msgs[-1]['conteudo']=='resposta do fluxo'
            # Envio real: a simulação é recusada e nada é gravado.
            assert (await c.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'send_mode':'live'})).status_code==200
            r=await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',
                           json={'canal_id':can,'identificador':'outro','texto':'olá'})
            assert r.status_code==400 and r.json()['erro']['detalhes']['campo']=='send_mode'
            assert await conn.fetchval("SELECT count(*) FROM conversas WHERE conversation_id LIKE '%:outro'")==0
            await app.state.http.aclose()
    banco(cenario)
