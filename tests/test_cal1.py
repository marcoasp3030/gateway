"""CAL1: calendário no pacote (docs/ESPEC_CAL1.md) — hoje + 7 dias, feriados, pontos facultativos e exceções das unidades."""
from datetime import date,datetime,timezone
import pytest
from agentes import skyone
from nucleo import calendario as CAL
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import ativo

SP='America/Sao_Paulo'
MORUMBI={'id':'u1','nome':'Morumbi','cidade':'São Paulo','uf':'SP'}
EINSTEIN={'id':'u2','nome':'Einstein','cidade':'São Paulo','uf':'SP'}
RIO={'id':'u3','nome':'Leblon','cidade':'Rio de Janeiro','uf':'RJ'}


def dia(quando,unidades=(MORUMBI,EINSTEIN),excecoes=()):
    """O item de `quando` (primeiro dia do calendário montado a partir dele)."""
    return CAL.montar(date.fromisoformat(quando),SP,unidades,excecoes)['dias'][0]


def exc(unidade,quando,fechado=True,faixas=(),motivo=None):
    return {'unidade_id':unidade['id'],'data':date.fromisoformat(quando),'fechado':fechado,'faixas':[list(f) for f in faixas],'motivo':motivo}


def feriado(d): return d['feriado'],d['tipo_feriado'],d['observacao']


def test_formato_oito_dias_rotulos_e_dia_da_semana():
    c=CAL.montar(date(2026,10,12),SP,[MORUMBI])
    assert set(c)=={'fuso','dias'} and c['fuso']==SP and len(c['dias'])==8
    assert all(set(d)=={'data','dia_semana','rotulo','feriado','tipo_feriado','observacao'} for d in c['dias'])
    assert [d['data'] for d in c['dias']]==[f'2026-10-{n}' for n in range(12,20)]
    assert [d['dia_semana'] for d in c['dias']]==['segunda-feira','terça-feira','quarta-feira','quinta-feira','sexta-feira','sábado','domingo','segunda-feira']
    assert [d['rotulo'] for d in c['dias']]==['hoje','amanhã']+[None]*6
    assert c['dias'][0]=={'data':'2026-10-12','dia_semana':'segunda-feira','rotulo':'hoje','feriado':'Nossa Senhora Aparecida','tipo_feriado':'nacional','observacao':None}
    assert all(feriado(d)==(None,None,None) for d in c['dias'][1:])
    # Virada de mês e de ano dentro da janela.
    c=CAL.montar(date(2026,12,28),SP,[MORUMBI]);assert [d['data'] for d in c['dias']][-4:]==['2027-01-01','2027-01-02','2027-01-03','2027-01-04']
    assert feriado(c['dias'][4])==('Confraternização Universal','nacional',None)


def test_feriado_nacional_estadual_municipal_e_corpus_christi():
    # Nacional: vale para qualquer empresa, mesmo sem unidade ou sem cidade cadastrada.
    assert feriado(dia('2026-11-15',()))==('Proclamação da República','nacional',None)
    assert feriado(dia('2026-04-03',[{'id':'x','nome':'Sem cidade','cidade':None,'uf':None}]))==('Sexta-feira Santa','nacional',None)
    # Estadual (09/07, São Paulo): só para unidade de SP.
    assert feriado(dia('2026-07-09'))==('Revolução Constitucionalista','estadual',None)
    assert feriado(dia('2026-07-09',[RIO]))==(None,None,None) and feriado(dia('2026-07-09',()))==(None,None,None)
    assert feriado(dia('2026-07-09',[{'id':'x','nome':'Sem cidade','cidade':None,'uf':None}]))==(None,None,None)
    assert feriado(dia('2026-07-09',[MORUMBI,RIO]))==('Revolução Constitucionalista','estadual','feriado só em SP')
    # Municipal (25/01, aniversário de São Paulo): só para unidade da capital.
    assert feriado(dia('2027-01-25'))==('Aniversário de São Paulo','municipal',None)
    assert feriado(dia('2027-01-25',[{'id':'x','nome':'Campinas','cidade':'Campinas','uf':'SP'}]))==(None,None,None)
    assert feriado(dia('2027-01-25',[{'id':'x','nome':'Centro','cidade':'SAO PAULO','uf':'SP'}]))[1]=='municipal'      # cidade sem acento e em maiúsculas
    assert feriado(dia('2027-01-25',[MORUMBI,RIO]))==('Aniversário de São Paulo','municipal','feriado só em São Paulo')
    # Corpus Christi: data móvel (Páscoa + 60 dias).
    assert CAL.pascoa(2026)==date(2026,4,5) and CAL.pascoa(2027)==date(2027,3,28) and CAL.pascoa(2028)==date(2028,4,16)
    assert feriado(dia('2026-06-04'))==('Corpus Christi','municipal',None) and feriado(dia('2027-05-27'))==('Corpus Christi','municipal',None)
    assert feriado(dia('2026-06-04',[RIO]))==(None,None,None) and feriado(dia('2026-06-05'))==(None,None,None)


def test_carnaval_e_cinzas_entram_como_observacao():
    # Data móvel pela Páscoa: 2026 → 16 e 17/02 (Carnaval) e 18/02 (Cinzas); 2027 → 08, 09 e 10/02.
    for quando,texto in (('2026-02-16','Carnaval (ponto facultativo)'),('2026-02-17','Carnaval (ponto facultativo)'),('2026-02-18','Quarta-feira de Cinzas (ponto facultativo)'),
                         ('2027-02-08','Carnaval (ponto facultativo)'),('2027-02-09','Carnaval (ponto facultativo)'),('2027-02-10','Quarta-feira de Cinzas (ponto facultativo)')):
        assert feriado(dia(quando))==(None,None,texto),quando
    assert feriado(dia('2026-02-15'))==(None,None,None) and feriado(dia('2026-02-19'))==(None,None,None)
    # Com exceção de unidade no mesmo dia: tudo na mesma observação, separado por "; ".
    assert dia('2026-02-16',excecoes=[exc(MORUMBI,'2026-02-16',motivo='bloco na rua')])['observacao']=='Carnaval (ponto facultativo); Morumbi fechado (bloco na rua)'
    c=CAL.montar(date(2026,2,14),SP,[MORUMBI]);assert [d['observacao'] for d in c['dias']][:6]==[None,None,'Carnaval (ponto facultativo)','Carnaval (ponto facultativo)','Quarta-feira de Cinzas (ponto facultativo)',None]


def test_excecoes_das_unidades_e_feriado_da_empresa():
    # Uma unidade fechada (com e sem motivo) e horário especial com um e com dois turnos.
    assert feriado(dia('2026-10-13',excecoes=[exc(MORUMBI,'2026-10-13',motivo='inventário')]))==(None,None,'Morumbi fechado (inventário)')
    assert dia('2026-10-13',excecoes=[exc(MORUMBI,'2026-10-13')])['observacao']=='Morumbi fechado'
    assert dia('2026-10-13',excecoes=[exc(EINSTEIN,'2026-10-13',False,[('12:00','16:00')])])['observacao']=='Einstein abre 12h–16h'
    assert dia('2026-10-13',excecoes=[exc(EINSTEIN,'2026-10-13',False,[('12:00','15:00'),('19:00','23:30')])])['observacao']=='Einstein abre 12h–15h e 19h–23h30'
    # Duas unidades com exceções diferentes: uma frase por unidade (ordem das unidades), separadas por "; ". Fechamento parcial não é feriado.
    d=dia('2026-10-13',excecoes=[exc(EINSTEIN,'2026-10-13',False,[('12:00','16:00')]),exc(MORUMBI,'2026-10-13',motivo='inventário')])
    assert feriado(d)==(None,None,'Morumbi fechado (inventário); Einstein abre 12h–16h')
    # Todas fechadas pelo mesmo motivo, sem feriado oficial: feriado da empresa, sem observação.
    recesso=[exc(MORUMBI,'2026-12-28',motivo='Recesso de fim de ano'),exc(EINSTEIN,'2026-12-28',motivo='recesso de fim de ano ')]
    assert feriado(dia('2026-12-28',excecoes=recesso))==('Recesso de fim de ano','empresa',None)
    assert feriado(dia('2026-12-28',[MORUMBI],recesso[:1]))==('Recesso de fim de ano','empresa',None)          # empresa de uma unidade só
    # Motivos diferentes, ou só parte das unidades: só observação.
    assert feriado(dia('2026-12-28',excecoes=[recesso[0],exc(EINSTEIN,'2026-12-28',motivo='reforma')]))==(None,None,'Morumbi fechado (Recesso de fim de ano); Einstein fechado (reforma)')
    assert feriado(dia('2026-12-28',excecoes=recesso[:1]))==(None,None,'Morumbi fechado (Recesso de fim de ano)')
    # Todas iguais sem motivo, e todas com o mesmo horário especial.
    assert feriado(dia('2026-12-28',excecoes=[exc(MORUMBI,'2026-12-28'),exc(EINSTEIN,'2026-12-28')]))==(None,None,'Todas as unidades fechadas')
    assert dia('2026-12-24',excecoes=[exc(u,'2026-12-24',False,[('12:00','17:00')],'véspera') for u in (MORUMBI,EINSTEIN)])['observacao']=='Todas as unidades abrem 12h–17h'
    # No feriado: exceção que só repete o feriado não gera texto; com motivo próprio ou horário especial, gera.
    assert feriado(dia('2026-10-12',excecoes=[exc(MORUMBI,'2026-10-12'),exc(EINSTEIN,'2026-10-12',motivo='Feriado')]))==('Nossa Senhora Aparecida','nacional',None)
    assert feriado(dia('2026-10-12',excecoes=[exc(MORUMBI,'2026-10-12',motivo='nossa senhora aparecida')]))==('Nossa Senhora Aparecida','nacional',None)
    assert feriado(dia('2026-10-12',excecoes=[exc(MORUMBI,'2026-10-12',False,[('12:00','16:00')])]))==('Nossa Senhora Aparecida','nacional','Morumbi abre 12h–16h')
    assert feriado(dia('2026-10-12',excecoes=[exc(u,'2026-10-12',motivo='dedetização') for u in (MORUMBI,EINSTEIN)]))==('Nossa Senhora Aparecida','nacional','Todas as unidades fechadas (dedetização)')
    # Exceção de outro dia não aparece; observação longa é cortada.
    assert dia('2026-10-13',excecoes=[exc(MORUMBI,'2026-10-14',motivo='x')])['observacao'] is None
    longa=dia('2026-10-13',excecoes=[exc(MORUMBI,'2026-10-13',motivo='m'*300)])['observacao'];assert len(longa)==160 and longa.endswith('…')


pytestmark_db=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


@pytestmark_db
def test_calendario_no_pacote_fuso_da_empresa_cadastro_da_unidade_e_falha(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            a,can=await criar(client,'alpha');await criar(client,'bravo')
            base='/admin/v1/tenants/alpha/unidades'
            # Cidade e UF no cadastro da unidade (criação, leitura e alteração); UF fora do formato é recusada.
            r=await client.post(base,json={'nome':'Morumbi','codigo':'morumbi','cidade':'São Paulo','uf':'SP'});assert r.status_code==201,r.text
            m=r.json();assert (m['cidade'],m['uf'])==('São Paulo','SP')
            r=await client.post(base,json={'nome':'Einstein','codigo':'einstein'});e=r.json();assert (e['cidade'],e['uf'])==(None,None)
            for metodo in ('put','patch'):
                r=await getattr(client,metodo)(f'{base}/{e["id"]}',json={'nome':'Einstein','cidade':'São Paulo','uf':'SP'})
                if r.status_code!=405: break
            assert r.status_code==200 and (r.json()['cidade'],r.json()['uf'])==('São Paulo','SP'),r.text
            assert (await client.get(f'{base}/{e["id"]}')).json()['cidade']=='São Paulo'
            assert (await getattr(client,metodo)(f'{base}/{e["id"]}',json={'nome':'Einstein','uf':'sp'})).status_code==400
            assert (await getattr(client,metodo)(f'{base}/{e["id"]}',json={'nome':'Einstein','uf':'São Paulo'})).status_code==400
            await conn.execute("INSERT INTO unidade_excecoes(tenant_id,unidade_id,data,fechado,motivo) VALUES ('alpha',$1,'2026-10-13',true,'inventário')",m['id'])
            await conn.execute("INSERT INTO unidade_excecoes(tenant_id,unidade_id,data,fechado,faixas) VALUES ('alpha',$1,'2026-10-14',false,$2::jsonb)",e['id'],'[["12:00","16:00"]]')
            # Exceção de unidade de outra empresa não aparece aqui.
            r=await client.post('/admin/v1/tenants/bravo/unidades',json={'nome':'Outra','codigo':'outra'});assert r.status_code==201
            await conn.execute("INSERT INTO unidade_excecoes(tenant_id,unidade_id,data,fechado,motivo) VALUES ('bravo',$1,'2026-10-12',true,'reforma')",r.json()['id'])

            # 23:55 de 12/10 em São Paulo (02:55 UTC do dia 13): hoje ainda é 12/10, feriado nacional.
            c=await CAL.do_pacote(conn,'alpha',SP,datetime(2026,10,13,2,55,tzinfo=timezone.utc))
            assert c['fuso']==SP and len(c['dias'])==8
            assert c['dias'][0]=={'data':'2026-10-12','dia_semana':'segunda-feira','rotulo':'hoje','feriado':'Nossa Senhora Aparecida','tipo_feriado':'nacional','observacao':None}
            assert (c['dias'][1]['data'],c['dias'][1]['rotulo'],c['dias'][1]['observacao'])==('2026-10-13','amanhã','Morumbi fechado (inventário)')
            assert c['dias'][2]['observacao']=='Einstein abre 12h–16h'
            # 00:05 de 13/10 em São Paulo (03:05 UTC): virou o dia.
            c=await CAL.do_pacote(conn,'alpha',SP,datetime(2026,10,13,3,5,tzinfo=timezone.utc))
            assert [(d['data'],d['dia_semana'],d['rotulo'],d['feriado'],d['observacao']) for d in c['dias'][:2]]==[
                ('2026-10-13','terça-feira','hoje',None,'Morumbi fechado (inventário)'),('2026-10-14','quarta-feira','amanhã',None,'Einstein abre 12h–16h')]
            assert c['dias'][-1]['data']=='2026-10-20'
            # Empresa em outro fuso: no mesmo instante, o dia dela é outro.
            assert (await CAL.do_pacote(conn,'alpha','Asia/Tokyo',datetime(2026,10,12,16,0,tzinfo=timezone.utc)))['dias'][0]['data']=='2026-10-13'
            # A outra empresa vê o que é dela (unidade sem cidade: só nacionais).
            c=await CAL.do_pacote(conn,'bravo',SP,datetime(2026,10,12,15,0,tzinfo=timezone.utc))
            assert (c['dias'][0]['feriado'],c['dias'][0]['observacao'],c['dias'][1]['observacao'])==('Nossa Senhora Aparecida','Outra fechado (reforma)',None)
            # Unidade desativada sai do calendário.
            await conn.execute("UPDATE tenant_unidades SET ativo=false WHERE tenant_id='alpha' AND id=$1",m['id'])
            c=await CAL.do_pacote(conn,'alpha',SP,datetime(2026,10,12,15,0,tzinfo=timezone.utc));assert c['dias'][1]['observacao'] is None

            # No pacote: campo `calendario` na raiz, com hoje no fuso da empresa.
            await ativo(client,'alpha',a,can)
            conv=(await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':'5511900000001','texto':'oi'})).json()['conversation_id']
            repo=Repositorio(conn);cfg=await Configuracoes(repo).efetiva('alpha',a)
            p=await skyone.pacote(repo,'alpha',conv,10**9,'oi',cfg)
            assert set(p['calendario'])=={'fuso','dias'} and len(p['calendario']['dias'])==8 and p['calendario']['dias'][0]['rotulo']=='hoje'
            assert p['calendario']['dias'][0]['data']==p['agora'][:10] and 'calendario' not in p['contexto_modulos']
            # Fonte fora do ar (biblioteca ou banco): o pacote sai com as datas e os dias da semana, sem feriado, e o turno segue.
            def quebrada(*a,**k): raise RuntimeError('fonte fora do ar')
            monkeypatch.setattr(CAL,'oficiais',quebrada)
            c=await CAL.do_pacote(conn,'alpha',SP,datetime(2026,10,12,15,0,tzinfo=timezone.utc))
            assert len(c['dias'])==8 and (c['dias'][0]['data'],c['dias'][0]['dia_semana'],c['dias'][0]['rotulo'],c['dias'][0]['feriado'])==('2026-10-12','segunda-feira','hoje',None)
            p=await skyone.pacote(repo,'alpha',conv,10**9,'oi',cfg);assert len(p['calendario']['dias'])==8 and p['calendario']['dias'][0]['feriado'] is None
    banco(cenario)
