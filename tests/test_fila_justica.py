"""Fase 0.6, bloco 3: justiça entre empresas (INV07), circuito e nova tentativa (INV15), validações."""
import asyncio,json,time
from functools import partial
import httpx,pytest
from agentes import skyone
from nucleo.fila import FilaDuravel
from test_admin import preparar,criar
from test_fila import banco_real,montar,limpar,conversas,esperar_processadas,parar
from test_migrations import banco,migrar,DSN
from test_pipeline_multi import ativo
from test_testar_provedor_real import Espiao,URL
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


def agente_lento(p,segundos=.4):
    """Agente simulado lento que registra quantas conversas de cada empresa/agente estão em andamento."""
    andamento={};pico={};fim={}
    async def chamar(http,repo,cofre,tenant,cfg,payload):
        for k in (tenant,(tenant,cfg['id'])):
            andamento[k]=andamento.get(k,0)+1;pico[k]=max(pico.get(k,0),andamento[k])
        try: await asyncio.sleep(segundos)
        finally:
            for k in (tenant,(tenant,cfg['id'])): andamento[k]-=1
        fim.setdefault(tenant,[]).append(time.monotonic())
        return {'resposta':['Resposta simulada.'],'acao':'nenhuma'}
    p.chamar=chamar
    return pico,fim


def test_INV07_pico_de_uma_empresa_nao_atrasa_a_outra(tmp_path):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path,concorrencia=20)
        outro=FilaDuravel(p,True,concorrencia=20,prefixo=pref,arquivo_vivo=tmp_path/'w2')
        pico,fim=agente_lento(p)
        try:
            async with c:
                grandes=await conversas(c,('alpha',),50)
                (_,pequena),=await conversas(c,('bravo',),1)
                await asyncio.sleep(1.3)  # tudo vencido ao mesmo tempo: 50 da alpha na frente
                tarefas=[asyncio.create_task(app.state.fila.agendador(intervalo=.05)),
                         asyncio.create_task(app.state.fila.worker(bloqueio_ms=100)),asyncio.create_task(outro.worker(bloqueio_ms=100))]
                assert await esperar_processadas(conn,60)
                await asyncio.sleep(.3);await parar(*tarefas)
            # A única conversa da bravo foi atendida junto com a primeira leva da alpha.
            antes_da_bravo=sum(1 for t in fim['alpha'] if t<fim['bravo'][0])
            assert antes_da_bravo<=10,antes_da_bravo
            # Limite da empresa (5) respeitado somando os dois workers.
            assert pico['alpha']<=5 and pico['alpha']>=4,pico
            assert await conn.fetchval("SELECT count(*) FROM turnos WHERE etapa='concluido'")==51
            assert await conn.fetchval("SELECT count(*) FROM (SELECT conversation_id FROM turnos WHERE etapa='concluido' GROUP BY 1 HAVING count(*)>1) x")==0
            assert not await r.exists(app.state.fila.k_lotes('alpha'))  # vagas devolvidas
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_INV15_limite_por_agente_compartilhado_entre_workers(tmp_path):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path,concorrencia=20)
        outro=FilaDuravel(p,True,concorrencia=20,prefixo=pref,arquivo_vivo=tmp_path/'w2')
        pico,fim=agente_lento(p)
        try:
            async with c:
                a1,can1=await criar(c,'alpha')
                a2=(await c.post('/admin/v1/tenants/alpha/agentes',json={'nome':'Outro','modelo_id':'assistente_generico'})).json()['id']
                can2=(await c.post('/admin/v1/tenants/alpha/canais',json={'nome':'C2','tipo':'api','agente_id':a2})).json()['id']
                await ativo(c,'alpha',a1,can1,debounce_s=1,max_wait_s=1,max_concorrencia=2,max_lotes_simultaneos=10)
                await ativo(c,'alpha',a2,can2,debounce_s=1,max_wait_s=1,max_lotes_simultaneos=10)
                for i in range(8):
                    assert (await c.post(f'/admin/v1/tenants/alpha/agentes/{a1}/simular',json={'canal_id':can1,'identificador':f'55119000{i}','texto':'oi'})).status_code==200
                assert (await c.post(f'/admin/v1/tenants/alpha/agentes/{a2}/simular',json={'canal_id':can2,'identificador':'5511888','texto':'oi'})).status_code==200
                await asyncio.sleep(1.3)
                tarefas=[asyncio.create_task(app.state.fila.agendador(intervalo=.05)),
                         asyncio.create_task(app.state.fila.worker(bloqueio_ms=100)),asyncio.create_task(outro.worker(bloqueio_ms=100))]
                assert await esperar_processadas(conn,60)
                await parar(*tarefas)
            assert pico[('alpha',a1)]==2,pico   # nunca mais que o limite do agente
            assert pico[('alpha',a2)]==1
            assert await conn.fetchval("SELECT count(*) FROM turnos WHERE etapa='concluido'")==9
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def preparar_skyone(app,monkeypatch,respostas):
    """Agente Skyone com respostas HTTP em sequência; Response ou exceção por chamada."""
    async def dns(*args):pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',dns)
    chamadas=[]
    async def responder(req):
        chamadas.append(req);r=respostas[min(len(chamadas),len(respostas))-1]
        if isinstance(r,Exception): raise r
        if r==200:
            p=json.loads(req.content)
            return httpx.Response(200,json={**p,'resposta':'ok','status':'success','tipo':'text','acao':'nenhuma'})
        return httpx.Response(r)
    app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(responder))
    espiao=Espiao();app.state.pipeline.chamar=partial(skyone.chamar,controle=espiao)
    return chamadas,espiao


@pytest.mark.parametrize('respostas,espera,timeout,status,n_chamadas',[
    ([503,200],.01,60,200,2),                                   # transitório → nova tentativa resolve
    ([502,504],.01,60,502,2),                                   # só uma nova tentativa
    ([httpx.ConnectError('recusada'),200],.01,60,200,2),        # falha de conexão → nova tentativa
    ([500,200],.01,60,502,1),                                   # erro definitivo: sem nova tentativa
    ([httpx.ReadTimeout('demorou'),200],.01,60,502,1),          # tempo esgotado: nunca tenta de novo
    ([503,200],5,3,502,1),                                      # não cabe no timeout_s: sem nova tentativa
])
def test_INV15_nova_tentativa_unica_so_para_falha_transitoria(monkeypatch,respostas,espera,timeout,status,n_chamadas):
    monkeypatch.setattr(skyone,'ESPERA_NOVA_TENTATIVA_S',espera)
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,_=await criar(c)
            r=await c.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'provedor':'skyone','skyone_webhook_url':URL,
                'skyone_auth_tipo':'basic','skyone_auth_usuario':'flow-fixture@luxtia','skyone_auth':'senha-fixture','timeout_s':timeout})
            assert r.status_code==200,r.text
            chamadas,espiao=preparar_skyone(app,monkeypatch,respostas)
            r=await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/testar',json={'usar_provedor_real':True})
            assert r.status_code==status,r.text
            assert len(chamadas)==n_chamadas
            assert len(espiao.urls)==n_chamadas  # cada tentativa passa pela cota por URL
            assert len(espiao.terminados)==1     # o circuito conta uma chamada, não duas
            assert (espiao.terminados[0] is None)==(status==200)
            await app.state.http.aclose()
    banco(cenario)


def test_INV15_circuito_isola_agente_e_fecha_manualmente(tmp_path,monkeypatch):
    async def dns(*args):pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',dns)
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path)
        http=[]
        async def responder(req):
            http.append(req);raise httpx.ReadTimeout('Skyone fora do ar')
        p.http=httpx.AsyncClient(transport=httpx.MockTransport(responder))
        try:
            async with c:
                a,can=await criar(c,'alpha')
                await ativo(c,'alpha',a,can,debounce_s=1,max_wait_s=1,max_concorrencia=1)
                r1=await c.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'provedor':'skyone','skyone_webhook_url':URL,
                    'skyone_auth_tipo':'basic','skyone_auth_usuario':'flow-fixture@luxtia','skyone_auth':'senha-fixture','agente_mode':'live','send_mode':'dry'})
                assert r1.status_code==200,r1.text
                for i in range(4):
                    await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':f'551190{i}','texto':'oi'})
                (_,normal),=await conversas(c,('bravo',),1)  # outro agente, em simulação
                await asyncio.sleep(1.3)
                tarefas=[asyncio.create_task(app.state.fila.agendador(intervalo=.05)),asyncio.create_task(app.state.fila.worker(bloqueio_ms=100))]
                assert await esperar_processadas(conn,60)
                await parar(*tarefas)
                # Três falhas abrem o circuito; a quarta conversa recebe o fallback sem chamar o Skyone.
                assert len(http)==3
                erros=sorted(x['erro'] for x in await conn.fetch("SELECT erro FROM turnos WHERE tenant_id='alpha' AND etapa='concluido'"))
                assert erros==['AGENT_TIMEOUT']*3+['SKYONE_CIRCUIT_OPEN']
                motivos=sorted(x['aguardando_motivo'] for x in (await c.get('/admin/v1/tenants/alpha/conversas',params={'aguardando':True})).json()['itens'])
                assert motivos==['circuito_aberto']+['tempo_esgotado']*3
                # O agente da outra empresa respondeu normalmente.
                assert await conn.fetchval("SELECT status FROM turnos WHERE conversation_id=$1",normal)=='ok'
                url=f'/admin/v1/tenants/alpha/agentes/{a}/circuito'
                assert (await c.get(url)).json()['estado']=='aberto'
                assert (await c.post(f'/admin/v1/tenants/bravo/agentes/{a}/circuito/fechar')).status_code==404
                assert (await c.post(url+'/fechar',headers={'X-Forwarded-For':'1.2.3.4'})).status_code==404
                fechado=await c.post(url+'/fechar')
                assert fechado.status_code==200 and fechado.json()['estado']=='fechado' and fechado.json()['falhas']==0
                acoes=[e['tipo'] for e in (await c.get('/admin/v1/tenants/alpha/eventos',params={'limite':100})).json()['itens']]
                assert 'agente.circuito_fechado' in acoes
        finally:
            await p.http.aclose();await limpar(r,pref)
    banco_real(cenario)


def test_filtro_aguardando_invalido_e_fallback_validado():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        def campo(r): return r.status_code,r.json()['erro']['detalhes'].get('campo')
        async with c:
            a,_=await criar(c)
            assert campo(await c.get('/admin/v1/tenants/alpha/conversas',params={'aguardando':'talvez'}))==(400,'aguardando')
            url=f'/admin/v1/tenants/alpha/agentes/{a}'
            for ruim in ('/','curto demais','123456789012345678','               x'):
                assert campo(await c.patch(url,json={'fallback':ruim}))==(400,'fallback'),ruim
                assert campo(await c.put('/admin/v1/tenants/alpha/config',json={'fallback':ruim}))==(400,'fallback'),ruim
            bom='Tive um problema. Pode repetir, por favor?'
            assert (await c.patch(url,json={'fallback':bom})).json()['fallback']==bom
            # Vazio ou nulo = usa o padrão (herda).
            for vazio in ('','   ',None):
                assert (await c.patch(url,json={'fallback':vazio})).json()['fallback'] is None
            # Registro existente fora da regra não é alterado nem bloqueia outras edições.
            await conn.execute("UPDATE agentes SET fallback='/' WHERE id=$1",a)
            r=await c.patch(url,json={'aviso_demora':'Um instante'})
            assert r.status_code==200 and r.json()['fallback']=='/'
    banco(cenario)
