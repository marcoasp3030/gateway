"""CAT2 (docs/ESPEC_CAT2.md): edição do item pelo painel, leitura individual, categorias da unidade,
disponibilidade atual na API admin e prévia sem gravar."""
import json
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import pytest
from modulos.pedidos import agendamento as AG
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar,motivo

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
SP=ZoneInfo('America/Sao_Paulo')
T='bykoji'
ADMIN={'X-Actor':'ana','X-Role':'admin'}
ALMOCO={'dias_semana':['seg','ter','qua','qui','sex'],'exceto_feriado':True,'horario':[['11:30','15:00']]}
RESUMO='segunda a sexta, 11h30–15h, exceto feriados'
CAMPOS={'item_id','codigo','unidade','categoria','nome','descricao','preco','alcoolico','delivery','tipo','observacao','ativo','atualizado_em',
        'disponibilidade','disponivel_agora','indisponivel_motivo','data_referencia',
        # CAT3-a
        'categoria_id','posicao','prato','esgotado_ate','esgotado_motivo','arquivado_em','arquivado'}
def campo(r): return r.json()['erro']['detalhes']['campo']


def test_edicao_do_item_concorrencia_historico_e_carrinho_aberto(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        monkeypatch.setattr(AG,'agora_utc',lambda: datetime(2026,10,13,12,0,tzinfo=SP).astimezone(timezone.utc))
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            edamame=ids['GUE-0001'];url=f'/admin/v1/tenants/{T}/catalogo/itens/{edamame}'
            patch=lambda corpo,headers=ADMIN,u=url: client.patch(u,json=corpo,headers=headers)

            # GET individual: qualquer papel lê; item de outra empresa ou inexistente → 404.
            r=await client.get(url);g=r.json();assert r.status_code==200 and set(g)==CAMPOS
            assert (g['item_id'],g['nome'],g['preco'],g['categoria'],g['ativo'],g['disponivel_agora'],g['indisponivel_motivo'],g['data_referencia'])==(edamame,'Edamame',34.0,'Entradas quentes',True,True,None,'2026-10-13T12:00-03:00')
            await criar(client,'alpha');assert (await client.get(f'/admin/v1/tenants/alpha/catalogo/itens/{edamame}')).status_code==404
            assert (await client.get(f'/admin/v1/tenants/{T}/catalogo/itens/99999999')).status_code==404

            # Só admin e plataforma editam; membro só lê. Empresa conferida no gateway.
            assert (await client.patch(url,json={'nome':'X'},headers={'X-Role':''})).status_code==403 and (await patch({'nome':'X'},{'X-Role':'membro'})).status_code==403
            assert (await patch({'nome':'X'},u=f'/admin/v1/tenants/alpha/catalogo/itens/{edamame}')).status_code==404
            assert (await client.get(url)).json()['nome']=='Edamame'
            # Validações, com o campo no erro. Nada é gravado quando um campo não serve.
            for corpo,qual in (({'nome':''},'nome'),({'nome':'   '},'nome'),({'nome':None},'nome'),({'nome':'n'*121},'nome'),({'nome':12},'nome'),
                               ({'descricao':'d'*301},'descricao'),({'descricao':7},'descricao'),
                               ({'preco':0},'preco'),({'preco':-5},'preco'),({'preco':12.345},'preco'),({'preco':'abc'},'preco'),({'preco':True},'preco'),({'preco':100000},'preco'),
                               ({'ativo':None},'ativo'),({'ativo':'sim'},'ativo'),({'ativo':1},'ativo'),
                               ({'categoria':'Categoria que não existe'},'categoria'),({'categoria':'Sashimi'},'categoria'),({'categoria':None},'categoria'),({'categoria':''},'categoria'),
                               ({'disponibilidade':{'dias_semana':['segunda']}},'disponibilidade'),({'disponibilidade':'seg-sex'},'disponibilidade'),
                               ({},'corpo'),({'atualizado_em':g['atualizado_em']},'corpo'),({'nome':'Ok','preco':0},'preco')):
                r=await patch(corpo);assert (r.status_code,campo(r))==(400,qual),corpo
            # CAT3-a passou a aceitar código, tipo e observação (tests/test_cat3a.py); unidade e id continuam fora.
            for extra in ({'nome':'Ok','unidade':'morumbi'},{'id':5},{'tipo':'outro'},{'observacao':5}):
                assert (await patch(extra)).status_code==400,extra
            assert (await patch({'atualizado_em':'2026-10-13T12:00:00','nome':'Ok'})).status_code==400        # data sem fuso
            assert {k:v for k,v in (await client.get(url)).json().items()}==g

            # Campos independentes: o omitido fica como está. Devolve o item completo atualizado.
            r=await patch({'nome':'  Edamame com flor de sal  '});a=r.json();assert r.status_code==200 and set(a)==CAMPOS
            assert (a['nome'],a['preco'],a['descricao'],a['categoria'],a['ativo'])==('Edamame com flor de sal',34.0,None,'Entradas quentes',True) and a['atualizado_em']!=g['atualizado_em']
            a=(await patch({'descricao':'Vagens de soja no vapor','preco':'36,50'})).json();assert (a['descricao'],a['preco'],a['nome'])==('Vagens de soja no vapor',36.5,'Edamame com flor de sal')
            a=(await patch({'preco':40})).json();assert a['preco']==40.0
            # Categoria: só a que já existe na mesma unidade; sem acento e sem maiúsculas, gravada com a grafia que existe.
            r=await patch({'categoria':'PRATOS QUENTES'});assert r.status_code==200,r.text
            a=r.json();assert a['categoria']=='Pratos quentes' and a['unidade']=='patio_guedala'
            # Era o único item de "Entradas quentes": a categoria deixou de existir na unidade e não dá para voltar por aqui
            # (criar categoria fica para depois).
            r=await patch({'categoria':'Entradas quentes'});assert (r.status_code,campo(r))==(400,'categoria')
            # descricao null (ou só espaços) apaga; preco null deixa sem preço: o item sai do cardápio do agente.
            a=(await patch({'descricao':None})).json();assert a['descricao'] is None
            a=(await patch({'descricao':'   '})).json();assert a['descricao'] is None
            c=await conversa('cliente1');busca=lambda **p: api('GET','/catalogo/busca',params={'conversation_id':c,'unidade':'patio_guedala',**p})
            assert (await busca(categorias='true')).json()['total_itens']==4
            a=(await patch({'preco':None})).json();assert (a['preco'],a['disponivel_agora'],a['indisponivel_motivo'])==(None,False,'sem_preco')
            assert (await busca(categorias='true')).json()['total_itens']==3 and (await busca(q='edamame')).json()['itens'][0]['preco'] is None
            r=await api('POST',f'/carrinho/{c}/itens',json={'item_id':edamame});assert motivo(r)=='sem_preco'
            a=(await patch({'preco':40,'ativo':False})).json();assert (a['ativo'],a['disponivel_agora'],a['indisponivel_motivo'])==(False,False,'item_inativo')
            assert (await busca(q='edamame')).json()['itens']==[]
            a=(await patch({'ativo':True,'disponibilidade':ALMOCO})).json();assert (a['ativo'],a['disponibilidade']['resumo'],a['disponivel_agora'],a['indisponivel_motivo'])==(True,RESUMO,True,None)
            # Mandar o mesmo valor não é alteração: nada muda, nem a data, nem o histórico.
            antes=await conn.fetchval("SELECT count(*) FROM gestao_alteracoes WHERE tenant_id=$1 AND entidade='catalogo_item'",T)
            b=(await patch({'nome':a['nome'],'preco':40.0,'disponibilidade':ALMOCO})).json();assert b['atualizado_em']==a['atualizado_em']
            assert await conn.fetchval("SELECT count(*) FROM gestao_alteracoes WHERE tenant_id=$1 AND entidade='catalogo_item'",T)==antes

            # Concorrência: com atualizado_em antigo → 409 com o item atual, e nada é gravado; com o atual, salva.
            r=await patch({'nome':'Outro nome','atualizado_em':g['atualizado_em']});j=r.json()
            assert r.status_code==409 and (j['erro']['codigo'],j['erro']['detalhes']['campo'],j['erro']['detalhes']['motivo'])==('CONFLICT','atualizado_em','item_alterado')
            assert set(j['item'])==CAMPOS and (j['item']['nome'],j['item']['atualizado_em'])==(a['nome'],a['atualizado_em'])
            r=await patch({'nome':'Edamame','atualizado_em':j['item']['atualizado_em']});assert r.status_code==200 and r.json()['nome']=='Edamame'
            assert (await patch({'nome':'Sem conferência'})).status_code==200 and (await patch({'nome':'Edamame'})).status_code==200          # sem atualizado_em não confere

            # Histórico em /gestao/alteracoes: campos alterados, antes e depois, e quem alterou (X-Actor).
            h=(await client.get(f'/admin/v1/tenants/{T}/gestao/alteracoes',params={'entidade':'catalogo_item','entidade_id':str(edamame),'limite':50})).json()['itens']
            assert all(x['actor']=='ana' and x['acao']=='alterado' for x in h)
            primeira=h[-1];assert (primeira['antes'],primeira['depois'])==({'nome':'Edamame'},{'nome':'Edamame com flor de sal'})
            dupla=h[-2];assert dupla['antes']=={'descricao':None,'preco':34.0} and dupla['depois']=={'descricao':'Vagens de soja no vapor','preco':36.5}
            assert any(x['depois']=={'preco':None} for x in h) and any(x['depois'].get('disponibilidade')==ALMOCO for x in h)
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='catalogo.item_alterado' AND actor='ana'",T)==len(h)

            # Carrinho aberto não é tocado pela edição; o fechar_pedido é que protege: preço mudou → preco_alterado; item desativado → item_inativo.
            tempura=ids['GUE-0002'];ut=f'/admin/v1/tenants/{T}/catalogo/itens/{tempura}'
            assert (await api('POST',f'/carrinho/{c}/itens',json={'item_id':tempura})).status_code==200
            await api('PUT',f'/carrinho/{c}/entrega',json={'modalidade':'retirada'})
            assert (await patch({'preco':160},u=ut)).json()['preco']==160.0
            k=(await api('GET',f'/carrinho/{c}')).json()['carrinho'];assert k['itens'][0]['preco_unitario']==150.0 and k['total']==150.0       # o carrinho ainda tem o preço antigo
            r=await api('POST','/pedidos',json={'conversation_id':c});assert (r.status_code,motivo(r))==(409,'preco_alterado') and r.json()['carrinho']['total']==160.0
            assert (await patch({'ativo':False},u=ut)).json()['ativo'] is False
            r=await api('POST','/pedidos',json={'conversation_id':c});assert (r.status_code,motivo(r))==(422,'item_inativo') and await conn.fetchval('SELECT count(*) FROM pedidos')==0
            assert (await patch({'ativo':True,'nome':'Tempurá de camarão'},u=ut)).status_code==200
            r=await api('POST','/pedidos',json={'conversation_id':c});assert r.status_code==201 and r.json()['pedido']['total']==160.0
            # Pedido já fechado guarda o nome e o preço de quando foi fechado.
            await patch({'nome':'Outro nome','preco':999},u=ut)
            p=(await client.get(f'/admin/v1/tenants/{T}/pedidos/{r.json()["numero"]}')).json();assert (p['itens'][0]['nome'],p['total'])==('Tempurá de camarão',160.0)
    banco(cenario)


def test_disponivel_agora_na_lista_categorias_e_previa(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        relogio=[datetime(2026,10,13,12,0,tzinfo=SP)];monkeypatch.setattr(AG,'agora_utc',lambda: relogio[0].astimezone(timezone.utc))
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            await conn.execute("UPDATE tenant_unidades SET cidade='São Paulo',uf='SP' WHERE tenant_id=$1",T)
            assert (await client.patch(f'/admin/v1/tenants/{T}/catalogo/itens/{ids["GUE-0001"]}',json={'disponibilidade':ALMOCO},headers=ADMIN)).status_code==200
            async def agora():
                itens=(await client.get(f'/admin/v1/tenants/{T}/catalogo/itens',params={'unidade':'patio_guedala'})).json()['itens']
                assert all(set(i)==CAMPOS for i in itens)
                return {i['codigo']:(i['disponivel_agora'],i['indisponivel_motivo']) for i in itens},itens[0]['data_referencia']
            # Terça 12h: o que pode ser vendido está disponível; sem preço, opção do executivo (inativa) e só do salão saem com o motivo.
            a,ref=await agora();assert ref=='2026-10-13T12:00-03:00'
            assert a=={'GUE-0001':(True,None),'GUE-0002':(True,None),'GUE-0003':(False,'sem_preco'),'GUE-0004':(True,None),'GUE-0005':(True,None),
                       'GUE-0006':(False,'item_inativo'),'GUE-0007':(False,'sem_delivery')}
            # Mesma conferência da Skill: horário, dia da semana, feriado nacional e estadual da unidade; ponto facultativo não bloqueia.
            for quando,esperado in ((datetime(2026,10,13,20,36,tzinfo=SP),(False,'horario')),(datetime(2026,10,17,12,0,tzinfo=SP),(False,'dia_da_semana')),
                                    (datetime(2026,10,12,12,0,tzinfo=SP),(False,'feriado')),(datetime(2026,7,9,12,0,tzinfo=SP),(False,'feriado')),
                                    (datetime(2026,2,16,12,0,tzinfo=SP),(True,None))):
                relogio[0]=quando;a,_=await agora();assert a['GUE-0001']==esperado and a['GUE-0002']==(True,None),quando
            relogio[0]=datetime(2026,10,12,12,0,tzinfo=SP)
            g=(await client.get(f'/admin/v1/tenants/{T}/catalogo/itens/{ids["GUE-0001"]}')).json();assert (g['disponivel_agora'],g['indisponivel_motivo'],g['data_referencia'])==(False,'feriado','2026-10-12T12:00-03:00')
            # O painel e a Skill dizem a mesma coisa.
            c=await conversa('cliente1');s=(await api('GET','/catalogo/busca',params={'conversation_id':c,'q':'edamame','unidade':'patio_guedala'})).json()['itens'][0]
            assert (s['disponivel'],s['indisponivel_motivo'])==(g['disponivel_agora'],g['indisponivel_motivo'])

            # Categorias da unidade: ordem do cardápio, com as contagens. Qualquer papel lê.
            r=await client.get(f'/admin/v1/tenants/{T}/catalogo/categorias',params={'unidade':'patio_guedala'});assert r.status_code==200
            # CAT3-a: cada categoria ganhou id, posição e mais contagens; aqui confere o que já existia.
            antigos=('categoria','ordem','qtd_itens','itens_ativos','itens_no_cardapio')
            assert {'unidade':r.json()['unidade'],'categorias':[{k:c[k] for k in antigos} for c in r.json()['categorias']]}=={'unidade':'patio_guedala','categorias':[
                {'categoria':'Entradas quentes','ordem':1,'qtd_itens':1,'itens_ativos':1,'itens_no_cardapio':1},{'categoria':'Pratos quentes','ordem':2,'qtd_itens':1,'itens_ativos':1,'itens_no_cardapio':1},
                {'categoria':'Temakis','ordem':3,'qtd_itens':1,'itens_ativos':1,'itens_no_cardapio':0},{'categoria':'Cervejas','ordem':4,'qtd_itens':1,'itens_ativos':1,'itens_no_cardapio':1},
                {'categoria':'Carpaccios','ordem':5,'qtd_itens':1,'itens_ativos':1,'itens_no_cardapio':1},{'categoria':'Almoço executivo — opções','ordem':6,'qtd_itens':1,'itens_ativos':0,'itens_no_cardapio':0},
                {'categoria':'Sobremesas','ordem':7,'qtd_itens':1,'itens_ativos':1,'itens_no_cardapio':0}]}
            assert [x['categoria'] for x in (await client.get(f'/admin/v1/tenants/{T}/catalogo/categorias',params={'unidade':'morumbi'})).json()['categorias']]==['Sashimi']
            assert (await client.get(f'/admin/v1/tenants/{T}/catalogo/categorias')).status_code==400
            assert (await client.get(f'/admin/v1/tenants/{T}/catalogo/categorias',params={'unidade':'nao_existe'})).status_code==404
            await criar(client,'alpha');assert (await client.get('/admin/v1/tenants/alpha/catalogo/categorias',params={'unidade':'patio_guedala'})).status_code==404

            # Prévia sem gravar: resumo e se valeria agora naquela unidade. Membro pode usar; nada é gravado.
            previa=lambda corpo,**kw: client.post(f'/admin/v1/tenants/{T}/catalogo/disponibilidade/previa',json=corpo,**kw)
            antes=await conn.fetchval("SELECT count(*) FROM gestao_alteracoes")
            r=await previa({'unidade':'patio_guedala','disponibilidade':ALMOCO});assert r.status_code==200
            assert r.json()=={'unidade':'patio_guedala','disponibilidade':{**ALMOCO,'resumo':RESUMO},'resumo':RESUMO,'disponivel_agora':False,'indisponivel_motivo':'feriado','data_referencia':'2026-10-12T12:00-03:00'}
            relogio[0]=datetime(2026,10,13,12,0,tzinfo=SP)
            r=(await previa({'unidade':'patio_guedala','disponibilidade':{'dias_semana':['sab','dom']}},headers={'X-Role':'supervisor'})).json()
            assert (r['resumo'],r['disponivel_agora'],r['indisponivel_motivo'])==('sábado e domingo',False,'dia_da_semana')
            r=(await previa({'unidade':'patio_guedala','disponibilidade':None})).json();assert (r['disponibilidade'],r['resumo'],r['disponivel_agora'],r['indisponivel_motivo'])==(None,None,True,None)
            r=(await previa({'unidade':'patio_guedala','disponibilidade':{'horario':[['19:00','23:00']]}})).json();assert (r['resumo'],r['indisponivel_motivo'])==('19h–23h','horario')
            r=await previa({'unidade':'patio_guedala','disponibilidade':{'dias_semana':['segunda']}});assert (r.status_code,campo(r))==(400,'disponibilidade')
            assert (await previa({'unidade':'nao_existe','disponibilidade':ALMOCO})).status_code==404 and (await previa({'disponibilidade':ALMOCO})).status_code==400
            assert (await previa({'unidade':'patio_guedala','disponibilidade':ALMOCO,'item_id':1})).status_code==400
            assert (await client.post('/admin/v1/tenants/alpha/catalogo/disponibilidade/previa',json={'unidade':'patio_guedala','disponibilidade':ALMOCO})).status_code==404
            assert await conn.fetchval("SELECT count(*) FROM gestao_alteracoes")==antes
            assert json.loads(await conn.fetchval("SELECT disponibilidade FROM catalogo_itens WHERE id=$1",ids['GUE-0001']))==ALMOCO
    banco(cenario)
