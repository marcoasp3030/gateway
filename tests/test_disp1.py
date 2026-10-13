"""DISP1 (docs/ESPEC_DISP1.md): disponibilidade do item por dia, feriado e horário; item só entra se foi buscado;
unidade antes do preço; valores não pessoais no registro de Skills; tem_cardapio; paginação de categorias."""
import json
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import pytest
from agentes import skyone
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from modulos.catalogo import disponibilidade as D
from modulos.catalogo.importacao import _disponibilidade,ler,ArquivoInvalido
from modulos.pedidos import agendamento as AG
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar,motivo,CSV
from test_fase4_catalogo import CAB

SP=ZoneInfo('America/Sao_Paulo')
T,A='bykoji','bykoji_atendimento'
ADMIN={'X-Actor':'ana','X-Role':'admin'}
ALMOCO={'dias_semana':['seg','ter','qua','qui','sex'],'exceto_feriado':True,'horario':[['11:30','15:00']]}
RESUMO='segunda a sexta, 11h30–15h, exceto feriados'
def em(dia,hora,minuto=0,mes=10): return datetime(2026,mes,dia,hora,minuto,tzinfo=SP)
def detalhes(r): return r.json()['erro']['detalhes']


def test_validar_resumo_e_avaliar():
    assert D.validar(None) is None and D.validar({}) is None and D.validar({'dias_semana':[],'exceto_feriado':False,'horario':[]}) is None
    assert D.validar({'dias_semana':['sex','seg'],'exceto_feriado':True,'horario':[['19:00','23:00'],['11:30','15:00']]})=={'dias_semana':['seg','sex'],'exceto_feriado':True,'horario':[['11:30','15:00'],['19:00','23:00']]}
    assert D.validar({'dias_semana':list(D.DIAS)}) is None                                   # os sete dias = sem restrição
    for ruim in ({'dias':['seg']},{'dias_semana':['segunda']},{'dias_semana':'seg'},{'exceto_feriado':'sim'},{'horario':[['25:00','26:00']]},
                 {'horario':[['12:00','12:00']]},{'horario':[['11:00','15:00'],['14:00','16:00']]},'seg-sex'):
        with pytest.raises(ValueError): D.validar(ruim)
    assert D.resumo(ALMOCO)==RESUMO and D.resumo(None) is None
    assert D.resumo({'dias_semana':['ter','qua','qui','sex'],'exceto_feriado':True})=='terça a sexta, exceto feriados'
    assert D.resumo({'dias_semana':['seg','qua','sex']})=='segunda, quarta e sexta' and D.resumo({'dias_semana':['sab','dom']})=='sábado e domingo'
    assert D.resumo({'dias_semana':['dom']})=='domingo' and D.resumo({'exceto_feriado':True})=='exceto feriados'
    assert D.resumo({'horario':[['12:00','15:00'],['19:00','22:30']]})=='12h–15h e 19h–22h30'
    assert D.publica(ALMOCO)=={**ALMOCO,'resumo':RESUMO} and D.publica(None) is None
    feriado=lambda dia: 'Nossa Senhora Aparecida' if (dia.month,dia.day)==(10,12) else None
    for quando,esperado in ((em(13,12),(True,None)),(em(13,11,30),(True,None)),(em(13,15),(False,'horario')),(em(13,20,36),(False,'horario')),
                            (em(17,12),(False,'dia_da_semana')),(em(17,20),(False,'dia_da_semana')),(em(12,12),(False,'feriado')),(em(12,20,36),(False,'feriado'))):
        assert D.avaliar(ALMOCO,quando,feriado)==esperado,quando
    assert D.avaliar(None,em(12,3),feriado)==(True,None) and D.avaliar({'exceto_feriado':True},em(12,3),feriado)==(False,'feriado')
    assert D.avaliar({'dias_semana':['sab']},em(17,23),feriado)==(True,None)
    # Faixa que cruza a meia-noite pertence ao dia em que começa: sexta 22h–02h vale no sábado à 01h, não na sexta à 01h.
    noite={'dias_semana':['sex'],'horario':[['22:00','02:00']]}
    assert D.avaliar(noite,em(16,23))==(True,None) and D.avaliar(noite,em(17,1))==(True,None)
    assert D.avaliar(noite,em(16,1))==(False,'dia_da_semana') and D.avaliar(noite,em(16,12))==(False,'horario') and D.avaliar(noite,em(17,23))==(False,'dia_da_semana')


def test_colunas_de_disponibilidade_no_csv():
    assert _disponibilidade('seg-sex','sim','11:30-15:00')==ALMOCO and _disponibilidade('','','') is None
    assert _disponibilidade('ter-sex','sim','')=={'dias_semana':['ter','qua','qui','sex'],'exceto_feriado':True}
    assert _disponibilidade('seg,qua,sex','nao','11:30-15:00;19:00-22:00')=={'dias_semana':['seg','qua','sex'],'horario':[['11:30','15:00'],['19:00','22:00']]}
    for ruim in (('seg-xyz','',''),('sex-seg','',''),('segunda','',''),('','talvez',''),('','','11h30-15h'),('','','11:30-11:30')):
        with pytest.raises(ValueError): _disponibilidade(*ruim)
    novo=CAB+',dias_semana,exceto_feriado,horario\n'
    itens,erros=ler(novo+'A-1,centro,Almoço,Executivo,,90,item,nao,sim,sim,,seg-sex,sim,11:30-15:00\nA-2,centro,Almoço,Outro,,50,item,nao,sim,sim,,,,\n')
    assert not erros and itens[0]['disponibilidade']==ALMOCO and itens[1]['disponibilidade'] is None
    itens,erros=ler(novo+'A-1,centro,Almoço,Executivo,,90,item,nao,sim,sim,,seg-xyz,sim,\n');assert not itens and 'dias_semana' in erros[0]['erro']
    # Cabeçalho antigo continua valendo, e o item vem sem a chave (a disponibilidade cadastrada não é tocada).
    itens,erros=ler(CAB+'\nA-1,centro,Almoço,Executivo,,90,item,nao,sim,sim,\n');assert not erros and 'disponibilidade' not in itens[0]
    with pytest.raises(ArquivoInvalido): ler(CAB+',dias_semana\nA-1,centro,Almoço,Executivo,,90,item,nao,sim,sim,,seg\n')


pytestmark_db=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


@pytestmark_db
def test_disponibilidade_na_busca_no_carrinho_e_no_fechamento(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        relogio=[em(13,12)];monkeypatch.setattr(AG,'agora_utc',lambda: relogio[0].astimezone(timezone.utc))
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            await conn.execute("UPDATE tenant_unidades SET cidade='São Paulo',uf='SP' WHERE tenant_id=$1",T)
            c=await conversa('cliente1');base=f'/carrinho/{c}'
            busca=lambda **p: api('GET','/catalogo/busca',params={'conversation_id':c,**p})
            edamame=ids['GUE-0001'];item=f'/admin/v1/tenants/{T}/catalogo/itens/{edamame}'

            # Contrato admin: só admin ou plataforma; valida; null tira a restrição; item de outra empresa não existe.
            assert (await client.patch(item,json={'disponibilidade':ALMOCO})).status_code==403
            for ruim in ({'disponibilidade':{'dias_semana':['segunda']}},{'disponibilidade':{'horario':[['11:30']]}},{'disponibilidade':ALMOCO,'unidade':'morumbi'},{}):
                assert (await client.patch(item,json=ruim,headers=ADMIN)).status_code==400,ruim
            r=await client.patch(item,json={'disponibilidade':ALMOCO},headers=ADMIN);assert r.status_code==200,r.text
            assert r.json()['disponibilidade']=={**ALMOCO,'resumo':RESUMO} and r.json()['nome']=='Edamame'
            await criar(client,'alpha');assert (await client.patch(f'/admin/v1/tenants/alpha/catalogo/itens/{edamame}',json={'disponibilidade':None},headers=ADMIN)).status_code==404
            lista=(await client.get(f'/admin/v1/tenants/{T}/catalogo/itens',params={'unidade':'patio_guedala'})).json()['itens']
            assert {i['codigo']:i['disponibilidade'] for i in lista}['GUE-0001']['resumo']==RESUMO and {i['codigo']:i['disponibilidade'] for i in lista}['GUE-0002'] is None
            h=await conn.fetchrow("SELECT actor,antes,depois FROM gestao_alteracoes WHERE tenant_id=$1 AND entidade='catalogo_item' ORDER BY id DESC LIMIT 1",T)
            assert h['actor']=='ana' and json.loads(h['depois'])['disponibilidade']==ALMOCO
            assert (await client.patch(item,json={'disponibilidade':None},headers={'X-Role':'plataforma'})).json()['disponibilidade'] is None
            assert (await client.patch(item,json={'disponibilidade':ALMOCO},headers=ADMIN)).status_code==200

            # Busca: terça 12h → disponível; os campos novos existem em todo item (sem restrição = disponível, disponibilidade nula).
            async def edamame_em(quando):
                relogio[0]=quando;r=(await busca(q='edamame',unidade='patio_guedala')).json()
                return r['itens'][0]['disponivel'],r['itens'][0]['indisponivel_motivo'],r['data_referencia']
            assert await edamame_em(em(13,12))==(True,None,'2026-10-13T12:00-03:00')
            r=(await busca(q='tempura',unidade='patio_guedala')).json()['itens'][0];assert (r['disponivel'],r['indisponivel_motivo'],r['disponibilidade'])==(True,None,None)
            # Fora da faixa de horário, fim de semana, feriado nacional; ponto facultativo (Carnaval) não bloqueia.
            assert (await edamame_em(em(13,20,36)))[:2]==(False,'horario') and (await edamame_em(em(17,12)))[:2]==(False,'dia_da_semana')
            assert (await edamame_em(em(12,12)))[:2]==(False,'feriado') and (await edamame_em(em(16,12,mes=2)))[:2]==(True,None)
            # O item indisponível continua aparecendo, com o resumo em palavras, no fim da lista.
            relogio[0]=em(12,20,36);r=(await busca(unidade='patio_guedala')).json()['itens']
            assert r[-1]['nome']=='Edamame' and r[-1]['disponibilidade']=={**ALMOCO,'resumo':RESUMO} and all(i['disponivel'] for i in r[:-1])
            r=(await busca(categoria='entradas quentes',unidade='patio_guedala')).json();assert (r['itens'][0]['disponivel'],r['itens'][0]['indisponivel_motivo'],r['total'])==(False,'feriado',1)
            # Estadual (09/07) vale porque a unidade é de SP; sem cidade na unidade, só os nacionais.
            assert (await edamame_em(datetime(2026,7,9,12,tzinfo=SP)))[:2]==(False,'feriado')
            await conn.execute("UPDATE tenant_unidades SET cidade=NULL,uf=NULL WHERE tenant_id=$1",T)
            assert (await edamame_em(datetime(2026,7,9,12,tzinfo=SP)))[:2]==(True,None) and (await edamame_em(em(12,12)))[:2]==(False,'feriado')
            await conn.execute("UPDATE tenant_unidades SET cidade='São Paulo',uf='SP' WHERE tenant_id=$1",T)

            # adicionar_item no feriado à noite (o caso do piloto): recusa com o motivo, o feriado e quando o item é servido.
            relogio[0]=em(12,20,36)
            r=await api('POST',base+'/itens',json={'item_id':edamame});d=detalhes(r)
            assert (r.status_code,motivo(r),d['campo'])==(422,'item_indisponivel','item_id') and r.json()['carrinho']['itens']==[]
            assert (d['motivo_indisponivel'],d['feriado'],d['resumo'],d['data_referencia'])==('feriado','Nossa Senhora Aparecida',RESUMO,'2026-10-12T20:36-03:00')
            assert d['disponibilidade']=={**ALMOCO,'resumo':RESUMO}
            relogio[0]=em(13,20,36);r=await api('POST',base+'/itens',json={'item_id':edamame})
            assert (motivo(r),detalhes(r)['motivo_indisponivel'],detalhes(r)['feriado'])==('item_indisponivel','horario',None)

            # Vale o dia DO PEDIDO: domingo à noite, pedido agendado para terça 12h30 → o item entra.
            relogio[0]=em(11,20)
            assert (await api('POST',base+'/itens',json={'item_id':ids['GUE-0002']})).status_code==200
            r=await api('PUT',base+'/entrega',json={'modalidade':'retirada','agendado_para':'2026-10-13T12:30'});assert r.status_code==200 and r.json()['itens_indisponiveis']==[]
            r=(await busca(q='edamame',unidade='patio_guedala')).json();assert (r['itens'][0]['disponivel'],r['data_referencia'])==(True,'2026-10-13T12:30-03:00')
            r=await api('POST',base+'/itens',json={'item_id':edamame});assert r.status_code==200,r.text
            k=r.json()['carrinho'];assert [(i['nome'],i['disponivel']) for i in k['itens']]==[('Ebi no Tempurá',True),('Edamame',True)] and k['itens_indisponiveis']==[] and k['pode_fechar']
            # O cliente muda o horário para sábado: definir_entrega aceita e já avisa qual item ficou de fora.
            r=await api('PUT',base+'/entrega',json={'agendado_para':'2026-10-17T12:30'});assert r.status_code==200,r.text
            fora=r.json()['itens_indisponiveis'];k=r.json()['carrinho']
            assert [(f['nome'],f['item_id'],f['motivo'],f['resumo'],f['data_referencia']) for f in fora]==[('Edamame',edamame,'dia_da_semana',RESUMO,'2026-10-17T12:30-03:00')] and fora[0]['id']==k['itens'][1]['id']
            assert k['itens_indisponiveis']==fora and [i['disponivel'] for i in k['itens']]==[True,False] and (k['pode_fechar'],k['faltando'])==(False,['item_indisponivel'])
            # No pacote o carrinho vai igual.
            repo=Repositorio(conn);cfg=await Configuracoes(repo).efetiva(T,A)
            kp=(await skyone.pacote(repo,T,c,10**9,'oi',cfg))['contexto_modulos']['pedidos']['carrinho']
            assert kp['itens_indisponiveis'][0]['nome']=='Edamame' and kp['pode_fechar'] is False
            # fechar_pedido confere de novo e não cria pedido.
            r=await api('POST','/pedidos',json={'conversation_id':c});d=detalhes(r)
            assert (r.status_code,motivo(r))==(422,'item_indisponivel') and [f['nome'] for f in d['itens']]==['Edamame'] and (d['resumo'],d['motivo_indisponivel'])==(RESUMO,'dia_da_semana')
            assert await conn.fetchval('SELECT count(*) FROM pedidos')==0
            # Mudar quantidade de quem já está no carrinho não é barrado; voltar para um dia que serve fecha o pedido.
            assert (await api('PATCH',f'{base}/itens/{k["itens"][1]["id"]}',json={'quantidade':2})).status_code==200
            r=await api('PUT',base+'/entrega',json={'agendado_para':'2026-10-14T12:00'});assert r.json()['itens_indisponiveis']==[] and r.json()['carrinho']['pode_fechar']
            r=await api('POST','/pedidos',json={'conversation_id':c});assert r.status_code==201 and r.json()['numero']
            # Horário válido ao marcar e item fora ao fechar (o relógio andou): sem agendamento, vale agora.
            c2=await conversa('cliente2');relogio[0]=em(13,14,50)
            assert (await api('POST',f'/carrinho/{c2}/itens',json={'item_id':edamame})).status_code==200
            await api('PUT',f'/carrinho/{c2}/entrega',json={'modalidade':'retirada'});relogio[0]=em(13,15,10)
            r=await api('POST','/pedidos',json={'conversation_id':c2});assert (motivo(r),detalhes(r)['motivo_indisponivel'])==('item_indisponivel','horario')
            # Feriado da empresa (todas as unidades fechadas pelo mesmo motivo) conta como feriado; item sem restrição nunca é barrado.
            uid=await conn.fetchval("SELECT id FROM tenant_unidades WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
            await conn.execute("INSERT INTO unidade_excecoes(tenant_id,unidade_id,data,fechado,motivo) VALUES ($1,$2,'2026-10-14',true,'Recesso')",T,uid)
            assert (await edamame_em(em(14,12)))[:2]==(False,'feriado')
            assert (await api('POST',f'/carrinho/{c2}/itens',json={'item_id':ids['GUE-0002']})).status_code==200
    banco(cenario)


@pytestmark_db
def test_item_nao_buscado_unidade_antes_do_preco_registro_e_tem_cardapio():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            c1=await conversa('cliente1');c2=await conversa('cliente2')
            add=lambda conv,codigo,**kw: api('POST',f'/carrinho/{conv}/itens',buscado=False,json={'item_id':ids[codigo] if isinstance(codigo,str) else codigo,**kw})
            busca=lambda conv,**p: api('GET','/catalogo/busca',params={'conversation_id':conv,**p})
            await conn.execute("INSERT INTO catalogo_itens(tenant_id,codigo,unidade,categoria,nome,tipo,delivery) VALUES ($1,'MOR-0002','morumbi','Almoço executivo','Almoço executivo','item','sim')",T)
            ids['MOR-0002']=await conn.fetchval("SELECT id FROM catalogo_itens WHERE tenant_id=$1 AND codigo='MOR-0002'",T)

            # Id que não veio de busca desta conversa: recusa sem dizer nada sobre o item (o caso do turno 894).
            r=await add(c1,'MOR-0002',quantidade=10);d=detalhes(r)
            assert (r.status_code,motivo(r),d['campo'],d['janela_min'])==(422,'item_nao_buscado','item_id',30) and 'buscar_catalogo' in r.json()['erro']['mensagem']
            assert 'Almoço' not in r.text and 'morumbi' not in r.text and set(d)=={'campo','motivo','janela_min'} and r.json()['carrinho']['itens']==[]
            assert motivo(await add(c1,'GUE-0002'))=='item_nao_buscado'
            r=await add(c1,999999);assert (r.status_code,motivo(r))==(404,'item_inexistente')                # id que não existe continua 404
            # Buscado em OUTRA conversa não vale.
            assert [i['nome'] for i in (await busca(c2,q='tempura',unidade='patio_guedala')).json()['itens']]==['Ebi no Tempurá']
            assert motivo(await add(c1,'GUE-0002'))=='item_nao_buscado' and (await add(c2,'GUE-0002')).status_code==200
            # Buscado por texto, por categoria (qualquer página) e sem unidade: tudo conta.
            await busca(c1,q='tempura',unidade='patio_guedala');assert (await add(c1,'GUE-0002')).status_code==200
            await busca(c1,categoria='cervejas',unidade='patio_guedala',pagina=1);assert (await add(c1,'GUE-0004')).status_code==200
            await busca(c1,q='edamame')
            # Passou da janela de 30 minutos: precisa buscar de novo. Item que já está no carrinho dispensa.
            await conn.execute("UPDATE catalogo_vistos SET visto_em=now()-interval '31 minutes' WHERE conversation_id=$1",c1)
            assert motivo(await add(c1,'GUE-0001'))=='item_nao_buscado'
            r=await add(c1,'GUE-0002');assert r.status_code==200 and r.json()['carrinho']['itens'][0]['quantidade']==2
            await conn.execute("UPDATE catalogo_vistos SET visto_em=now()-interval '29 minutes' WHERE conversation_id=$1",c1)
            assert (await add(c1,'GUE-0001')).status_code==200
            # Pedido anterior não dispensa: fechado o pedido, o mesmo item exige buscar de novo.
            await api('PUT',f'/carrinho/{c2}/entrega',json={'modalidade':'retirada'});assert (await api('POST','/pedidos',json={'conversation_id':c2})).status_code==201
            await conn.execute("DELETE FROM catalogo_vistos WHERE conversation_id=$1",c2);assert motivo(await add(c2,'GUE-0002'))=='item_nao_buscado'

            # Unidade antes do preço: com carrinho do Pátio Guedala, item do Morumbi SEM preço devolve unidade_diferente (com as duas unidades), não sem_preco.
            await busca(c1,q='almoço executivo');await busca(c1,q='shake')
            for codigo in ('MOR-0002','MOR-0001'):
                r=await add(c1,codigo);d=detalhes(r)
                assert (r.status_code,motivo(r),d['unidade_do_carrinho'],d['unidade_do_item'])==(422,'unidade_diferente','patio_guedala','morumbi'),codigo
            # Sem carrinho (nem unidade definida), o mesmo item sem preço, depois de buscado, continua sendo sem_preco.
            c3=await conversa('cliente3');await busca(c3,q='almoço executivo');assert motivo(await add(c3,'MOR-0002'))=='sem_preco'
            # Unidade definida por definir_entrega (carrinho ainda sem item) também vale.
            await api('PUT',f'/carrinho/{c3}/entrega',json={'unidade':'patio_guedala'});assert motivo(await add(c3,'MOR-0002'))=='unidade_diferente'
            await busca(c3,q='edamame');await add(c3,'GUE-0001')
            assert (await client.post(f'/admin/v1/tenants/{T}/unidades',json={'nome':'Morumbi','codigo':'morumbi'})).status_code==201
            r=await api('PUT',f'/carrinho/{c3}/entrega',json={'unidade':'morumbi'});assert (motivo(r),detalhes(r)['unidade_do_carrinho'],detalhes(r)['unidade_do_item'])==('unidade_diferente','patio_guedala','morumbi')

            # Registro das Skills: valores que não são dado de cliente (item, unidade, nome do catálogo, quantidade, página); nunca q, endereço ou observação.
            await conn.execute('DELETE FROM skill_registro');c4=await conversa('cliente4')
            await busca(c4,q='tempura secreto',unidade='patio_guedala');await busca(c4,q='tempura',unidade='patio_guedala')
            await busca(c4,categorias='true',unidade='patio_guedala',pagina=1);await busca(c4,categoria='Cervejas',unidade='patio_guedala',pagina=2)
            await add(c4,'GUE-0002',quantidade=3,observacao='sem cebola por favor');await add(c4,'MOR-0002',quantidade=10);await add(c4,999999)
            linha=(await api('GET',f'/carrinho/{c4}')).json()['carrinho']['itens'][0]['id']
            await api('PATCH',f'/carrinho/{c4}/itens/{linha}',json={'quantidade':5,'observacao':'bem passado'})
            await api('PUT',f'/carrinho/{c4}/entrega',json={'modalidade':'delivery','endereco':'Rua Secreta, 99','horario':'o quanto antes','observacao':'interfone 12'})
            await api('GET','/unidades',params={'conversation_id':c4,'unidade':'patio_guedala'})
            numero=(await api('POST','/pedidos',json={'conversation_id':c4})).json()['numero']
            await api('GET',f'/pedidos/{numero}',params={'conversation_id':c4})
            rows=[(r['skill'],r['status'],r['motivo'],json.loads(r['ref']) if r['ref'] else None) for r in await conn.fetch('SELECT skill,status,motivo,ref FROM skill_registro ORDER BY id')]
            tempura=ids['GUE-0002']
            assert rows==[
                ('buscar_catalogo',200,'ok',{'unidade':'patio_guedala','categorias':False,'qtd_resultados':0}),
                ('buscar_catalogo',200,'ok',{'unidade':'patio_guedala','categorias':False,'qtd_resultados':1}),
                ('buscar_catalogo',200,'ok',{'unidade':'patio_guedala','categorias':True,'pagina':1,'qtd_resultados':4}),
                ('buscar_catalogo',200,'ok',{'unidade':'patio_guedala','categorias':False,'categoria':'Cervejas','pagina':2,'qtd_resultados':0}),
                ('adicionar_item',200,'ok',{'item_id':tempura,'item_unidade':'patio_guedala','item_nome':'Ebi no Tempurá','quantidade':3}),
                ('adicionar_item',422,'item_nao_buscado',{'item_id':ids['MOR-0002'],'item_unidade':'morumbi','item_nome':'Almoço executivo','quantidade':10}),
                ('adicionar_item',404,'item_inexistente',{'item_id':999999,'quantidade':1}),
                ('ver_carrinho',200,'ok',None),
                ('alterar_item',200,'ok',{'linha_id':linha,'item_id':tempura,'item_unidade':'patio_guedala','item_nome':'Ebi no Tempurá','quantidade':5}),
                ('definir_entrega',200,'ok',{'modalidade':'delivery'}),
                ('info_unidades',200,'ok',{'unidade':'patio_guedala'}),
                ('fechar_pedido',201,'ok',{'numero':numero}),('consultar_pedido',200,'ok',{'numero':numero})]
            tudo=json.dumps(rows,ensure_ascii=False);assert not any(x in tudo for x in ('secreto','Rua Secreta','sem cebola','bem passado','interfone'))
            r=(await client.get(f'/admin/v1/tenants/{T}/skills/registro',params={'conversation_id':c4,'skill':'adicionar_item'})).json()['itens']
            assert [i['ref'].get('item_nome') for i in r]==[None,'Almoço executivo','Ebi no Tempurá'] and r[1]['motivo']=='item_nao_buscado'
            # O registro curto (turno refeito) também guarda o item_id.
            assert json.loads(await conn.fetchval("SELECT dados FROM skill_chamadas WHERE conversation_id=$1 AND skill='adicionar_item' AND resultado='ok'",c4))=={'item':'Ebi no Tempurá','quantidade':3,'item_id':tempura}

            # tem_cardapio e itens_no_cardapio: no pacote e em info_unidades. Unidade sem item com preço = false.
            assert (await client.post(f'/admin/v1/tenants/{T}/unidades',json={'nome':'Einstein','codigo':'einstein'})).status_code==201
            await conn.execute("UPDATE catalogo_itens SET preco=NULL WHERE tenant_id=$1 AND codigo='MOR-0001'",T)
            un={u['codigo']:(u['tem_cardapio'],u['itens_no_cardapio']) for u in (await api('GET','/unidades',params={'conversation_id':c4})).json()['unidades']}
            assert un=={'patio_guedala':(True,4),'morumbi':(False,0),'einstein':(False,0)}
            repo=Repositorio(conn);cfg=await Configuracoes(repo).efetiva(T,A)
            p=await skyone.pacote(repo,T,c4,10**9,'oi',cfg);assert {u['codigo']:(u['tem_cardapio'],u['itens_no_cardapio']) for u in p['unidades']}==un
            r=(await busca(c4,categorias='true',unidade='einstein')).json();assert (r['tem_cardapio'],r['categorias'],r['total_categorias'],r['total_itens'])==(False,[],0,0)
            assert (await busca(c4,categorias='true',unidade='patio_guedala')).json()['tem_cardapio'] is True
    banco(cenario)


@pytestmark_db
def test_paginacao_de_categorias_e_csv_com_disponibilidade():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            c=await conversa('cliente1')
            cats=lambda **p: api('GET','/catalogo/busca',params={'conversation_id':c,'categorias':'true','unidade':'patio_guedala',**p})
            for n in range(1,21):
                await conn.execute("INSERT INTO catalogo_itens(tenant_id,codigo,unidade,categoria,nome,preco,tipo,delivery) VALUES ($1,$2,'patio_guedala',$3,$4,10,'item','sim')",T,f'X-{n:02d}',f'Zeta {21-n:02d}',f'Item {n:02d}')
            # Ordem do cardápio importado (posição do primeiro item de cada categoria), não alfabética: "Zeta 20" entrou antes de "Zeta 01".
            ordem=['Entradas quentes','Pratos quentes','Cervejas','Carpaccios']+[f'Zeta {21-n:02d}' for n in range(1,21)]
            # Sem `pagina`: todas, como sempre, já com os campos novos.
            r=(await cats()).json();assert [x['categoria'] for x in r['categorias']]==ordem and (r['total_categorias'],r['pagina'],r['por_pagina'],r['tem_mais'],r['total_itens'])==(24,None,None,False,24)
            # Com `pagina`: 9 por vez (a 10ª linha da lista do WhatsApp fica para "Ver mais categorias").
            vistas=[]
            for pagina,quantas,mais in ((1,9,True),(2,9,True),(3,6,False),(4,0,False)):
                r=(await cats(pagina=pagina)).json()
                assert (len(r['categorias']),r['tem_mais'],r['pagina'],r['por_pagina'],r['total_categorias'],r['total_itens'])==(quantas,mais,pagina,9,24,24),pagina
                vistas+=[x['categoria'] for x in r['categorias']]
            assert vistas==ordem and (await cats(pagina=0)).status_code==400
            assert (await cats(pagina=1)).json()['categorias'][0]=={'categoria':'Entradas quentes','qtd_itens':1}
            # A paginação dos itens de uma categoria continua em 10, com a página 1 como padrão.
            r=(await api('GET','/catalogo/busca',params={'conversation_id':c,'categoria':'zeta','unidade':'patio_guedala'})).json();assert (r['pagina'],r['por_pagina'],r['total'],r['tem_mais'],len(r['itens']))==(1,10,20,True,10)

            # CSV com as três colunas opcionais: grava a disponibilidade; célula vazia = sem restrição.
            importar=lambda corpo,**p: client.post(f'/admin/v1/tenants/{T}/catalogo/importar',params={'simulacao':'false',**p},content=corpo.encode())
            linhas=CSV.strip('\n').split('\n')
            novo='\n'.join([linhas[0]+',dias_semana,exceto_feriado,horario']+[l+(',ter-sex,sim,11:30-15:00;19:00-22:00' if l.startswith('GUE-0002') else ',,,') for l in linhas[1:]])+'\n'
            r=(await importar(novo)).json();assert r['aplicado'] and r['erros']==[] and r['atualizados']==1,r
            disp=lambda codigo: conn.fetchval("SELECT disponibilidade FROM catalogo_itens WHERE tenant_id=$1 AND codigo=$2",T,codigo)
            assert json.loads(await disp('GUE-0002'))=={'dias_semana':['ter','qua','qui','sex'],'exceto_feriado':True,'horario':[['11:30','15:00'],['19:00','22:00']]} and await disp('GUE-0001') is None
            # Arquivo com o cabeçalho antigo: aceito, e NÃO apaga a disponibilidade cadastrada.
            r=(await importar(CSV)).json();assert r['aplicado'] and r['atualizados']==0 and json.loads(await disp('GUE-0002'))['dias_semana']==['ter','qua','qui','sex']
            # Com as colunas e a célula vazia: tira a restrição. Valor inválido: erro na linha e nada é gravado.
            ruim=novo.replace('ter-sex,sim','ter-xyz,sim');r=(await importar(ruim)).json();assert not r['aplicado'] and 'dias_semana' in r['erros'][0]['erro'] and await disp('GUE-0002') is not None
            limpo=novo.replace(',ter-sex,sim,11:30-15:00;19:00-22:00',',,,');r=(await importar(limpo)).json();assert r['aplicado'] and r['atualizados']==1 and await disp('GUE-0002') is None
    banco(cenario)
