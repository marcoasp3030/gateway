"""Fase 0.6, bloco 2: fila durável, workers, agendador, exclusividade e item A."""
import asyncio,json,os,time,uuid
import asyncpg,httpx,pytest
import redis.asyncio as redis
from cryptography.fernet import Fernet
from plataforma import Infra,criar_app
from agentes.erros import TempoEsgotado
from agentes.controle import CircuitoAberto
from nucleo.configuracao import FALLBACK_PADRAO
from nucleo.fila import FilaDuravel
from nucleo.pipeline import Pipeline
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
from test_pipeline_multi import RedisFila,ativo
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


def banco_real(cenario):
    """Pool real (várias conexões) num schema temporário do banco de teste, apagado no fim.
    Os testes da fila rodam workers e agendador ao mesmo tempo."""
    async def run():
        schema='fila_'+uuid.uuid4().hex
        admin=await asyncpg.connect(DSN)
        await admin.execute(f'CREATE SCHEMA {schema}')
        pool=await asyncpg.create_pool(DSN,min_size=2,max_size=12,server_settings={'search_path':f'{schema},public'})
        try:
            async with pool.acquire() as c: await migrar.aplicar(c)
            await cenario(pool)
        finally:
            await pool.close()
            await admin.execute(f'DROP SCHEMA {schema} CASCADE');await admin.close()
    asyncio.run(run())


async def montar(conn,ligada,tmp_path,skyone_rpm=240,**fila_kw):
    r=redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
    infra=Infra('a'*32,'b'*32,'c'*32,Fernet.generate_key().decode())
    app=criar_app(infra,conn,r)
    c=httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url='http://teste')
    c.headers.update({'Authorization':'Bearer '+infra.panel_key,'X-Actor':'teste'})
    pref='teste:'+uuid.uuid4().hex
    p=Pipeline(app.state.db,r,app.state.configuracoes,app.state.cofre,app.state.http,prefixo=pref,
               skyone_rpm=skyone_rpm,fila_duravel=ligada)
    app.state.pipeline=p;app.state.redis=r
    kw=dict(concorrencia=5,prefixo=pref,lider_ttl_ms=1000,ocioso_ms=200,arquivo_vivo=tmp_path/'vivo');kw.update(fila_kw)
    app.state.fila=FilaDuravel(p,ligada,**kw)
    return app,c,r,p,pref


async def limpar(r,pref):
    async for k in r.scan_iter(match=pref+':*'): await r.delete(k)
    await r.aclose()


async def conversas(c,tenants=('alpha','bravo'),por=3,identificador='5511900000'):
    convs=[]
    for t in tenants:
        a,can=await criar(c,t)
        await ativo(c,t,a,can,debounce_s=1,max_wait_s=1)
        for i in range(por):
            r=await c.post(f'/admin/v1/tenants/{t}/agentes/{a}/simular',json={'canal_id':can,'identificador':f'{identificador}{i}','texto':'oi'})
            assert r.status_code==200,r.text;convs.append((t,r.json()['conversation_id']))
    return convs


async def esperar_processadas(conn,limite=20):
    fim=time.monotonic()+limite
    while time.monotonic()<fim:
        if not await conn.fetchval("SELECT count(*) FROM mensagens WHERE processada_em IS NULL AND direcao='entrada'"): return True
        await asyncio.sleep(.2)
    return False


async def parar(*tarefas):
    for t in tarefas: t.cancel()
    await asyncio.gather(*tarefas,return_exceptions=True)


@pytest.mark.parametrize('posicao',['desligada','ligada','divergente'])
def test_exclusividade_nunca_processa_a_mesma_conversa_duas_vezes(posicao,tmp_path):
    """Em cada posição da chave, o lado que deve ficar parado roda sozinho, com tudo vencido,
    e não processa nada; depois o outro lado processa cada lote exatamente uma vez.
    Na posição divergente (configuração errada), os dois rodam juntos e ainda assim não duplicam."""
    async def cenario(conn):
        ligada=posicao!='desligada'
        app,c,r,p,pref=await montar(conn,posicao=='ligada',tmp_path)
        novos=[FilaDuravel(p,ligada,concorrencia=5,prefixo=pref,lider_ttl_ms=1000,ocioso_ms=200,arquivo_vivo=tmp_path/f'v{i}') for i in range(2)]
        def caminho_novo():
            return [asyncio.create_task(novos[0].agendador(intervalo=.05)),
                    *[asyncio.create_task(f.worker(bloqueio_ms=100)) for f in novos]]
        pendentes="SELECT count(*) FROM mensagens WHERE processada_em IS NULL AND direcao='entrada'"
        try:
            async with c:
                convs=await conversas(c)
                await asyncio.sleep(1.3)  # todos os lotes já vencidos
                if posicao=='desligada':
                    parados=caminho_novo();await asyncio.sleep(1.5)
                    assert await conn.fetchval(pendentes)==len(convs)  # workers e agendador novos não consumiram nada
                    assert not await r.exists(novos[0].stream('alpha'),novos[0].stream('bravo'))
                    ativos=[asyncio.create_task(p.agendador())]
                elif posicao=='ligada':
                    parados=[asyncio.create_task(p.agendador())];await asyncio.sleep(1.5)
                    assert await conn.fetchval(pendentes)==len(convs)  # caminho atual não processou nada
                    ativos=caminho_novo()
                else:
                    parados=[];ativos=[asyncio.create_task(p.agendador()),*caminho_novo()]
                assert await esperar_processadas(conn)
                await asyncio.sleep(.5)
                await parar(*parados,*ativos)
            for t,conv in convs:
                assert await conn.fetchval("SELECT count(*) FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 AND etapa='concluido'",t,conv)==1,conv
                assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida'",t,conv)==1,conv
            novos_turnos=await conn.fetchval("SELECT count(*) FROM turnos WHERE mensagem_ids IS NOT NULL")
            if posicao=='desligada': assert novos_turnos==0
            if posicao=='ligada': assert novos_turnos==len(convs)
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_INV01_retoma_depois_da_resposta_sem_chamar_o_agente_de_novo(tmp_path,monkeypatch):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path)
        try:
            async with c:
                (t,conv),=await conversas(c,('alpha',),1)
            chamadas=[]
            original=p.chamar
            async def contar(*a,**k):
                chamadas.append(1);return await original(*a,**k)
            p.chamar=contar
            enviar=p._enviar_partes
            async def cair(*a,**k): raise RuntimeError('worker caiu depois da resposta')
            p._enviar_partes=cair
            with pytest.raises(RuntimeError): await p.processar_duravel(t,conv)
            assert await conn.fetchval("SELECT etapa FROM turnos WHERE conversation_id=$1",conv)=='respondido'
            p._enviar_partes=enviar
            assert await p.processar_duravel(t,conv)=='concluido'
            assert len(chamadas)==1  # o agente não foi chamado de novo
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND direcao='saida'",conv)==1
            assert await conn.fetchval("SELECT etapa FROM turnos WHERE conversation_id=$1",conv)=='concluido'
            assert not await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND processada_em IS NULL",conv)
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_INV01_parte_presa_em_enviando_nunca_e_reenviada(tmp_path):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path)
        try:
            async with c:
                (t,conv),=await conversas(c,('alpha',),1)
                enviar=p._enviar_partes
                async def cair_no_meio(tenant,conversa,cfg,turno):
                    # Worker caiu depois de gravar 'enviando' e antes de confirmar.
                    await conn.execute("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,turno_request_id,parte,entrega)
                        VALUES ($1,$2,'saida','bot','Resposta simulada.',now(),$3,1,'enviando')""",tenant,conversa,turno['request_id'])
                    raise RuntimeError('caiu')
                p._enviar_partes=cair_no_meio
                with pytest.raises(RuntimeError): await p.processar_duravel(t,conv)
                p._enviar_partes=enviar
                assert await p.processar_duravel(t,conv)=='concluido'
                saidas=await conn.fetch("SELECT entrega FROM mensagens WHERE conversation_id=$1 AND direcao='saida'",conv)
                assert [s['entrega'] for s in saidas]==['incerta']  # não reenviou
                linha=(await c.get('/admin/v1/tenants/alpha/conversas',params={'aguardando':True})).json()['itens']
                assert [x['conversation_id'] for x in linha]==[conv] and linha[0]['aguardando_motivo']=='entrega_incerta'
                tipos=[e['tipo'] for e in (await c.get('/admin/v1/tenants/alpha/eventos',params={'limite':100})).json()['itens']]
                assert 'mensagem.entrega_incerta' in tipos and 'conversa.aguardando_atendente' in tipos
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_INV14_redis_perde_a_fila_e_a_reconciliacao_recupera(tmp_path):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path)
        fila=app.state.fila
        try:
            async with c:
                convs=await conversas(c)
                await asyncio.sleep(1.2)
                assert await fila.mover_vencidos()==len(convs)
                # Perda total do que estava no Redis (fila, agendamento e marcações).
                async for k in r.scan_iter(match=pref+':*'): await r.delete(k)
                fila.grupos.clear()
                assert await fila.reconciliar()==len(convs)
                tarefas=[asyncio.create_task(fila.agendador(intervalo=.05)),asyncio.create_task(fila.worker(bloqueio_ms=100))]
                assert await esperar_processadas(conn)
                await parar(*tarefas)
            for t,conv in convs:
                assert await conn.fetchval("SELECT count(*) FROM turnos WHERE conversation_id=$1 AND etapa='concluido'",conv)==1
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_INV14_worker_morto_tem_o_item_retomado_por_outro(tmp_path):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path)
        fila=app.state.fila
        try:
            async with c:
                (t,conv),=await conversas(c,('alpha',),1)
                await asyncio.sleep(1.2);await fila.mover_vencidos()
                # Um worker leu o item e morreu sem confirmar.
                lido=await r.xreadgroup('workers','morto',{fila.stream('alpha'):'>'},count=1)
                assert lido and await r.xpending(fila.stream('alpha'),'workers')
                outro=FilaDuravel(p,True,concorrencia=5,prefixo=pref,ocioso_ms=200,arquivo_vivo=tmp_path/'o')
                tarefa=asyncio.create_task(outro.worker(bloqueio_ms=100))
                assert await esperar_processadas(conn)
                await asyncio.sleep(.3);await parar(tarefa)
            assert await conn.fetchval("SELECT count(*) FROM turnos WHERE conversation_id=$1 AND etapa='concluido'",conv)==1
            assert (await r.xpending(fila.stream('alpha'),'workers'))['pending']==0
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_lideranca_reserva_assume_em_poucos_segundos_sem_duplicar(tmp_path):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path)
        a=FilaDuravel(p,True,prefixo=pref,lider_ttl_ms=1000,arquivo_vivo=tmp_path/'a')
        b=FilaDuravel(p,True,prefixo=pref,lider_ttl_ms=1000,arquivo_vivo=tmp_path/'b')
        try:
            async with c:
                convs=await conversas(c)
            ta=asyncio.create_task(a.agendador(intervalo=.05));await asyncio.sleep(.3)
            tb=asyncio.create_task(b.agendador(intervalo=.05));await asyncio.sleep(.3)
            assert await r.get(a.k_lider)==a.id and a.lider and not b.lider
            # Principal para sem soltar a trava (processo morto).
            ta.cancel();await asyncio.gather(ta,return_exceptions=True)
            inicio=time.monotonic()
            while await r.get(a.k_lider)!=b.id and time.monotonic()-inicio<5: await asyncio.sleep(.1)
            assert await r.get(a.k_lider)==b.id and time.monotonic()-inicio<3
            await asyncio.sleep(1.5)  # vencidos movidos pelo novo líder
            await parar(tb)
            total=sum([await r.xlen(a.stream(t)) for t in ('alpha','bravo')])
            assert total==len(convs)  # cada conversa entrou na fila uma única vez
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_indicadores_zerados_com_a_fila_desligada_e_rotas(tmp_path):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,False,tmp_path)
        try:
            async with c:
                await criar(c,'alpha');await criar(c,'bravo')
                ind=await app.state.fila.indicadores()
                assert ind['ligada'] is False and ind['aguardando']==0 and ind['mais_antiga_s']==0
                assert ind['empresas']=={} and ind['agentes_em_andamento']=={}
                interno=(await c.get('/health')).json()
                assert interno['fila']['ligada'] is False and interno['fila']['aguardando']==0
                publico=(await c.get('/health',headers={'X-Forwarded-For':'1.2.3.4'})).json()
                assert publico=={'ok':True}
                assert (await c.get('/admin/v1/fila')).json()['aguardando']==0
                assert (await c.get('/admin/v1/tenants/alpha/fila')).json()=={'ligada':False,'aguardando':0,'em_processamento':0,'mais_antiga_s':0.0,'agentes_em_andamento':{}}
                assert (await c.get('/admin/v1/tenants/alpha/metricas')).json()['fila']['aguardando']==0
                assert (await c.get('/admin/v1/tenants/ausente/fila')).status_code==404
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_indicadores_com_fila_e_alertas_agrupados(tmp_path):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path,skyone_rpm=2,alerta_fila_s=.2,alerta_cota_s=0)
        fila=app.state.fila
        try:
            async with c:
                convs=await conversas(c,('alpha','bravo'),2)
                await asyncio.sleep(1.2);await fila.mover_vencidos();await asyncio.sleep(.4)
                ind=await fila.indicadores()
                assert ind['empresas']['alpha']['aguardando']==2 and ind['empresas']['bravo']['aguardando']==2
                assert ind['mais_antiga_s']>=.2
                # Isolamento: a empresa só vê a própria fila.
                assert (await c.get('/admin/v1/tenants/alpha/fila')).json()['aguardando']==2
                url='https://skyone.example/fluxo'
                await p.controle.esperar(url);await p.controle.esperar(url)
                ind=await fila.indicadores()
                assert ind['cota_skyone'][0]['percentual']==1.0 and 'skyone.example' not in json.dumps(ind)
                await fila.alertas(ind);await fila.alertas(await fila.indicadores())
                assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='fila.atrasada' AND tenant_id='alpha'")==1
                assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='skyone.cota_alta' AND tenant_id IS NULL")==1
        finally:
            await limpar(r,pref)
    banco_real(cenario)


@pytest.mark.parametrize('falha,motivo',[(TempoEsgotado(),'tempo_esgotado'),(CircuitoAberto(),'circuito_aberto'),(RuntimeError('x'),'erro_agente')])
def test_item_A_fallback_novo_e_aguardando_atendente_no_caminho_atual(falha,motivo):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async def falhar(*a,**k): raise falha
        app.state.pipeline.chamar=falhar
        async with c:
            a,can=await criar(c);await ativo(c,'alpha',a,can)
            conv=(await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':'5511999990000','texto':'oi'})).json()['conversation_id']
            await app.state.pipeline.processar('alpha',conv)
            saida=await conn.fetchval("SELECT conteudo FROM mensagens WHERE conversation_id=$1 AND direcao='saida'",conv)
            assert saida==FALLBACK_PADRAO=='Tive uma instabilidade agora. Pode repetir sua mensagem em instantes? 🙏'
            itens=(await c.get('/admin/v1/tenants/alpha/conversas',params={'aguardando':True})).json()['itens']
            assert [(x['conversation_id'],x['aguardando_motivo']) for x in itens]==[(conv,motivo)] and itens[0]['aguardando_atendente_desde']
            eventos=(await c.get('/admin/v1/tenants/alpha/eventos',params={'limite':100})).json()['itens']
            assert [e['alvo'] for e in eventos if e['tipo']=='conversa.aguardando_atendente']==[conv]
            # Atendente assumiu: sai da lista de aguardando.
            assert (await c.post(f'/admin/v1/tenants/alpha/conversas/{conv}/assumir')).status_code==200
            assert (await c.get('/admin/v1/tenants/alpha/conversas',params={'aguardando':True})).json()['itens']==[]
            assert (await c.get('/admin/v1/tenants/alpha/conversas',params={'aguardando':False})).json()['itens'][0]['aguardando_motivo'] is None
    banco(cenario)


def test_015_troca_so_os_textos_antigos_com_auditoria(tmp_path):
    for arquivo in migrar.PASTA.glob('*.sql'):
        if arquivo.name < '015': (tmp_path/arquivo.name).write_bytes(arquivo.read_bytes())
    async def cenario(conn):
        await migrar.aplicar(conn,tmp_path)
        await conn.execute("INSERT INTO tenants(id,nome) VALUES ('bravo','Bravo')")
        await conn.execute("INSERT INTO tenant_config(tenant_id,fallback) VALUES ('bravo','Tive uma instabilidade. Por favor, tente novamente.')")
        await conn.execute("""INSERT INTO agentes(id,tenant_id,modelo_id,nome,fallback) VALUES
            ('ag_antigo','bravo','assistente_generico','A','Tive uma instabilidade agora. Já vou te responder, tudo bem? 🙏'),
            ('ag_proprio','bravo','assistente_generico','B','Texto da casa')""")
        await migrar.aplicar(conn)
        f=dict(await conn.fetch("SELECT id,fallback FROM agentes WHERE tenant_id='bravo'"))
        assert f=={'ag_antigo':FALLBACK_PADRAO,'ag_proprio':'Texto da casa'}
        assert await conn.fetchval("SELECT fallback FROM tenant_config WHERE tenant_id='bravo'")==FALLBACK_PADRAO
        audit={(r['acao'],r['alvo']) for r in await conn.fetch("SELECT acao,alvo FROM audit_log WHERE actor='migracao015'")}
        assert audit=={('agentes.alterado','ag_antigo'),('config.alterada','bravo')}
    banco(cenario)
