import asyncio,base64,json,os,uuid,time
import httpx,pytest
import redis.asyncio as redis
from agentes import skyone
from agentes.controle import Controle,CredencialRecusada,CircuitoAberto
from test_admin import preparar,criar,Pool
from test_migrations import banco,migrar,DSN
from test_skyone_adaptador import preparar_agente

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


def test_SEG13_basic_criptografado_api_e_header(monkeypatch,caplog):
    async def dns(*args):pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',dns)
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,ca=await criar(c)
            url=f'/admin/v1/tenants/alpha/agentes/{a}'
            cfg={'skyone_auth_tipo':'basic','skyone_webhook_url':'https://skyone.example/flow','provedor':'skyone','agente_mode':'live'}
            for extra in ({},{'skyone_auth':'senha-fixture'},{'skyone_auth_usuario':'usuario-fixture'}):
                assert (await c.patch(url,json={**cfg,**extra})).status_code==400
            cfg.update(skyone_auth='senha-fixture',skyone_auth_usuario='usuario-fixture')
            r=await c.patch(url,json=cfg);assert r.status_code==200,r.text
            assert r.json()['skyone_auth_usuario_configurado']
            for campo in ('skyone_auth','skyone_auth_usuario'):
                assert (await c.patch(url,json={campo:None})).status_code==400
            async def responder(req):
                assert req.headers['Authorization']=='Basic '+base64.b64encode(b'usuario-fixture:senha-fixture').decode()
                assert b'senha-fixture' not in req.content and b'usuario-fixture' not in req.content
                p=json.loads(req.content)
                return httpx.Response(200,json={**p,'resposta':'ok','status':'success','tipo':'text','acao':'nenhuma'})
            app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(responder))
            # Controle de admissão separado, coberto com Redis real no teste abaixo.
            app.state.pipeline.chamar=skyone.chamar
            r=await c.post(url+'/testar');assert r.status_code==200,r.text
            for path in (url,'/admin/v1/tenants/alpha/agentes','/admin/v1/tenants/alpha/auditoria'):
                text=(await c.get(path)).text
                assert 'senha-fixture' not in text and 'usuario-fixture' not in text
            stored=await conn.fetchrow('SELECT skyone_auth_enc,skyone_auth_usuario_enc FROM agentes WHERE tenant_id=$1 AND id=$2','alpha',a)
            assert 'senha-fixture' not in str(stored) and 'usuario-fixture' not in str(stored)
            assert 'senha-fixture' not in caplog.text and 'usuario-fixture' not in caplog.text
            await app.state.http.aclose()
    banco(cenario)


def test_limite_url_compartilhada_redis_e_timeout():
    async def cenario(conn):
        r=redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
        ctrl=Controle(Pool(conn),r,limite=2,prefixo='teste:'+uuid.uuid4().hex,janela_ms=300)
        url='https://skyone.example/fluxo';keys=[ctrl.chave_url(url),ctrl.chave_url(url+'2')]
        try:
            # Agentes distintos compartilham a mesma identidade URL; nenhuma chave contém agente/tenant.
            assert ctrl.chave_url(url)==ctrl.chave_url('https://SKYONE.example:443/fluxo')
            await asyncio.gather(ctrl.esperar(url),ctrl.esperar(url))
            with pytest.raises(TimeoutError):await asyncio.wait_for(ctrl.esperar(url),.02)
            await asyncio.wait_for(ctrl.esperar(url+'2'),.1)
            inicio=time.monotonic();await ctrl.esperar(url)
            assert time.monotonic()-inicio>=.15
        finally:
            await r.delete(*keys);await r.aclose()
    banco(cenario)


@pytest.mark.parametrize('status',[401,403])
def test_credencial_recusada_alerta_circuito_sem_retry(monkeypatch,status):
    async def dns(*args):pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',dns)
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'oi',cfg)
        ctrl=Controle(Pool(conn),None)
        async def esperar(url):pass
        ctrl.esperar=esperar
        calls=[]
        def responder(req):calls.append(1);return httpx.Response(status,text='credencial-fixture')
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            for n in range(3):
                with pytest.raises(CredencialRecusada,match='credencial do Skyone recusada'):
                    await skyone.chamar(client,repo,cofre,'bykoji',cfg,body,controle=ctrl)
                assert len(calls)==n+1
            with pytest.raises(CircuitoAberto):await skyone.chamar(client,repo,cofre,'bykoji',cfg,body,controle=ctrl)
            assert len(calls)==3
        assert await conn.fetchval("SELECT falhas FROM agente_circuitos WHERE tenant_id='bykoji' AND agente_id=$1",cfg['id'])==3
        assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id='bykoji' AND acao='agente.credencial_recusada'")==1
        await conn.execute("UPDATE agente_circuitos SET aberto_ate=now()-interval '1 second' WHERE tenant_id='bykoji' AND agente_id=$1",cfg['id'])
        generation=await ctrl.iniciar('bykoji',cfg['id'],90)
        with pytest.raises(CircuitoAberto):await ctrl.iniciar('bykoji',cfg['id'],90)
        await ctrl.terminar('bykoji',cfg['id'],generation)
        assert await conn.fetchval("SELECT aberto_ate IS NULL FROM agente_circuitos WHERE tenant_id='bykoji' AND agente_id=$1",cfg['id'])
    banco(cenario)


def test_limite_espera_sem_erro_e_timeout_com_fallback(monkeypatch):
    from test_pipeline_multi import RedisFila,ativo
    async def dns(*args):pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',dns)
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        app.state.redis=RedisFila();app.state.pipeline.redis=app.state.redis
        async with c:
            a,can=await criar(c)
            await ativo(c,'alpha',a,can,provedor='skyone',agente_mode='live',send_mode='dry',timeout_s=1,
                skyone_auth_tipo='basic',skyone_auth_usuario='flow-fixture@luxtia',skyone_auth='senha-fixture',
                skyone_webhook_url='https://skyone.example/compartilhado')
            calls=[]
            def responder(req):
                calls.append(1);p=json.loads(req.content)
                return httpx.Response(200,json={**p,'status':'success','tipo':'text','resposta':'resposta-fixture','acao':'nenhuma'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as http:
                app.state.pipeline.http=http
                async def vaga(url):await asyncio.sleep(.02)
                app.state.pipeline.controle.esperar=vaga
                conv=(await app.state.pipeline.receber('alpha',can,{'identificador':'espera','texto':'oi'}))['conversation_id']
                await app.state.pipeline.processar('alpha',conv)
                assert await conn.fetchval("SELECT status FROM turnos WHERE tenant_id='alpha' AND conversation_id=$1",conv)=='ok'
                async def sem_vaga(url):await asyncio.sleep(10)
                app.state.pipeline.controle.esperar=sem_vaga
                conv2=(await app.state.pipeline.receber('alpha',can,{'identificador':'timeout','texto':'oi'}))['conversation_id']
                await app.state.pipeline.processar('alpha',conv2)
                assert len(calls)==1
                cfg=await app.state.configuracoes.efetiva('alpha',a)
                assert await conn.fetchval("SELECT conteudo FROM mensagens WHERE tenant_id='alpha' AND conversation_id=$1 AND origem='bot'",conv2)==cfg['fallback']
                assert await conn.fetchval("SELECT falhas FROM agente_circuitos WHERE tenant_id='alpha' AND agente_id=$1",a)==0
                # A recusa é clara no teste administrativo e aparece no polling e no circuito.
                app.state.pipeline.controle.esperar=vaga
                async def recusar(*args):raise CredencialRecusada()
                monkeypatch.setattr(skyone,'_chamar',recusar)
                r=await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/testar')
                assert r.status_code==502 and 'credencial do Skyone recusada' in r.text
                state=(await c.get(f'/admin/v1/tenants/alpha/agentes/{a}/circuito')).json()
                assert state['falhas']==1
                assert (await c.get(f'/admin/v1/tenants/bravo/agentes/{a}/circuito')).status_code==404
    banco(cenario)
