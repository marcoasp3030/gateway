"""Gestão, bloco G2: Skill info_unidades, filtros do histórico (G1.1) e telefone no formato E.164."""
from datetime import datetime,timedelta,timezone
import asyncpg
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
SEMANA={d:[['00:00','23:59']] for d in ('seg','ter','qua','qui','sex','sab','dom')}
CAMPOS={'tem_cardapio','itens_no_cardapio','codigo','nome','ativa','endereco','telefone','situacao','agenda','aceita_retirada','delivery','taxa_entrega','pedido_minimo','formas_pagamento','area_entrega','regras',
        'entrega','retirada','pagamentos'}


async def chave(client,tenant,escopos=('ferramentas:catalogo',)):
    r=(await client.post(f'/admin/v1/tenants/{tenant}/api-keys',json={'nome':'skills','escopos':list(escopos)})).json()
    return next(v for k,v in r.items() if isinstance(v,str) and k in ('chave','api_key','valor','segredo','key'))


async def conversa(client,tenant,agente,canal):
    await ativo(client,tenant,agente,canal)
    r=await client.post(f'/admin/v1/tenants/{tenant}/agentes/{agente}/simular',json={'canal_id':canal,'identificador':'u','texto':'oi','message_id':'m-'+tenant})
    assert r.status_code==200,r.text
    return r.json()['conversation_id']


def test_info_unidades_contrato_nulos_filtro_e_isolamento():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,ca=await criar(client);b,cb=await criar(client,'bravo')
            conv=await conversa(client,'alpha',a,ca);conv_b=await conversa(client,'bravo',b,cb)
            base='/admin/v1/tenants/alpha/unidades'
            cheia=(await client.post(base,json={'nome':'Centro','codigo':'centro','horarios':SEMANA,'endereco':'Rua A, 1','telefone':'(11) 3000-0000',
                'whatsapp_pedidos':'5511900000010','formas_pagamento':'Pix e cartão','taxa_entrega':12.5,'pedido_minimo':80,'area_entrega':'Centro e Bela Vista',
                'aceita_retirada':True,'regras':'Sem troco para 200'},headers={'X-Role':'admin'})).json()
            vazia=(await client.post(base,json={'nome':'Zona Sul','codigo':'zona_sul'})).json()
            fora=(await client.post(base,json={'nome':'Antiga','codigo':'antiga','ativo':False})).json()
            assert (await client.post('/admin/v1/tenants/bravo/unidades',json={'nome':'Do Bravo','codigo':'centro','whatsapp_pedidos':'5511900000099'})).status_code==201
            segredo=await chave(client,'alpha')
            async def info(headers=None,**params):
                return await client.get('/api/v1/unidades',params={'conversation_id':conv,**params},headers={'X-API-Key':segredo,'Authorization':'',**(headers or {})})
            r=await info();assert r.status_code==200,r.text
            # Todas as da empresa da chave: ativas em ordem de nome e a desativada marcada, sem sumir; contrato exato.
            us=r.json()['unidades'];assert [(u['codigo'],u['ativa']) for u in us]==[('centro',True),('zona_sul',True),('antiga',False)] and set(r.json())=={'unidades'}
            assert all(set(u)==CAMPOS for u in us)
            c,z,_=us
            assert (c['nome'],c['ativa'],c['endereco'],c['telefone'],c['aceita_retirada'],c['area_entrega'],c['pedido_minimo'],c['formas_pagamento'],c['regras'])==(
                'Centro',True,'Rua A, 1','(11) 3000-0000',True,'Centro e Bela Vista',80.0,'Pix e cartão','Sem troco para 200')
            assert c['taxa_entrega']==12.5 and c['delivery'] is None
            # Situação: a mesma que o painel recebe (mesma função do G1).
            painel=(await client.get(f'{base}/{cheia["id"]}')).json()['situacao']
            assert {k:c['situacao'][k] for k in painel}==painel and c['situacao']['aberta_agora'] is True and c['situacao']['fecha_as']
            assert set(c['situacao'])=={'aberta_agora','motivo','fecha_as','proxima_abertura','pausada_ate','pausa_motivo'}
            # Agenda dos próximos 7 dias, a mesma do painel.
            assert c['agenda']==(await client.get(f'{base}/{cheia["id"]}/agenda')).json()['dias'] and len(c['agenda'])==7
            assert set(c['agenda'][0])=={'data','dia','origem','fechado','faixas','motivo'}
            # Não informado vem nulo, sem inventar; sem horário = aberta_agora nulo.
            assert [z[k] for k in ('endereco','telefone','aceita_retirada','area_entrega','taxa_entrega','pedido_minimo','formas_pagamento','regras','delivery')]==[None]*9
            assert (z['situacao']['aberta_agora'],z['situacao']['motivo'])==(None,'sem_horario') and all(d['fechado'] for d in z['agenda'])
            # O WhatsApp de pedidos nunca sai; nada da outra empresa aparece.
            assert '5511900000010' not in r.text and 'whatsapp' not in r.text and 'Do Bravo' not in r.text and 'uni_' not in r.text and 'criado_em' not in r.text and 'actor' not in r.text
            # Pausa e exceção aparecem na hora.
            amanha=(datetime.now(timezone.utc)+timedelta(days=1)).date().isoformat()
            await client.post(f'{base}/{cheia["id"]}/pausar',json={'indefinida':True,'motivo':'falta de luz'})
            await client.post(f'{base}/{cheia["id"]}/excecoes',json={'data':amanha,'fechado':True,'motivo':'feriado'})
            c=(await info(unidade='centro')).json()['unidades'];assert len(c)==1
            assert (c[0]['situacao']['aberta_agora'],c[0]['situacao']['motivo'],c[0]['situacao']['pausa_motivo'])==(False,'pausa','falta de luz')
            dia=[d for d in c[0]['agenda'] if d['data']==amanha];assert dia and (dia[0]['origem'],dia[0]['fechado'],dia[0]['motivo'])==('excecao',True,'feriado')
            # Reaberta e com exceção HOJE (fechado): fechada por exceção, com a próxima abertura.
            await client.post(f'{base}/{cheia["id"]}/reabrir')
            from zoneinfo import ZoneInfo
            hoje=datetime.now(ZoneInfo('America/Sao_Paulo')).date().isoformat()
            assert (await info(unidade='centro')).json()['unidades'][0]['situacao']['aberta_agora'] is True
            await client.post(f'{base}/{cheia["id"]}/excecoes',json={'data':hoje,'fechado':True,'motivo':'manutenção'})
            c=(await info(unidade='centro')).json()['unidades'][0]
            assert c['agenda'][0]['data']==hoje and (c['agenda'][0]['origem'],c['agenda'][0]['fechado'],c['agenda'][0]['motivo'])==('excecao',True,'manutenção')
            # (pode estar aberta só se um turno da véspera cruzasse a meia-noite; aqui a grade acaba 23:59)
            assert (c['situacao']['aberta_agora'],c['situacao']['motivo'])==(False,'excecao') and c['situacao']['proxima_abertura']
            # Filtro: unidade desativada vem marcada; inexistente → 404 com o envelope de erro das outras Skills.
            r404=await info(unidade='nao_existe');rconv=await info(conversation_id='alpha:x:nada')
            assert r404.status_code==404 and r404.json()==rconv.json() and (await info(unidade='uni_x',headers={'X-Tenant-ID':'bravo'})).status_code==404
            d=(await info(unidade='antiga')).json()['unidades'];assert len(d)==1 and d[0]['ativa'] is False and d[0]['situacao']['motivo']=='inativa'
            assert (await info(unidade='nao_existe')).status_code==404 and fora['id'] and vazia['id']
            # Segurança: sem chave 401; conversa de outra empresa ou inexistente 404; sem o escopo 403; sem conversa 400.
            assert (await client.get('/api/v1/unidades',params={'conversation_id':conv},headers={'Authorization':''})).status_code==401
            assert (await info(conversation_id=conv_b)).status_code==404 and (await info(conversation_id='alpha:x:nada')).status_code==404
            assert (await info(headers={'X-API-Key':await chave(client,'alpha',('notificar',))})).status_code==403
            assert (await client.get('/api/v1/unidades',headers={'X-API-Key':segredo,'Authorization':''})).status_code==400
            # A empresa vem sempre da chave: cabeçalho ou parâmetro com outra empresa não muda nada.
            r=await info(headers={'X-Tenant-ID':'bravo'},tenant_id='bravo');assert [u['codigo'] for u in r.json()['unidades']]==['centro','zona_sul','antiga']
            # A chave do bravo só enxerga o bravo.
            rb=await client.get('/api/v1/unidades',params={'conversation_id':conv_b},headers={'X-API-Key':await chave(client,'bravo'),'Authorization':''})
            assert [u['nome'] for u in rb.json()['unidades']]==['Do Bravo'] and (await client.get('/api/v1/unidades',params={'conversation_id':conv},headers={'X-API-Key':await chave(client,'bravo'),'Authorization':''})).status_code==404
    banco(cenario)


def test_G1_1_historico_por_periodo_e_por_unidade_com_excecoes():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            await criar(client);await criar(client,'bravo')
            base='/admin/v1/tenants/alpha/unidades';h='/admin/v1/tenants/alpha/gestao/alteracoes'
            u1=(await client.post(base,json={'nome':'Centro','codigo':'centro'})).json()['id']
            u2=(await client.post(base,json={'nome':'Sul','codigo':'sul'})).json()['id']
            ub=(await client.post('/admin/v1/tenants/bravo/unidades',json={'nome':'B','codigo':'b'})).json()['id']
            e=(await client.post(f'{base}/{u1}/excecoes',json={'data':'2026-12-25','fechado':True})).json()['id']
            await client.post(f'{base}/{u1}/excecoes',json={'data':'2026-12-31','faixas':[['12:00','16:00']]})
            await client.delete(f'{base}/{u1}/excecoes/{e}')
            await client.post(f'{base}/{u2}/excecoes',json={'data':'2026-12-25','fechado':True})
            await client.patch(f'{base}/{u1}',json={'nome':'Centro Novo'})
            async def lista(**p):
                r=await client.get(h,params=p);assert r.status_code==200,r.text
                return r.json()
            # unidade_id: a unidade e todas as exceções dela (inclusive a removida), mais novas primeiro; nada da outra unidade.
            itens=(await lista(unidade_id=u1))['itens']
            assert [(x['entidade'],x['acao']) for x in itens]==[('unidade','alterado'),('excecao','removido'),('excecao','criado'),('excecao','criado'),('unidade','criado')]
            assert all(x['entidade_id']==u1 or x['entidade_id'].startswith(u1+':') for x in itens)
            assert [(x['entidade'],x['acao']) for x in (await lista(unidade_id=u2))['itens']]==[('excecao','criado'),('unidade','criado')]
            # Paginação por cursor continua valendo com o filtro.
            p1=await lista(unidade_id=u1,limite=2);p2=await lista(unidade_id=u1,limite=2,cursor=p1['proximo_cursor'])
            assert [x['id'] for x in p1['itens']+p2['itens']]==[x['id'] for x in itens][:4] and p2['proximo_cursor']
            # criado_em sempre com offset.
            for x in itens:
                assert datetime.fromisoformat(x['criado_em']).tzinfo is not None and x['criado_em'][-6] in '+-'
            # Período: desde inclusivo, ate exclusivo, em qualquer fuso.
            await conn.execute("UPDATE gestao_alteracoes SET criado_em='2026-10-01T12:00:00Z' WHERE id=$1",itens[-1]['id'])
            await conn.execute("UPDATE gestao_alteracoes SET criado_em='2026-10-02T12:00:00Z' WHERE id=$1",itens[-2]['id'])
            ids=lambda r:[x['id'] for x in r['itens']]
            assert ids(await lista(unidade_id=u1,desde='2026-10-01T09:00:00-03:00',ate='2026-10-02T09:00:00-03:00'))==[itens[-1]['id']]
            assert ids(await lista(unidade_id=u1,desde='2026-10-01T09:00:01-03:00',ate='2026-10-02T09:00:01-03:00'))==[itens[-2]['id']]
            assert ids(await lista(unidade_id=u1,ate='2026-10-02T12:00:00Z'))==[itens[-1]['id']]
            assert ids(await lista(unidade_id=u1,desde='2026-10-02T12:00:00+00:00',ate='2026-10-03T00:00:00+00:00'))==[itens[-2]['id']]
            assert len(ids(await lista(desde='2026-10-03T00:00:00Z')))==len(ids(await lista()))-2
            # O "+" do fuso sem codificar chega como espaço: aceito.
            r=await client.get(h+f'?unidade_id={u1}&desde=2026-10-02T12:00:00+00:00&ate=2026-10-03T00:00:00+00:00');assert r.status_code==200 and ids(r.json())==[itens[-2]['id']]
            # Recusas: sem fuso, data inválida, desde depois de ate, unidade_id com entidade, limite fora de 1..200.
            for ruim in ({'desde':'2026-10-01T09:00:00'},{'ate':'2026-10-01'},{'desde':'ontem'},{'desde':'2026-10-02T00:00:00Z','ate':'2026-10-01T00:00:00Z'},
                         {'unidade_id':u1,'entidade':'unidade'},{'unidade_id':u1,'entidade_id':u1},{'limite':0},{'limite':201},{'cursor':-1}):
                assert (await client.get(h,params=ruim)).status_code==400,ruim
            assert len((await lista(limite=200))['itens'])==7
            # Unidade de outra empresa ou inexistente → 404; outra empresa não vê nada daqui.
            assert (await client.get(h,params={'unidade_id':ub})).status_code==404 and (await client.get(h,params={'unidade_id':'uni_nada'})).status_code==404
            assert [x['entidade_id'] for x in (await client.get('/admin/v1/tenants/bravo/gestao/alteracoes',params={'unidade_id':ub})).json()['itens']]==[ub]
            # Os filtros antigos continuam iguais.
            assert len((await lista(entidade='excecao'))['itens'])==4 and len((await lista(entidade='unidade',entidade_id=u1))['itens'])==2
    banco(cenario)


def test_telefone_de_pedidos_e_de_alerta_no_formato_E164():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            await criar(client)
            base='/admin/v1/tenants/alpha/unidades'
            uid=(await client.post(base,json={'nome':'Centro','codigo':'centro'})).json()['id']
            for bom in ('5511900000010','1234567890','123456789012345'):
                assert (await client.patch(f'{base}/{uid}',json={'whatsapp_pedidos':bom})).status_code==200,bom
                assert (await client.patch('/admin/v1/tenants/alpha',json={'whatsapp_alerta_transbordo':bom})).status_code==200,bom
            for ruim in ('123456789','1234567890123456','+5511900000010','55 11 90000-0010','11-9999',''):
                r=await client.patch(f'{base}/{uid}',json={'whatsapp_pedidos':ruim})
                assert r.status_code==400,ruim
                assert (await client.patch('/admin/v1/tenants/alpha',json={'whatsapp_alerta_transbordo':ruim})).status_code==400,ruim
            # Recusa não altera o valor; nulo limpa (não informado). O banco também recusa fora da regra.
            assert (await client.get(f'{base}/{uid}')).json()['whatsapp_pedidos']=='123456789012345'
            assert (await client.patch(f'{base}/{uid}',json={'whatsapp_pedidos':None})).json()['whatsapp_pedidos'] is None
            for sql in ("UPDATE tenant_unidades SET whatsapp_pedidos='123456789' WHERE id=$1","UPDATE tenant_unidades SET whatsapp_pedidos='1234567890123456' WHERE id=$1"):
                with pytest.raises(asyncpg.CheckViolationError):
                    async with conn.transaction(): await conn.execute(sql,uid)
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction(): await conn.execute("UPDATE tenants SET whatsapp_alerta_transbordo='123456789' WHERE id='alpha'")
    banco(cenario)


def test_migracao_025_nao_altera_dado_fora_da_regra_falha_e_nada_muda(tmp_path):
    import shutil
    async def cenario(conn):
        ate24=tmp_path/'ate24';ate24.mkdir()
        for f in sorted(migrar.PASTA.glob('*.sql')):
            if int(f.name[:3])<=24: shutil.copy(f,ate24/f.name)
        await migrar.aplicar(conn,ate24)
        await conn.execute("INSERT INTO tenants(id,nome) VALUES ('alpha','alpha')")
        await conn.execute("INSERT INTO tenant_unidades(id,tenant_id,nome,whatsapp_pedidos) VALUES ('uni_1','alpha','Centro','12345678')")
        with pytest.raises(asyncpg.CheckViolationError): await migrar.aplicar(conn)
        assert await conn.fetchval('SELECT max(versao) FROM schema_migrations')==24
        assert await conn.fetchval("SELECT whatsapp_pedidos FROM tenant_unidades WHERE id='uni_1'")=='12345678'
        # Corrigido por quem decide (aqui, o teste), a migração passa.
        await conn.execute("UPDATE tenant_unidades SET whatsapp_pedidos='5511900000010' WHERE id='uni_1'")
        assert (await migrar.aplicar(conn))[0]=='025_telefone_e164.sql'
    banco(cenario)


def test_info_unidades_turno_que_cruza_a_meia_noite_usa_a_funcao_do_G1(monkeypatch):
    """Sexta 19:00–02:00: sábado 00:30 ainda está aberta (fecha às 02:00) e às 02:30 já fechou."""
    from zoneinfo import ZoneInfo
    from modulos import unidades as M
    Z=ZoneInfo('America/Sao_Paulo')
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/unidades',json={'nome':'Bar','codigo':'bar','horarios':{'sex':[['19:00','02:00']],'sab':[['12:00','16:00']]}})
            assert r.status_code==201,r.text
            row=await conn.fetchrow("SELECT * FROM tenant_unidades WHERE tenant_id='alpha' AND codigo='bar'")
            # 2026-10-10 é sábado.
            u=await M.unidade_para_o_agente(conn,'alpha',row,'America/Sao_Paulo',datetime(2026,10,10,0,30,tzinfo=Z))
            assert (u['situacao']['aberta_agora'],u['situacao']['motivo'],u['situacao']['fecha_as'])==(True,None,'2026-10-10T02:00-03:00')
            assert u['agenda'][0]['data']=='2026-10-10' and u['agenda'][0]['faixas']==[['12:00','16:00']] and len(u['agenda'])==7
            assert [d['faixas'] for d in u['agenda'] if d['dia']=='sex']==[[['19:00','02:00']]]
            u=await M.unidade_para_o_agente(conn,'alpha',row,'America/Sao_Paulo',datetime(2026,10,10,2,30,tzinfo=Z))
            assert (u['situacao']['aberta_agora'],u['situacao']['motivo'],u['situacao']['proxima_abertura'])==(False,'horario','2026-10-10T12:00-03:00')
            assert 'id' not in u and 'whatsapp_pedidos' not in u
    banco(cenario)
