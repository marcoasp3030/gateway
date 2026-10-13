"""Fase 4, bloco 1: importação do catálogo por CSV, busca e configuração por unidade."""
import json
from decimal import Decimal
import pytest
from modulos.catalogo import busca as mod_busca
from modulos.catalogo.importacao import ler,ArquivoInvalido,COLUNAS
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo

CAB=','.join(COLUNAS)
BASE=CAB+'''
GUE-0001,patio_guedala,Entradas quentes,Edamame,,34,item,nao,a_confirmar,sim,
GUE-0002,patio_guedala,Pratos quentes,Ebi no Tempurá,Tempurá de camarão,150,item,nao,a_confirmar,sim,
GUE-0003,patio_guedala,Temakis,Toro,,,item,nao,a_confirmar,sim,preço sob consulta
GUE-0004,patio_guedala,Cervejas,Sapporo,,55,item,sim,a_confirmar,sim,
GUE-0005,patio_guedala,Carpaccios,Adicional de azeite trufado,,12,adicional,nao,a_confirmar,sim,
GUE-0006,patio_guedala,Almoço executivo — opções,Sunomono,,,opcao_executivo,nao,a_confirmar,nao,opção do almoço executivo
MOR-0001,morumbi,Aguemono,Tempurá Moriawase,"Tempurá misto de camarão e legumes",,item,nao,a_confirmar,sim,
MOR-0002,morumbi,Sashimi,Shake,Salmão,,item,nao,a_confirmar,sim,
EIN-0001,einstein,Regras,Taxa de serviço — entrega nos quartos,Cobrada em pedidos entregues nos quartos,,regra,nao,a_confirmar,nao,taxa de 13%
'''


def test_ler_csv_precos_bom_e_erros_por_linha():
    itens,erros=ler(('﻿'+BASE).encode('utf-8'))     # com BOM
    assert len(itens)==9 and erros==[]
    por={i['codigo']:i for i in itens}
    # Preço vazio é None, nunca 0; preço preenchido vira decimal com duas casas.
    assert por['GUE-0003']['preco'] is None and por['MOR-0001']['preco'] is None
    assert por['GUE-0001']['preco']==Decimal('34.00') and por['GUE-0004']['alcoolico'] is True and por['GUE-0006']['ativo'] is False
    assert por['MOR-0001']['descricao']=='Tempurá misto de camarão e legumes' and por['GUE-0001']['descricao'] is None
    assert ler(BASE)[0]==itens      # sem BOM e como texto dá o mesmo
    assert ler(CAB+'\nA1,u,C,N,,"124,50",item,nao,sim,sim,\nA2,u,C,N,,R$ 9.9,item,nao,nao,sim,\n')[0][0]['preco']==Decimal('124.50')
    ruins=CAB+'''
X1,patio,C,N,,0,item,nao,sim,sim,
X2,patio,C,N,,-5,item,nao,sim,sim,
X3,patio,C,N,,abc,item,nao,sim,sim,
X4,patio,C,N,,10,combo,nao,sim,sim,
X5,patio,C,N,,10,item,talvez,sim,sim,
X6,patio,C,N,,10,item,nao,quem sabe,sim,
X7,Pátio Guedala,C,N,,10,item,nao,sim,sim,
X8,patio,C,,,10,item,nao,sim,sim,
X9,patio,C,N,,10,item,nao,sim
,patio,C,N,,10,item,nao,sim,sim,
OK1,patio,C,N,,10,item,nao,sim,sim,
OK1,patio,C,N,,10,item,nao,sim,sim,
'''
    itens,erros=ler(ruins)
    assert [i['codigo'] for i in itens]==['OK1'] and len(erros)==11
    assert [e['linha'] for e in erros]==[2,3,4,5,6,7,8,9,10,11,13] and 'zero' in erros[0]['erro'] and 'repetido' in erros[-1]['erro']
    for arquivo,trecho in ((b'','vazio'),('nome,preco\nA,1\n','cabeçalho'),(CAB+'\n','nenhuma linha'),(b'\xff\xfe\x00','UTF-8')):
        with pytest.raises(ArquivoInvalido) as erro: ler(arquivo)
        assert trecho in str(erro.value)


def test_busca_sem_acento_e_tolerante():
    itens=[{'id':i,'nome':n,'descricao':d,'categoria':c} for i,(n,d,c) in enumerate([
        ('Ebi no Tempurá','Tempurá de camarão','Aguemono'),('Tempurá Moriawase','Tempurá misto de camarão e legumes','Aguemono'),
        ('Tempurá Udon','Macarrão com tempurá de camarão','Menrui'),('Shake','Salmão','Sashimi'),('Uramaki Shake','salmão','Makizushi'),
        ('Crème Brûlée de Matchá',None,'Sobremesas'),('Guioza Tradicional',None,'Ipin Mono'),('Água Mineral','Sem e com gás','Bebidas'),
        ('Yakissoba','Macarrão com carnes e legumes','Menrui')],start=1)]
    nomes=lambda q: [i['nome'] for i in mod_busca.buscar(itens,q)]
    assert nomes('tempura')==['Tempurá Udon','Tempurá Moriawase','Ebi no Tempurá'] or set(nomes('tempura'))=={'Tempurá Udon','Tempurá Moriawase','Ebi no Tempurá'}
    assert nomes('TEMPURÁ')==nomes('tempura') and nomes('creme brulee')==['Crème Brûlée de Matchá'] and nomes('agua')==['Água Mineral']
    # Achar no nome vem antes de achar só na descrição.
    assert nomes('salmao')==['Shake','Uramaki Shake'] and nomes('shake')[0]=='Shake'
    # Erro de digitação e começo de palavra.
    assert nomes('yakisoba')==['Yakissoba'] and nomes('tempra')[:1] and 'Guioza Tradicional' in nomes('guiosa') and nomes('temp')==nomes('tempura')
    # Todas as palavras precisam aparecer; sem termo devolve o catálogo (limitado).
    assert nomes('tempura camarao')==['Ebi no Tempurá','Tempurá Moriawase','Tempurá Udon'] or set(nomes('tempura camarao'))=={'Ebi no Tempurá','Tempurá Moriawase','Tempurá Udon'}
    assert nomes('tempura chocolate')==[] and nomes('pizza')==[] and len(nomes(''))==len(itens)
    assert len(mod_busca.buscar([{'id':i,'nome':f'Item {i}','descricao':None,'categoria':'x'} for i in range(100)],'item'))==mod_busca.MAX_RESULTADOS


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_importacao_simulacao_upsert_e_desativacao():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            await criar(client);await criar(client,'bravo')
            url='/admin/v1/tenants/alpha/catalogo/importar'
            n=lambda t='alpha': conn.fetchval('SELECT count(*) FROM catalogo_itens WHERE tenant_id=$1',t)
            # Simulação é o padrão: relata e não grava.
            r=(await client.post(url,content=BASE.encode())).json()
            assert (r['simulacao'],r['aplicado'],r['linhas'],r['inseridos'],r['atualizados'],r['desativados'],r['erros'])==(True,False,9,9,0,0,[]) and await n()==0
            g=r['por_unidade']['patio_guedala']
            assert (g['total'],g['ativos'],g['com_preco'],g['vendaveis'],g['por_tipo'])==(6,5,4,4,{'item':4,'adicional':1,'opcao_executivo':1})
            assert r['por_unidade']['morumbi']['vendaveis']==0 and r['por_unidade']['einstein']['por_tipo']=={'regra':1}
            # Importação real.
            r=(await client.post(url,params={'simulacao':'false'},content=('﻿'+BASE).encode())).json()
            assert (r['aplicado'],r['inseridos'])==(True,9) and await n()==9
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id='alpha' AND acao='catalogo.importado'")==1
            # Preço vazio fica NULO, nunca zero.
            assert await conn.fetchval("SELECT count(*) FROM catalogo_itens WHERE preco=0")==0
            assert await conn.fetchval("SELECT preco FROM catalogo_itens WHERE tenant_id='alpha' AND codigo='GUE-0003'") is None
            ids={r['codigo']:r['id'] for r in await conn.fetch("SELECT codigo,id FROM catalogo_itens WHERE tenant_id='alpha'")}
            # Reimportar o mesmo arquivo não duplica nem muda nada.
            r=(await client.post(url,params={'simulacao':'false'},content=BASE.encode())).json()
            assert (r['inseridos'],r['atualizados'],r['inalterados'],r['desativados'])==(0,0,9,0) and await n()==9
            # Arquivo novo: um preço muda, um item ganha preço, um some, um entra.
            novo=BASE.replace('Edamame,,34,','Edamame,,36,').replace('MOR-0002,morumbi,Sashimi,Shake,Salmão,,','MOR-0002,morumbi,Sashimi,Shake,Salmão,30,')
            novo=novo.replace('GUE-0004,patio_guedala,Cervejas,Sapporo,,55,item,sim,a_confirmar,sim,\n','')+'GUE-0099,patio_guedala,Sobremesas,Purin,,32,item,nao,a_confirmar,sim,\n'
            sim=(await client.post(url,content=novo.encode())).json()
            assert (sim['inseridos'],sim['atualizados'],sim['inalterados'],sim['desativados'])==(1,2,6,1) and await n()==9
            r=(await client.post(url,params={'simulacao':'false'},content=novo.encode())).json()
            assert (r['inseridos'],r['atualizados'],r['desativados'],r['aplicado'])==(1,2,1,True) and await n()==10
            atual={r['codigo']:r for r in await conn.fetch("SELECT * FROM catalogo_itens WHERE tenant_id='alpha'")}
            # Atualiza pelo codigo (mesmo id); o que sumiu fica inativo, não é apagado.
            assert atual['GUE-0001']['preco']==Decimal('36.00') and atual['GUE-0001']['id']==ids['GUE-0001'] and atual['MOR-0002']['preco']==Decimal('30.00')
            assert atual['GUE-0004']['ativo'] is False and atual['GUE-0004']['id']==ids['GUE-0004'] and atual['GUE-0099']['ativo'] is True
            # Item que volta ao arquivo é reativado; o que já estava inativo não conta como desativado de novo.
            r=(await client.post(url,params={'simulacao':'false'},content=BASE.encode())).json()
            assert (r['inseridos'],r['atualizados'],r['desativados'])==(0,3,1) and (await conn.fetchrow("SELECT ativo FROM catalogo_itens WHERE codigo='GUE-0004'"))['ativo']
            # Erro em uma linha: nada é gravado, mesmo pedindo para gravar.
            ruim=BASE+'GUE-0100,patio_guedala,X,Zero,,0,item,nao,sim,sim,\n'
            antes=[tuple(r) for r in await conn.fetch("SELECT codigo,preco,ativo FROM catalogo_itens WHERE tenant_id='alpha' ORDER BY codigo")]
            r=(await client.post(url,params={'simulacao':'false'},content=ruim.encode())).json()
            assert r['aplicado'] is False and r['erros'][0]['codigo']=='GUE-0100' and r['erros'][0]['linha']==11
            assert [tuple(r) for r in await conn.fetch("SELECT codigo,preco,ativo FROM catalogo_itens WHERE tenant_id='alpha' ORDER BY codigo")]==antes
            # Arquivo inválido → 400; empresa inexistente → 404; outra empresa não é tocada.
            assert (await client.post(url,content=b'nome,preco\nA,1\n')).status_code==400
            assert (await client.post('/admin/v1/tenants/nao_existe/catalogo/importar',content=BASE.encode())).status_code==404
            assert await n('bravo')==0
            # Mesmo codigo em outra empresa é outro item.
            assert (await client.post('/admin/v1/tenants/bravo/catalogo/importar',params={'simulacao':'false'},content=BASE.encode())).json()['inseridos']==9
            assert await n('bravo')==9 and await n()==10
            # Resumo e lista administrativa.
            res={(i['unidade'],i['tipo']):i for i in (await client.get('/admin/v1/tenants/alpha/catalogo/resumo')).json()['itens']}
            assert (res[('patio_guedala','item')]['total'],res[('patio_guedala','item')]['vendaveis'],res[('morumbi','item')]['vendaveis'])==(5,3,0)   # GUE-0099 inativo, GUE-0003 sem preço
            lista=(await client.get('/admin/v1/tenants/alpha/catalogo/itens',params={'unidade':'patio_guedala','ativo':'true','q':'tempura'})).json()['itens']
            assert [i['codigo'] for i in lista]==['GUE-0002'] and lista[0]['preco']==150.0
            assert (await client.get('/admin/v1/tenants/alpha/catalogo/itens',params={'tipo':'combo'})).status_code==400
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_busca_do_agente_por_unidade_so_ativos_e_preco_nulo():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            await conn.execute("INSERT INTO modulos_tenant(tenant_id,modulo,ativo) VALUES ('bykoji','catalogo',true) ON CONFLICT DO NOTHING")
            can=(await client.post('/admin/v1/tenants/bykoji/canais',json={'nome':'API','tipo':'api','agente_id':'bykoji_atendimento'})).json()['id']
            await ativo(client,'bykoji','bykoji_atendimento',can)
            conv=(await client.post('/admin/v1/tenants/bykoji/agentes/bykoji_atendimento/simular',json={'canal_id':can,'identificador':'u','texto':'oi','message_id':'m1'})).json()['conversation_id']
            assert (await client.post('/admin/v1/tenants/bykoji/catalogo/importar',params={'simulacao':'false'},content=BASE.encode())).json()['aplicado']
            chave=(await client.post('/admin/v1/tenants/bykoji/api-keys',json={'nome':'skills','escopos':['ferramentas:catalogo']})).json()
            segredo=next(v for k,v in chave.items() if isinstance(v,str) and k in ('chave','api_key','valor','segredo','key'))
            async def busca(headers=None,**params):
                return await client.get('/api/v1/catalogo/busca',params={'conversation_id':conv,**params},headers={'X-API-Key':segredo,'Authorization':'',**(headers or {})})
            r=await busca(q='tempura');assert r.status_code==200,r.text
            itens=r.json()['itens']
            # Sem acento acha "Tempurá" nas duas unidades; cada item traz os campos do contrato.
            assert {i['codigo'] for i in itens}=={'GUE-0002','MOR-0001'}
            assert set(itens[0])=={'item_id','codigo','unidade','categoria','nome','descricao','preco','alcoolico','delivery','tipo','observacao','disponivel','indisponivel_motivo','disponibilidade'}
            por={i['codigo']:i for i in itens}
            assert por['GUE-0002']['preco']==150.0 and por['MOR-0001']['preco'] is None and por['MOR-0001']['delivery']=='a_confirmar'
            # Filtro de unidade.
            assert [i['codigo'] for i in (await busca(q='tempura',unidade='morumbi')).json()['itens']]==['MOR-0001']
            assert (await busca(q='tempura',unidade='einstein')).json()['itens']==[]
            # Só ativos: opção do executivo e regra (ativo=nao) não aparecem; alcoólico vem marcado.
            assert (await busca(q='sunomono')).json()['itens']==[] and (await busca(q='taxa')).json()['itens']==[]
            assert (await busca(q='sapporo')).json()['itens'][0]['alcoolico'] is True
            await conn.execute("UPDATE catalogo_itens SET ativo=false WHERE tenant_id='bykoji' AND codigo='GUE-0002'")
            assert {i['codigo'] for i in (await busca(q='tempura')).json()['itens']}=={'MOR-0001'}
            # Segurança: sem chave 401; conversa inexistente 404; chave sem o escopo 403.
            assert (await client.get('/api/v1/catalogo/busca',params={'conversation_id':conv,'q':'x'},headers={'Authorization':''})).status_code==401
            assert (await busca(conversation_id='bykoji:x:nao-existe')).status_code==404
            outra=(await client.post('/admin/v1/tenants/bykoji/api-keys',json={'nome':'outra','escopos':['notificar']})).json()
            seg2=next(v for k,v in outra.items() if isinstance(v,str) and k in ('chave','api_key','valor','segredo','key'))
            assert (await busca(headers={'X-API-Key':seg2},q='x')).status_code==403
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_configuracao_por_unidade_e_pacote_ao_agente():
    from nucleo.horarios import pacote
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/unidades',json={'nome':'Pátio Guedala','codigo':'patio_guedala','endereco':'Av. Comendador Adibo Ares, 9',
                'horarios':{'seg':[['12:00','15:00'],['19:00','22:00']]},'regras':'Horários do salão.'})
            assert r.status_code==201,r.text
            u=r.json();uid=u['id']
            # Campo não informado fica nulo (nunca 0, nunca texto vazio).
            assert (u['codigo'],u['telefone'],u['whatsapp_pedidos'],u['taxa_entrega'],u['pedido_minimo'],u['aceita_retirada'],u['formas_pagamento'])==('patio_guedala',None,None,None,None,None,None)
            r=await client.patch(f'/admin/v1/tenants/alpha/unidades/{uid}',json={'whatsapp_pedidos':'5511900000010','taxa_entrega':12.5,'aceita_retirada':True,'formas_pagamento':'  '},headers={'X-Role':'admin'})
            u=r.json();assert (u['whatsapp_pedidos'],u['taxa_entrega'],u['aceita_retirada'],u['formas_pagamento'],u['endereco'])==('5511900000010',12.5,True,None,'Av. Comendador Adibo Ares, 9')
            # codigo é único na empresa; o mesmo WhatsApp de pedidos pode atender mais de uma unidade.
            assert (await client.post('/admin/v1/tenants/alpha/unidades',json={'nome':'Outra','codigo':'patio_guedala'})).status_code==409
            assert (await client.post('/admin/v1/tenants/alpha/unidades',json={'nome':'Outra','codigo':'outra','whatsapp_pedidos':'5511900000010'})).status_code==201
            for ruim in ({'codigo':'Pátio'},{'whatsapp_pedidos':'11-9999'},{'taxa_entrega':-1}):
                assert (await client.patch(f'/admin/v1/tenants/alpha/unidades/{uid}',json=ruim)).status_code in (400,422),ruim
            # Pacote ao agente: dados da unidade, nulo para o que não foi informado; o WhatsApp de pedidos não vai.
            p=[u for u in await pacote(conn,'alpha','America/Sao_Paulo') if u['codigo']=='patio_guedala'][0]
            assert (p['codigo'],p['endereco'],p['telefone'],p['taxa_entrega'],p['pedido_minimo'],p['aceita_retirada'],p['regras'])==('patio_guedala','Av. Comendador Adibo Ares, 9',None,12.5,None,True,'Horários do salão.')
            assert 'whatsapp_pedidos' not in p and 'telefone_whatsapp' not in p and p['horarios']=={'seg':[['12:00','15:00'],['19:00','22:00']]} and 'aberta_agora' in p
            json.dumps(p)   # serializável (sem Decimal)
    banco(cenario)
