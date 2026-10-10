"""Gestão, bloco G1: função única de horário, exceções por data, pausa manual, fuso e rotas do painel."""
import json
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import pytest
from nucleo import horarios as H
from admin.alteracoes import diferenca
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar

SP='America/Sao_Paulo';Z=ZoneInfo(SP)
def t(*a): return datetime(*a,tzinfo=Z)
# 2026-10-05 é segunda. Sexta com turno que cruza a meia-noite; domingo fechado.
GRADE={'seg':[['12:00','15:00'],['19:00','22:00']],'ter':[['12:00','15:00'],['19:00','22:00']],'sex':[['12:00','15:00'],['19:00','02:00']],'sab':[['12:00','16:00']]}
s=lambda agora,**k: H.situacao(GRADE,SP,agora,**k)


def test_varios_turnos_intervalo_e_proxima_abertura():
    assert s(t(2026,10,5,12,0))=={'aberta_agora':True,'motivo':None,'fecha_as':'2026-10-05T15:00-03:00','proxima_abertura':None,'pausada_ate':None}
    assert s(t(2026,10,5,14,59))['aberta_agora'] and not s(t(2026,10,5,15,0))['aberta_agora']      # fim do turno é fechado
    r=s(t(2026,10,5,16,0));assert (r['aberta_agora'],r['motivo'],r['proxima_abertura'])==(False,'horario','2026-10-05T19:00-03:00')
    assert s(t(2026,10,5,23,0))['proxima_abertura']=='2026-10-06T12:00-03:00'
    # Atravessa dias fechados: terça à noite → sexta ao meio-dia (quarta e quinta sem turno).
    assert s(t(2026,10,6,22,30))['proxima_abertura']=='2026-10-09T12:00-03:00'
    # Instante informado em UTC é convertido para o fuso.
    assert H.situacao(GRADE,SP,datetime(2026,10,5,15,30,tzinfo=timezone.utc))['aberta_agora'] is True


def test_turno_que_cruza_a_meia_noite():
    assert s(t(2026,10,9,23,30))['fecha_as']=='2026-10-10T02:00-03:00'
    r=s(t(2026,10,10,0,30));assert r['aberta_agora'] and r['fecha_as']=='2026-10-10T02:00-03:00'      # sábado 00:30 ainda é o turno de sexta
    r=s(t(2026,10,10,2,0));assert not r['aberta_agora'] and r['proxima_abertura']=='2026-10-10T12:00-03:00'
    # Turno até 00:00 fecha à meia-noite; turnos emendados contam como um período só.
    g={'seg':[['18:00','00:00']],'ter':[['00:00','03:00']]}
    assert H.situacao(g,SP,t(2026,10,5,23,59))['fecha_as']=='2026-10-06T03:00-03:00' and not H.situacao(g,SP,t(2026,10,6,3,0))['aberta_agora']
    # Domingo para segunda (virada da semana).
    g={'dom':[['20:00','01:00']]};assert H.situacao(g,SP,t(2026,10,12,0,30))['aberta_agora'] and not H.situacao(g,SP,t(2026,10,12,1,0))['aberta_agora']


def test_excecoes_por_data():
    fechado=[{'data':'2026-10-05','fechado':True,'faixas':[],'motivo':'feriado'}]
    r=s(t(2026,10,5,12,30),excecoes=fechado);assert (r['aberta_agora'],r['motivo'],r['proxima_abertura'])==(False,'excecao','2026-10-06T12:00-03:00')
    # Horário especial substitui a grade do dia (inclusive em dia que normalmente não abre).
    especial=[{'data':'2026-10-07','fechado':False,'faixas':[['18:00','23:00']]},{'data':'2026-10-05','fechado':False,'faixas':[['10:00','11:00']]}]
    assert s(t(2026,10,7,19,0),excecoes=especial)['fecha_as']=='2026-10-07T23:00-03:00'
    r=s(t(2026,10,5,12,30),excecoes=especial);assert not r['aberta_agora'] and r['motivo']=='excecao' and r['proxima_abertura']=='2026-10-06T12:00-03:00'
    assert s(t(2026,10,5,10,30),excecoes=especial)['aberta_agora']
    # Feriado depois de um turno que cruza a meia-noite: o turno da véspera vai até o fim.
    assert s(t(2026,10,10,1,0),excecoes=[{'data':'2026-10-10','fechado':True,'faixas':[]}])['aberta_agora']
    # Vários dias fechados seguidos empurram a próxima abertura.
    varios=[{'data':f'2026-10-{d:02d}','fechado':True,'faixas':[]} for d in (5,6,9,10)]
    assert s(t(2026,10,5,8,0),excecoes=varios)['proxima_abertura']=='2026-10-12T12:00-03:00'


def test_pausa_manual():
    r=s(t(2026,10,5,12,30),pausa_ate=t(2026,10,5,14,0));assert (r['aberta_agora'],r['motivo'],r['pausada_ate'],r['proxima_abertura'])==(False,'pausa','2026-10-05T14:00-03:00','2026-10-05T14:00-03:00')
    # Pausa que termina com a unidade fechada: reabre no próximo turno.
    assert s(t(2026,10,5,12,30),pausa_ate=t(2026,10,5,16,0))['proxima_abertura']=='2026-10-05T19:00-03:00'
    # Pausa vencida é ignorada (não precisa de ninguém "reabrir").
    assert s(t(2026,10,5,14,30),pausa_ate=t(2026,10,5,14,0))['aberta_agora'] is True
    r=s(t(2026,10,5,12,30),pausa_indefinida=True);assert (r['aberta_agora'],r['motivo'],r['proxima_abertura'],r['pausada_ate'])==(False,'pausa',None,None)
    r=s(t(2026,10,5,12,30),ativo=False);assert (r['aberta_agora'],r['motivo'])==(False,'inativa')


def test_fuso_da_unidade_sem_horario_e_horario_de_verao():
    # A mesma grade em fusos diferentes: 12:30 em São Paulo são 11:30 em Manaus (fechada) e 16:30 em Lisboa (fechada, entre turnos).
    agora=t(2026,10,5,12,30)
    assert H.situacao(GRADE,SP,agora)['aberta_agora'] and not H.situacao(GRADE,'America/Manaus',agora)['aberta_agora']
    assert H.situacao(GRADE,'America/Manaus',agora)['proxima_abertura']=='2026-10-05T12:00-04:00'
    assert H.situacao(GRADE,'Europe/Lisbon',agora)['proxima_abertura']=='2026-10-05T19:00+01:00'
    # Sem nada cadastrado: não informado (None), não "fechada".
    assert H.situacao({},SP,agora)=={'aberta_agora':None,'motivo':'sem_horario','fecha_as':None,'proxima_abertura':None,'pausada_ate':None}
    assert H.estado({},'UTC')=={'aberta_agora':False,'proxima_abertura':None}       # forma antiga preservada
    # Fim do horário de verão em Lisboa (25/10/2026, 02:00 → 01:00): o turno das 19:00 continua às 19:00 locais.
    g={'sab':[['19:00','23:00']],'dom':[['19:00','23:00']]}
    assert H.situacao(g,'Europe/Lisbon',datetime(2026,10,24,23,30,tzinfo=ZoneInfo('Europe/Lisbon')))['proxima_abertura']=='2026-10-25T19:00+00:00'


def test_validacao_da_grade_e_agenda():
    for ruim in ({'seg':[['12:00','15:00'],['14:00','16:00']]},{'dom':[['22:00','03:00']],'seg':[['02:00','05:00']]},{'sex':[['19:00','02:00']],'sab':[['01:00','03:00']]},
                 {'seg':[['25:00','18:00']]},{'seg':[['12:00','12:00']]},{'feira':[['12:00','15:00']]},{'seg':[['12:00']]},{'seg':'12:00'},{'seg':[['12:00','15:00']]*13}):
        with pytest.raises(ValueError): H.validar_sem_sobreposicao(ruim)
    assert H.validar_sem_sobreposicao(GRADE)==GRADE and H.validar_sem_sobreposicao({'seg':[['12:00','15:00'],['15:00','18:00']]})
    a=H.agenda(GRADE,SP,[{'data':'2026-10-06','fechado':True,'faixas':[],'motivo':'feriado'},{'data':'2026-10-07','fechado':False,'faixas':[['18:00','23:00']],'motivo':None}],4,t(2026,10,5,9,0))
    assert [(d['data'],d['dia'],d['origem'],d['fechado'],d['faixas'],d['motivo']) for d in a]==[
        ('2026-10-05','seg','grade',False,[['12:00','15:00'],['19:00','22:00']],None),('2026-10-06','ter','excecao',True,[],'feriado'),
        ('2026-10-07','qua','excecao',False,[['18:00','23:00']],None),('2026-10-08','qui','grade',True,[],None)]
    # Histórico: só o que mudou, telefone mascarado.
    assert diferenca({'nome':'A','whatsapp_pedidos':'5511900000010','x':1},{'nome':'B','whatsapp_pedidos':'5511900000011','x':1})==({'nome':'A','whatsapp_pedidos':'5511****0010'},{'nome':'B','whatsapp_pedidos':'5511****0011'})


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_rotas_do_painel_horarios_excecoes_pausa_e_historico():
    from nucleo.horarios import pacote
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            await criar(client);await criar(client,'bravo')
            base='/admin/v1/tenants/alpha/unidades'
            r=await client.post(base,json={'nome':'Centro','codigo':'centro','horarios':{'seg':[['00:00','23:59']],'ter':[['00:00','23:59']],'qua':[['00:00','23:59']],'qui':[['00:00','23:59']],'sex':[['00:00','23:59']],'sab':[['00:00','23:59']],'dom':[['00:00','23:59']]}})
            assert r.status_code==201,r.text
            u=r.json();uid=u['id'];url=f'{base}/{uid}'
            # A unidade já volta com a situação e o fuso efetivo (herdado da empresa).
            assert u['situacao']['aberta_agora'] is True and u['fuso'] is None and u['fuso_efetivo'] and (u['pausa_ate'],u['pausa_indefinida'])==(None,False)
            # Fuso próprio validado.
            assert (await client.patch(url,json={'fuso':'America/Manaus'})).json()['fuso_efetivo']=='America/Manaus'
            assert (await client.patch(url,json={'fuso':'Marte/Olympus'})).status_code==400
            assert (await client.patch(url,json={'fuso':None})).json()['fuso'] is None
            # Pausa até um horário, indefinida e reabrir.
            ate=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
            r=await client.post(url+'/pausar',json={'ate':ate,'motivo':'falta de luz'});assert r.status_code==200,r.text
            sit=r.json()['situacao'];assert (sit['aberta_agora'],sit['motivo'])==(False,'pausa') and sit['pausada_ate'] and r.json()['pausa_motivo']=='falta de luz'
            p=[x for x in await pacote(conn,'alpha','America/Sao_Paulo') if x['codigo']=='centro'][0]
            assert (p['aberta_agora'],p['motivo_fechada'],p['pausa_motivo'])==(False,'pausa','falta de luz') and p['pausada_ate'] and p['proxima_abertura']
            r=await client.post(url+'/pausar',json={'indefinida':True});assert r.json()['situacao']['proxima_abertura'] is None and r.json()['pausa_ate'] is None
            for ruim in ({},{'ate':ate,'indefinida':True},{'ate':'2020-01-01T10:00:00-03:00'},{'ate':'2026-10-10T10:00:00'},{'ate':(datetime.now(timezone.utc)+timedelta(days=90)).isoformat()},{'indefinida':True,'outro':1}):
                assert (await client.post(url+'/pausar',json=ruim)).status_code==400,ruim
            r=await client.post(url+'/reabrir');assert r.json()['situacao']['aberta_agora'] is True and (r.json()['pausa_indefinida'],r.json()['pausa_motivo'])==(False,None)
            # Grade semanal inteira; sobreposição e formato recusados sem alterar nada.
            nova={'seg':[['12:00','15:00'],['19:00','02:00']]}
            r=await client.put(url+'/horarios',json=nova);assert r.status_code==200 and r.json()['horarios']==nova
            for ruim in ({'seg':[['12:00','15:00'],['14:00','16:00']]},{'seg':[['12:00','25:00']]},{'segunda':[]}):
                assert (await client.put(url+'/horarios',json=ruim)).status_code==400
            assert (await client.patch(url,json={'horarios':{'seg':[['12:00','15:00'],['14:00','16:00']]}})).status_code==400
            assert (await client.get(url)).json()['horarios']==nova
            # Exceções: criar, substituir a mesma data, listar, remover.
            hoje=datetime.now(timezone.utc).date();d1=(hoje+timedelta(days=3)).isoformat();d2=(hoje+timedelta(days=5)).isoformat()
            r=await client.post(url+'/excecoes',json={'data':d1,'fechado':True,'motivo':'feriado'});assert r.status_code==200,r.text
            e1=r.json();assert (e1['data'],e1['fechado'],e1['faixas'],e1['motivo'])==(d1,True,[],'feriado')
            r=await client.post(url+'/excecoes',json={'data':d1,'faixas':[['12:00','16:00']]});assert r.json()['id']==e1['id'] and r.json()['fechado'] is False
            await client.post(url+'/excecoes',json={'data':d2,'fechado':True})
            assert [x['data'] for x in (await client.get(url+'/excecoes')).json()['itens']]==[d1,d2]
            assert [x['data'] for x in (await client.get(url+'/excecoes',params={'desde':d2})).json()['itens']]==[d2]
            for ruim in ({'data':d1},{'data':d1,'fechado':True,'faixas':[['12:00','16:00']]},{'data':d1,'faixas':[['12:00','16:00'],['15:00','18:00']]},{'data':'amanhã','fechado':True},{'data':d1,'fechado':False}):
                assert (await client.post(url+'/excecoes',json=ruim)).status_code==400,ruim
            # Agenda: grade + exceções, dia a dia.
            ag=(await client.get(url+'/agenda',params={'dias':7})).json()
            dias={x['data']:x for x in ag['dias']};assert len(ag['dias'])==7 and ag['fuso'] and 'aberta_agora' in ag['situacao']
            assert (dias[d1]['origem'],dias[d1]['faixas'],dias[d2]['fechado'],dias[d2]['origem'])==('excecao',[['12:00','16:00']],True,'excecao')
            assert (await client.get(url+'/agenda',params={'dias':0})).status_code==400
            assert (await client.delete(f'{url}/excecoes/{e1["id"]}')).status_code==200 and (await client.delete(f'{url}/excecoes/{e1["id"]}')).status_code==404
            # Isolamento: outra empresa não vê nem mexe na unidade.
            for metodo,caminho,corpo in (('GET',f'/admin/v1/tenants/bravo/unidades/{uid}',None),('POST',f'/admin/v1/tenants/bravo/unidades/{uid}/pausar',{'indefinida':True}),
                                         ('PUT',f'/admin/v1/tenants/bravo/unidades/{uid}/horarios',{}),('POST',f'/admin/v1/tenants/bravo/unidades/{uid}/excecoes',{'data':d1,'fechado':True}),
                                         ('GET',f'/admin/v1/tenants/bravo/unidades/{uid}/agenda',None)):
                assert (await client.request(metodo,caminho,json=corpo)).status_code==404,caminho
            assert (await client.get(url)).json()['pausa_indefinida'] is False
            # Histórico com antes e depois, por quem; outra empresa não vê.
            h=(await client.get('/admin/v1/tenants/alpha/gestao/alteracoes',params={'entidade':'unidade','entidade_id':uid})).json()['itens']
            acoes=[x['acao'] for x in h];assert acoes[-1]=='criado' and {'pausado','reaberto','horarios','alterado'}<=set(acoes) and all(x['actor']=='teste' for x in h)
            hor=[x for x in h if x['acao']=='horarios'][0];assert hor['depois']=={'horarios':nova} and 'horarios' in hor['antes']
            pausa=[x for x in h if x['acao']=='pausado'][-1];assert pausa['antes']['pausa_ate'] is None and pausa['depois']['pausa_motivo']=='falta de luz'
            exc=(await client.get('/admin/v1/tenants/alpha/gestao/alteracoes',params={'entidade':'excecao'})).json()['itens']
            assert [x['acao'] for x in exc][::-1]==['criado','alterado','criado','removido'] and exc[0]['depois'] is None
            assert (await client.get('/admin/v1/tenants/bravo/gestao/alteracoes')).json()['itens']==[]
            # Repetir a mesma alteração não cria linha de histórico.
            n=len(h);await client.put(url+'/horarios',json=nova)
            assert len((await client.get('/admin/v1/tenants/alpha/gestao/alteracoes',params={'entidade':'unidade','entidade_id':uid})).json()['itens'])==n
    banco(cenario)
