import json
from urllib.parse import urlsplit, parse_qs
import pytest
from test_migrations import banco, migrar, DSN
from test_admin import preparar, criar
from test_pipeline_multi import RedisFila, ativo

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


def fila(app):
    redis=RedisFila();app.state.redis=redis;app.state.pipeline.redis=redis


async def simular(client,t,a,can,identificador='5511999991234'):
    r=await client.post(f'/admin/v1/tenants/{t}/agentes/{a}/simular',json={'canal_id':can,'identificador':identificador,'texto':'olá'})
    assert r.status_code==200,r.text
    return r.json()['conversation_id']


def test_INV08_INV13_testar_contrato_mock_e_erro_sanitizado():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,ca=await criar(c);b,cb=await criar(c,'bravo')
            url=f'/admin/v1/tenants/alpha/agentes/{a}/testar'
            r=await c.post(url)
            assert r.status_code==200,r.text
            assert r.json()['ok'] and r.json()['resposta']==['Resposta simulada.']
            assert r.json()['latencia_ms']>=0
            assert (await c.post(f'/admin/v1/tenants/alpha/agentes/{b}/testar')).status_code==404
            assert (await c.post(url,json={'mensagem':'formato antigo'})).status_code==400
            chamadas=[]
            async def chamada(http,repo,cofre,t,cfg,payload):
                chamadas.append(payload)
                assert payload['message']=='teste do painel' and payload['historico']==[]
                assert payload['session_id']==payload['request_id']
                assert payload['tenant_id']=='alpha' and payload['agente_id']==a
                assert 'token' not in payload
                raise RuntimeError('url?token=segredo-nao-expor')
            app.state.pipeline.chamar=chamada
            r=await c.post(url,json={'message':'teste do painel'})
            assert r.status_code==502 and 'segredo-nao-expor' not in r.text
            assert len(chamadas)==1
            assert await conn.fetchval('SELECT count(*) FROM mensagens')==0
            audit=await conn.fetch('SELECT acao,dados FROM audit_log WHERE tenant_id=$1','alpha')
            assert 'agente.testado' in [r['acao'] for r in audit]
            assert 'agente.teste_falhou' in [r['acao'] for r in audit]
            assert 'segredo-nao-expor' not in str(audit)
    banco(cenario)


def test_INV08_SEG10_rotacao_invalida_segredo_anterior_e_mostra_so_uma_vez():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn);fila(app)
        async with c:
            a,ca=await criar(c);b,cb=await criar(c,'bravo');await ativo(c,'alpha',a,ca)
            oldcipher=await conn.fetchval('SELECT webhook_secret FROM tenant_canais WHERE tenant_id=$1 AND id=$2','alpha',ca)
            old=app.state.cofre.abrir('alpha',oldcipher)
            r=await c.post(f'/admin/v1/tenants/alpha/canais/{ca}/rotacionar-segredo')
            assert r.status_code==200,r.text
            new=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            assert new!=old
            body={'identificador':'5511999991234','texto':'teste'}
            assert (await c.post(f'/webhooks/api/{ca}',params={'s':old},json=body)).status_code==401
            assert (await c.post(f'/webhooks/api/{ca}',params={'s':new},json=body)).status_code==200
            assert (await c.post(f'/admin/v1/tenants/alpha/canais/{cb}/rotacionar-segredo')).status_code==404
            for path in [f'/canais/{ca}','/canais','/auditoria','/eventos']:
                text=(await c.get('/admin/v1/tenants/alpha'+path)).text
                assert new not in text and old not in text
    banco(cenario)


def test_INV08_INV17_lista_conversas_filtros_paginacao():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn);fila(app)
        async with c:
            a,ca=await criar(c);b,cb=await criar(c,'bravo')
            await ativo(c,'alpha',a,ca);await ativo(c,'bravo',b,cb)
            conv=await simular(c,'alpha',a,ca)
            outra=await simular(c,'alpha',a,ca,'5511999995678')
            alheia=await simular(c,'bravo',b,cb)
            await c.post(f'/admin/v1/tenants/alpha/conversas/{conv}/assumir')
            base='/admin/v1/tenants/alpha/conversas'
            r=await c.get(base,params={'agente':a,'canal':ca,'status':'pausada','busca':'1234','desde':'2000-01-01T00:00:00Z'})
            assert r.status_code==200,r.text
            assert [x['conversation_id'] for x in r.json()['itens']]==[conv]
            assert (await c.get(base,params={'status':'ativa'})).json()['itens'][0]['conversation_id']==outra
            first=(await c.get(base,params={'limite':1})).json()
            second=(await c.get(base,params={'limite':1,'cursor':first['proximo_cursor']})).json()
            assert {first['itens'][0]['conversation_id'],second['itens'][0]['conversation_id']}=={conv,outra}
            assert alheia not in str(first)+str(second)
            for params in [{'agente':b},{'canal':cb}]: assert (await c.get(base,params=params)).status_code==404
            for params in [{'status':'invalido'},{'desde':'2026-01-01'},{'limite':0}]: assert (await c.get(base,params=params)).status_code==400
            assert (await c.get('/admin/v1/tenants/ausente/conversas')).status_code==404
    banco(cenario)


def test_INV03_INV08_assumir_durante_agente_devolver_e_mensagem_humana():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn);fila(app)
        async with c:
            a,ca=await criar(c);b,cb=await criar(c,'bravo')
            await ativo(c,'alpha',a,ca);await ativo(c,'bravo',b,cb)
            conv=await simular(c,'alpha',a,ca);alheia=await simular(c,'bravo',b,cb)
            base='/admin/v1/tenants/alpha/conversas/'+conv
            original=app.state.pipeline.chamar
            async def assumir_durante(*args):
                assert (await c.post(base+'/assumir',json={'minutos':30})).status_code==200
                return {'resposta':['não deve sair'],'acao':'nenhuma'}
            app.state.pipeline.chamar=assumir_durante
            await app.state.pipeline.processar('alpha',conv)
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id='alpha' AND origem='bot'")==0
            assert not await app.state.pipeline.permitido('alpha',conv,a)
            assert (await c.post(base+'/devolver')).status_code==200
            assert await app.state.pipeline.permitido('alpha',conv,a)
            app.state.pipeline.chamar=original
            await simular(c,'alpha',a,ca)
            await app.state.pipeline.processar('alpha',conv)
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id='alpha' AND origem='bot'")==1
            r=await c.post(base+'/mensagens',json={'texto':'Atendente aqui'})
            assert r.status_code==201,r.text
            assert r.json()['origem']=='humano' and r.json()['send_mode']=='dry'
            assert not await app.state.pipeline.permitido('alpha',conv,a)
            assert await conn.fetchval("SELECT conteudo FROM mensagens WHERE tenant_id='alpha' AND origem='humano'")=='Atendente aqui'
            for sufixo,body in [('assumir',{'minutos':60}),('devolver',{}),('mensagens',{'texto':'não'})]:
                assert (await c.post('/admin/v1/tenants/alpha/conversas/'+alheia+'/'+sufixo,json=body)).status_code==404
            assert (await c.post(base+'/assumir',json={'minutos':0})).status_code==400
            assert (await c.post(base+'/mensagens',json={'texto':'   '})).status_code==400
    banco(cenario)


def test_INV08_eventos_polling_cursor_mensagens_e_turnos_sem_conteudo():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn);fila(app)
        async with c:
            a,ca=await criar(c);b,cb=await criar(c,'bravo')
            await ativo(c,'alpha',a,ca);await ativo(c,'bravo',b,cb)
            url='/admin/v1/tenants/alpha/eventos'
            inicio=(await c.get(url,params={'limite':100})).json()['proximo_cursor']
            conv=await simular(c,'alpha',a,ca);await app.state.pipeline.processar('alpha',conv)
            outro=await simular(c,'bravo',b,cb);await app.state.pipeline.processar('bravo',outro)
            cursor=inicio;itens=[]
            for _ in range(20):
                r=await c.get(url,params={'desde':cursor,'limite':1});assert r.status_code==200
                page=r.json();itens+=page['itens'];cursor=page['proximo_cursor']
                if not page['tem_mais']:break
            assert {'mensagem.recebida','mensagem.enviada','turno.ok'}.issubset({r['tipo'] for r in itens})
            assert all(r['alvo']==conv for r in itens if r['tipo'].startswith(('mensagem.','turno.')))
            assert outro not in str(itens) and b not in str(itens)
            assert len({r['id'] for r in itens})==len(itens)
            assert 'conteudo' not in str(itens) and 'olá' not in str(itens)
            assert (await c.get(url,params={'desde':cursor})).json()['itens']==[]
            assert (await c.get(url,params={'desde':-1})).status_code==400
            assert (await c.get('/admin/v1/tenants/ausente/eventos')).status_code==404
    banco(cenario)


def test_SEG02_SEG12_gestao_protegida_e_actor_obrigatorio():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,ca=await criar(c)
            routes=[('POST',f'/agentes/{a}/testar'),('POST',f'/canais/{ca}/rotacionar-segredo'),('GET','/conversas'),
                    ('POST','/conversas/ausente/assumir'),('POST','/conversas/ausente/devolver'),('POST','/conversas/ausente/mensagens'),('GET','/eventos'),('GET','/eventos/cursor')]
            for method,path in routes:
                url='/admin/v1/tenants/alpha'+path
                assert (await c.request(method,url,headers={'X-Forwarded-For':'127.0.0.1'})).status_code==404
                assert (await c.request(method,url,headers={'Authorization':'Bearer '+'z'*32})).status_code==401
                if method=='POST': assert (await c.request(method,url,json={'texto':'x'},headers={'X-Actor':''})).status_code==400
            assert (await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/testar',headers={'Authorization':'Bearer '+'b'*32})).status_code==200
    banco(cenario)


def test_INV03_INV13_envio_humano_recusado_mantem_pausa_sem_entrega_falsa(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn);fila(app)
        async with c:
            a,ca=await criar(c);await ativo(c,'alpha',a,ca)
            conv=await simular(c,'alpha',a,ca)
            # Somente o transporte simulado lança a falha: nenhuma saída de rede.
            await conn.execute("UPDATE tenant_canais SET tipo='uazapi' WHERE tenant_id='alpha' AND id=$1",ca)
            await conn.execute("UPDATE agentes SET send_mode='live' WHERE tenant_id='alpha' AND id=$1",a)
            app.state.configuracoes.invalidar('alpha')
            chamadas=[]
            async def recusar(*args):
                chamadas.append(1)
                raise RuntimeError('credencial-secreta-do-provedor')
            monkeypatch.setattr('admin.gestao.uazapi.enviar',recusar)
            r=await c.post('/admin/v1/tenants/alpha/conversas/'+conv+'/mensagens',json={'texto':'teste'})
            assert r.status_code==502 and 'credencial-secreta' not in r.text
            assert chamadas==[1]
            assert not await app.state.pipeline.permitido('alpha',conv,a)
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id='alpha' AND origem='humano'")==0
            eventos=(await c.get('/admin/v1/tenants/alpha/eventos',params={'limite':100})).json()['itens']
            assert any(e['tipo']=='mensagem.humana_falhou' for e in eventos)
    banco(cenario)


def test_INV08_cursor_eventos_isolado_vazio_inexistente_e_continuacao():
    async def cenario(conn):
        await migrar.aplicar(conn); app,c=preparar(conn)
        async with c:
            # Tenant inserido sem auditoria para cobrir cursor inicial zero.
            await conn.execute("INSERT INTO tenants(id,nome) VALUES ('vazio','Vazio')")
            assert (await c.get('/admin/v1/tenants/vazio/eventos/cursor')).json()=={'ultimo_id':0}
            await criar(c,'alpha'); await criar(c,'bravo')
            url='/admin/v1/tenants/alpha/eventos/cursor'
            r=await c.get(url); assert r.status_code==200
            ultimo=r.json()['ultimo_id']
            assert r.json()=={'ultimo_id':await conn.fetchval("SELECT max(id) FROM audit_log WHERE tenant_id='alpha'")}
            assert (await c.get('/admin/v1/tenants/bravo/eventos/cursor')).json()['ultimo_id']>ultimo
            await c.patch('/admin/v1/tenants/bravo',json={'nome':'Outro tenant'})
            assert (await c.get(url)).json()=={'ultimo_id':ultimo}
            await c.patch('/admin/v1/tenants/alpha',json={'nome':'Nome atualizado'})
            eventos=(await c.get('/admin/v1/tenants/alpha/eventos',params={'desde':ultimo})).json()
            assert len(eventos['itens'])==1 and eventos['itens'][0]['alvo']=='alpha'
            assert (await c.get(url)).json()=={'ultimo_id':eventos['proximo_cursor']}
            ausente=await c.get('/admin/v1/tenants/ausente/eventos/cursor')
            assert ausente.status_code==404 and ausente.json()['erro']['codigo']=='NOT_FOUND'
    banco(cenario)
