"""CAT3-b (docs/ESPEC_CAT3.md, seção 6): opções do item — cadastro no painel, grupos na busca, escolhas no adicionar_item e no
alterar_item, recusas novas, carrinho e pedido com o preço final e a cópia das escolhas, texto para a unidade."""
from test_cat3a import cenario_cat3,det,ADMIN,MEMBRO,CAT,T
from test_fase4_pedidos import motivo
from modulos.pedidos import avisos


MOLHO={'nome':'Escolha o molho','minimo':1,'maximo':1,'opcoes':[{'nome':'Tarê','preco_extra':0},{'nome':'Trufado','preco_extra':'6,00'}]}
EXTRAS={'nome':'Adicionais','minimo':0,'maximo':3,'repetir':True,'opcoes':[{'nome':'Cream cheese','preco_extra':4},{'nome':'Gergelim','preco_extra':0,'ativo':False}]}


def test_cadastro_no_painel_validacao_conflito_e_aplicar_em(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        ed=ids['GUE-0001'];url=f'{CAT}/itens/{ed}/opcoes'
        vazio=(await client.get(url)).json();assert vazio=={'item_id':ed,'atualizado_em':None,'grupos':[]}
        assert (await client.put(url,json={'grupos':[MOLHO]},headers=MEMBRO)).status_code==403
        for ruim,campo in (({'grupos':[{**MOLHO,'minimo':2}]},'grupos[0].minimo'),({'grupos':[{**MOLHO,'maximo':21}]},'grupos[0].minimo'),
                           ({'grupos':[{**MOLHO,'opcoes':[]}]},'grupos[0].opcoes'),({'grupos':[MOLHO]*11},'grupos'),
                           ({'grupos':[{**MOLHO,'opcoes':[{'nome':'X','preco_extra':-1}]}]},'grupos[0].opcoes[0].preco_extra'),
                           ({'grupos':[{**MOLHO,'opcoes':[{'nome':'X'}]*31}]},'grupos[0].opcoes'),({'grupos':[MOLHO],'outro':1},'corpo')):
            r=await client.put(url,json=ruim,headers=ADMIN);assert (r.status_code,det(r)['campo'])==(400,campo),ruim
        r=await client.put(url,json={'grupos':[MOLHO,EXTRAS]},headers=ADMIN);assert r.status_code==200,r.text
        g=r.json();molho,extras=g['grupos']
        assert (molho['obrigatorio'],molho['minimo'],molho['maximo'],[o['nome'] for o in molho['opcoes']],molho['opcoes'][1]['preco_extra'])==(True,1,1,['Tarê','Trufado'],6.0)
        assert (extras['obrigatorio'],extras['repetir'],extras['opcoes'][1]['ativo'])==(False,True,False)
        # Edição com ids mantém os ids; atualizado_em antigo → 409 com o conjunto atual.
        r=await client.put(url,json={'grupos':[{**MOLHO,'id':molho['id'],'opcoes':[{'id':molho['opcoes'][0]['id'],'nome':'Tarê da casa','preco_extra':0}]}],
                                     'atualizado_em':g['atualizado_em']},headers=ADMIN)
        assert r.status_code==200 and r.json()['grupos'][0]['id']==molho['id'] and r.json()['grupos'][0]['opcoes'][0]['id']==molho['opcoes'][0]['id']
        assert len(r.json()['grupos'])==1
        r=await client.put(url,json={'grupos':[MOLHO],'atualizado_em':g['atualizado_em']},headers=ADMIN)
        assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(409,'opcoes_alteradas') and r.json()['opcoes']['grupos'][0]['opcoes'][0]['nome']=='Tarê da casa'
        # Id de grupo de outro item é recusado.
        r=await client.put(f'{CAT}/itens/{ids["GUE-0002"]}/opcoes',json={'grupos':[{**MOLHO,'id':molho['id']}]},headers=ADMIN);assert det(r)['motivo']=='grupo_de_outro_item'
        # aplicar_em: copia o conjunto para os itens do mesmo prato.
        sashimi=await conn.fetchval("SELECT id FROM catalogo_categorias WHERE tenant_id=$1 AND unidade='morumbi'",T)
        mor=(await client.post(f'{CAT}/itens',json={'unidade':'morumbi','categoria_id':sashimi,'nome':'Edamame','preco':30},headers=ADMIN)).json()['item_id']
        assert (await client.post(f'{CAT}/pratos',json={'itens':[ed,mor]},headers=ADMIN)).status_code==201
        r=await client.put(url,json={'grupos':[MOLHO,EXTRAS],'aplicar_em':'todas'},headers=ADMIN)
        assert r.json()['aplicado_em']==[{'unidade':'morumbi','item_id':mor,'ok':True}]
        copia=(await client.get(f'{CAT}/itens/{mor}/opcoes')).json()['grupos']
        assert [g['nome'] for g in copia]==['Escolha o molho','Adicionais'] and copia[0]['id']!=r.json()['grupos'][0]['id']
        assert await conn.fetchval("SELECT count(*) FROM gestao_alteracoes WHERE tenant_id=$1 AND entidade='catalogo_opcoes'",T)==4
    cenario_cat3(monkeypatch,corpo)


def test_busca_escolhas_recusas_carrinho_pedido_e_unidade(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        ed=ids['GUE-0001']
        g=(await client.put(f'{CAT}/itens/{ed}/opcoes',json={'grupos':[MOLHO,EXTRAS]},headers=ADMIN)).json()['grupos']
        molho,extras=g;tare,trufado=[o['id'] for o in molho['opcoes']];cream,gergelim=[o['id'] for o in extras['opcoes']]
        c=await conversa('cliente-op')
        # Busca: grupos e opções ativos (o gergelim, inativo, não vem); item sem grupo vem com lista vazia.
        itens={i['codigo']:i for i in (await api('GET','/catalogo/busca',params={'conversation_id':c,'q':'edamame tempura'})).json()['itens']}
        itens.update({i['codigo']:i for i in (await api('GET','/catalogo/busca',params={'conversation_id':c,'q':'tempura'})).json()['itens']})
        assert itens['GUE-0002']['grupos']==[]
        busca=(await api('GET','/catalogo/busca',params={'conversation_id':c,'q':'edamame'})).json()['itens'][0]['grupos']
        assert busca==[{'grupo_id':molho['id'],'nome':'Escolha o molho','obrigatorio':True,'minimo':1,'maximo':1,'repetir':False,
                        'opcoes':[{'opcao_id':tare,'nome':'Tarê','preco_extra':0.0,'disponivel':True},{'opcao_id':trufado,'nome':'Trufado','preco_extra':6.0,'disponivel':True}]},
                       {'grupo_id':extras['id'],'nome':'Adicionais','obrigatorio':False,'minimo':0,'maximo':3,'repetir':True,
                        'opcoes':[{'opcao_id':cream,'nome':'Cream cheese','preco_extra':4.0,'disponivel':True}]}]
        add=lambda **kw: api('POST',f'/carrinho/{c}/itens',json={'item_id':ed,**kw})
        # Recusas: o grupo completo vem nos detalhes para o agente perguntar.
        r=await add();assert (r.status_code,motivo(r))==(422,'opcao_obrigatoria')
        d=r.json()['erro']['detalhes'];assert d['grupo_id']==molho['id'] and d['grupo']==busca[0] and d['campo']=='opcoes'
        r=await add(opcoes=[{'grupo_id':molho['id'],'opcao_id':tare},{'grupo_id':molho['id'],'opcao_id':trufado}]);assert motivo(r)=='opcoes_acima_do_maximo'
        r=await add(opcoes=[{'grupo_id':molho['id'],'opcao_id':tare,'quantidade':2}]);assert motivo(r)=='opcao_repetida'
        r=await add(opcoes=[{'grupo_id':molho['id'],'opcao_id':cream}]);assert motivo(r)=='opcao_inexistente' and r.json()['erro']['detalhes']['grupo_id']==molho['id']
        r=await add(opcoes=[{'grupo_id':extras['id'],'opcao_id':gergelim},{'grupo_id':molho['id'],'opcao_id':tare}]);assert motivo(r)=='opcao_inexistente'
        r=await add(opcoes=[{'grupo_id':999999,'opcao_id':tare}]);assert motivo(r)=='opcao_inexistente'
        assert (await add(opcoes=[{'grupo_id':molho['id']}])).status_code==400
        assert r.json()['carrinho']['itens']==[]
        # Escolha certa: preço unitário = base + extras; as escolhas copiadas na linha.
        r=await add(quantidade=2,opcoes=[{'grupo_id':molho['id'],'opcao_id':trufado},{'grupo_id':extras['id'],'opcao_id':cream,'quantidade':2}]);assert r.status_code==200,r.text
        [l]=r.json()['carrinho']['itens']
        assert (l['preco_base'],l['preco_unitario'],l['subtotal'],l['quantidade'])==(34.0,48.0,96.0,2)
        assert l['opcoes']==[{'grupo_id':molho['id'],'grupo':'Escolha o molho','opcao_id':trufado,'opcao':'Trufado','quantidade':1,'preco_extra':6.0},
                             {'grupo_id':extras['id'],'grupo':'Adicionais','opcao_id':cream,'opcao':'Cream cheese','quantidade':2,'preco_extra':4.0}]
        # Mesmas escolhas somam; escolhas diferentes viram outra linha.
        r=await add(opcoes=[{'grupo_id':extras['id'],'opcao_id':cream,'quantidade':2},{'grupo_id':molho['id'],'opcao_id':trufado}])
        assert [x['quantidade'] for x in r.json()['carrinho']['itens']]==[3]
        r=await add(opcoes=[{'grupo_id':molho['id'],'opcao_id':tare}]);its=r.json()['carrinho']['itens']
        assert [(x['quantidade'],x['preco_unitario']) for x in its]==[(3,48.0),(1,34.0)] and r.json()['carrinho']['subtotal']==178.0
        # alterar_item: opcoes substitui todas as escolhas da linha.
        linha=its[1]['id']
        r=await api('PATCH',f'/carrinho/{c}/itens/{linha}',json={'opcoes':[{'grupo_id':molho['id'],'opcao_id':trufado}]});assert r.status_code==200,r.text
        x=r.json()['carrinho']['itens'][1];assert (x['preco_unitario'],[o['opcao'] for o in x['opcoes']])==(40.0,['Trufado'])
        r=await api('PATCH',f'/carrinho/{c}/itens/{linha}',json={'opcoes':[]});assert motivo(r)=='opcao_obrigatoria' and r.json()['erro']['detalhes']['linha_id']==linha
        r=await api('PATCH',f'/carrinho/{c}/itens/{linha}',json={'quantidade':2});assert r.json()['carrinho']['itens'][1]['opcoes'][0]['opcao']=='Trufado'
        # Fechar revalida: preço extra mudou → preco_alterado com o carrinho refeito.
        await api('PUT',f'/carrinho/{c}/entrega',json={'unidade':'patio_guedala','modalidade':'retirada'})
        await conn.execute("UPDATE tenant_unidades SET whatsapp_pedidos='5511900000091' WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        await conn.execute('UPDATE catalogo_opcoes SET preco_extra=7 WHERE tenant_id=$1 AND id=$2',T,trufado)
        r=await api('POST','/pedidos',json={'conversation_id':c});assert (r.status_code,motivo(r))==(409,'preco_alterado')
        assert [x['preco_unitario'] for x in r.json()['carrinho']['itens']]==[49.0,41.0]
        r=await api('POST','/pedidos',json={'conversation_id':c});assert r.status_code==201,r.text
        p=r.json()['pedido'];assert p['total']==3*49+2*41
        assert p['itens'][0]['opcoes'][0]=={'grupo_id':molho['id'],'grupo':'Escolha o molho','opcao_id':trufado,'opcao':'Trufado','quantidade':1,'preco_extra':7.0}
        assert p['itens'][0]['preco_base']==34.0
        # Texto para a unidade: as escolhas abaixo de cada item.
        texto=await conn.fetchval("SELECT texto FROM avisos_internos WHERE tenant_id=$1 AND tipo='pedido_unidade' ORDER BY id DESC LIMIT 1",T)
        assert '3× Edamame (R$ 49,00) = R$ 147,00\n   + Trufado (Escolha o molho) +R$ 7,00\n   + 2× Cream cheese (Adicionais) +R$ 4,00' in texto
        # O pedido guarda a cópia: mudar a opção depois não muda o pedido.
        await conn.execute("UPDATE catalogo_opcoes SET nome='Outro' WHERE tenant_id=$1 AND id=$2",T,trufado)
        assert (await client.get(f'/admin/v1/tenants/{T}/pedidos/{p["numero"]}')).json()['itens'][0]['opcoes'][0]['opcao']=='Trufado'
    cenario_cat3(monkeypatch,corpo)


def test_grupo_novo_obrigatorio_depois_do_item_no_carrinho(monkeypatch):
    """Item entrou sem opções; a empresa cadastrou um grupo obrigatório depois: o fechamento recusa com o grupo, e o alterar_item resolve."""
    async def corpo(conn,client,api,ids,conversa,relogio):
        ed=ids['GUE-0001'];c=await conversa('cliente-depois')
        assert (await api('POST',f'/carrinho/{c}/itens',json={'item_id':ed})).status_code==200
        await api('PUT',f'/carrinho/{c}/entrega',json={'unidade':'patio_guedala','modalidade':'retirada'})
        molho=(await client.put(f'{CAT}/itens/{ed}/opcoes',json={'grupos':[MOLHO]},headers=ADMIN)).json()['grupos'][0]
        r=await api('POST','/pedidos',json={'conversation_id':c});assert motivo(r)=='opcao_obrigatoria'
        linha=r.json()['erro']['detalhes']['linha_id'];assert r.json()['erro']['detalhes']['grupo']['nome']=='Escolha o molho'
        assert (await api('PATCH',f'/carrinho/{c}/itens/{linha}',json={'opcoes':[{'grupo_id':molho['id'],'opcao_id':molho['opcoes'][1]['id']}]})).status_code==200
        r=await api('POST','/pedidos',json={'conversation_id':c});assert r.status_code==201 and r.json()['pedido']['total']==40.0
    cenario_cat3(monkeypatch,corpo)


def test_texto_da_unidade_sem_opcoes_continua_igual():
    p={'numero':'AB12','cliente_nome':'Cliente','cliente_telefone':'5511900000001','subtotal':34,'taxa_entrega':None,'total':34,'modalidade':'retirada',
       'endereco':None,'horario':None,'observacao':None,'tem_alcoolico':False}
    antes=avisos.texto_pedido_unidade(p,[{'quantidade':1,'nome':'Edamame','preco_unitario':34,'subtotal':34,'observacao':None}],'Pátio')
    depois=avisos.texto_pedido_unidade(p,[{'quantidade':1,'nome':'Edamame','preco_unitario':34,'subtotal':34,'observacao':None,'opcoes':[],'preco_base':34}],'Pátio')
    assert antes==depois and '+' not in antes.split('─')[0].split('Telefone')[1]
