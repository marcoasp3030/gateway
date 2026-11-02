"""G4-b (ESPEC_GESTAO 11.4, 11.5 e 11.11): taxa por bairro ou CEP, frete grátis e área não atendida no carrinho, no pedido e na consulta."""
from decimal import Decimal
from modulos.entrega import bairro_no_endereco,calcular_taxa,cep_no_endereco
from modulos.pedidos import cardapio as CARD
from test_cat3a import cenario_cat3,T
from test_fase4_pedidos import motivo

PAGA={'forma_pagamento':'pix'}


def u(modo=None,fixa=None,frete=None,delivery=None):
    return {'taxa_modo':modo,'taxa_entrega':fixa,'frete_gratis_acima':frete,'delivery_ativo':delivery}


def bairro(id,nome,busca,taxa=None,atendido=True,apelidos=(),ativo=True):
    return {'id':id,'tipo':'bairro','bairro':nome,'bairro_busca':busca,'apelidos_busca':list(apelidos),'atendido':atendido,'taxa':taxa,'ativo':ativo}


def faixa(id,ini,fim,taxa):
    return {'id':id,'tipo':'cep','cep_inicio':ini,'cep_fim':fim,'taxa':taxa,'ativo':True}


B=[bairro(1,'Vila Mariana','vila mariana',Decimal('8'),apelidos=('vl mariana',)),bairro(2,'Vila Nova Mariana','vila nova mariana',Decimal('12')),
   bairro(3,'Paraisópolis','paraisopolis',atendido=False)]
F=[faixa(9,'05650000','05659999',Decimal('15'))]


def resumo(e): return e['atende'],e['motivo'],e['taxa'],e['origem'],e['pedir']


def test_sem_modo_e_igual_a_antes():
    assert resumo(calcular_taxa(u(),[]))==(True,None,None,'a_confirmar',None)
    assert resumo(calcular_taxa(u(fixa=Decimal('7')),[]))==(True,None,Decimal('7'),'fixa',None)
    assert resumo(calcular_taxa(u('bairro',Decimal('7')),[]))==(True,None,Decimal('7'),'fixa',None)   # modo sem linhas
    assert calcular_taxa(u(delivery=False),[])['motivo']=='delivery_indisponivel'


def test_bairro_por_nome_apelido_acento_e_inativo():
    assert resumo(calcular_taxa(u('bairro'),B,bairro='VILA  mariana'))==(True,None,Decimal('8'),'bairro',None)
    assert resumo(calcular_taxa(u('bairro'),B,bairro='Vl. Mariana'))[2]==Decimal('8')
    assert resumo(calcular_taxa(u('bairro'),B,bairro='paraisopolis'))==(False,'fora_area',None,None,None)
    inativo=[bairro(1,'Vila Mariana','vila mariana',Decimal('8'),ativo=False),*B[1:]]
    assert calcular_taxa(u('bairro'),inativo,bairro='Vila Mariana')['motivo']=='bairro_nao_reconhecido'


def test_bairro_desconhecido_pede_o_que_a_unidade_tem():
    assert resumo(calcular_taxa(u('bairro'),B,bairro='Tatuapé'))==(False,'bairro_nao_reconhecido',None,None,'confirmar_bairro')
    assert resumo(calcular_taxa(u('bairro'),B,bairro='Tatuapé',bairro_confirmado=True))==(True,None,None,'a_confirmar',None)
    assert resumo(calcular_taxa(u('bairro'),B+F,bairro='Tatuapé'))==(False,'bairro_nao_reconhecido',None,None,'cep')
    assert resumo(calcular_taxa(u('bairro'),B+F,bairro='Tatuapé',cep='05650-000'))==(True,None,Decimal('15'),'cep',None)
    assert resumo(calcular_taxa(u('bairro'),B+F,bairro='Tatuapé',cep='01000000'))[1]=='fora_area'


def test_cep_nos_limites_da_faixa():
    m=u('cep')
    assert calcular_taxa(m,F,cep='05650000')['taxa']==Decimal('15') and calcular_taxa(m,F,cep='05659999')['taxa']==Decimal('15')
    assert calcular_taxa(m,F,cep='05649999')['motivo']=='fora_area' and calcular_taxa(m,F,cep='05660000')['motivo']=='fora_area'
    assert resumo(calcular_taxa(m,F))==(False,'falta_cep',None,None,'cep')


def test_endereco_em_texto_decide_sem_campos_separados():
    assert calcular_taxa(u('bairro'),B,endereco='Rua Um, 10, Vila Nova Mariana, São Paulo')['taxa']==Decimal('12')   # a citação mais longa vence
    assert calcular_taxa(u('bairro'),B,endereco='Rua Dois, 5 - Paraisópolis')['motivo']=='fora_area'
    assert calcular_taxa(u('cep'),F,endereco='Rua Três, 8, CEP 05651-010')['taxa']==Decimal('15')
    assert bairro_no_endereco('Rua Vila Mariana, 10, Paraisópolis',B) is None   # dois bairros: pergunta
    assert bairro_no_endereco('Rua Marianaópolis',B) is None and cep_no_endereco('tel 11999998888') is None


def test_frete_gratis_no_valor_exato_e_um_centavo_abaixo():
    m=u('bairro',frete=Decimal('50'))
    assert resumo(calcular_taxa(m,B,bairro='Vila Mariana',subtotal=Decimal('50')))==(True,None,0,'frete_gratis',None)
    assert resumo(calcular_taxa(m,B,bairro='Vila Mariana',subtotal=Decimal('49.99')))[2:4]==(Decimal('8'),'bairro')
    assert calcular_taxa(u(frete=Decimal('50')),[],subtotal=Decimal('90'))['origem']=='a_confirmar'   # sem valor a zerar


def test_carrinho_pedido_e_consulta(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        uid=await conn.fetchval("SELECT id FROM tenant_unidades WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        U=f'/admin/v1/tenants/{T}/unidades/{uid}'
        c=await conversa('cliente1');base=f'/carrinho/{c}'
        await api('POST',base+'/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})   # subtotal 68
        # Sem configuração: como sempre foi, com qualquer endereço.
        r=await api('PUT',base+'/entrega',json={'unidade':'patio_guedala','modalidade':'delivery','endereco':'Rua A, 1, Bairro Qualquer',**PAGA});assert r.status_code==200,r.text
        k=r.json()['carrinho'];assert (k['taxa_entrega'],k['taxa_origem'],k['total'],k['pode_fechar'])==(None,'a_confirmar',68.0,True)
        # Configura por bairro.
        assert (await client.put(U+'/taxas',json={'itens':[{'tipo':'bairro','bairro':'Vila Mariana','taxa':8},{'tipo':'bairro','bairro':'Paraisópolis','atendido':False}]})).status_code==200
        assert (await client.patch(U,json={'taxa_modo':'bairro','frete_gratis_acima':100})).status_code==200
        # Já no carrinho, o endereço antigo deixa de fechar.
        k=(await api('GET',base)).json()['carrinho'];assert 'bairro' in k['faltando'] and not k['pode_fechar']
        r=await api('POST','/pedidos',json={'conversation_id':c});assert (r.status_code,motivo(r))==(422,'bairro_nao_reconhecido') and r.json()['erro']['detalhes']['pedir']=='confirmar_bairro'
        # Fora da área: recusa com o que oferecer, e nada é gravado.
        r=await api('PUT',base+'/entrega',json={'endereco':'Rua B, 2, Paraisópolis'});assert (r.status_code,motivo(r))==(422,'fora_area')
        d=r.json()['erro']['detalhes'];assert d['campo']=='endereco' and d['aceita_retirada'] is True and d['modo']=='bairro'
        assert (await api('GET',base)).json()['carrinho']['endereco']=='Rua A, 1, Bairro Qualquer'
        # Dentro da área, só com o endereço em texto: o agente de hoje não precisa mudar.
        r=await api('PUT',base+'/entrega',json={'endereco':'Rua C, 3, Vila Mariana'});assert r.status_code==200,r.text
        k=r.json()['carrinho'];assert (k['taxa_entrega'],k['taxa_origem'],k['total'],k['faltando'])==(8.0,'bairro',76.0,[])
        # Frete grátis: atingiu o valor, a taxa zera e o total acompanha.
        await api('POST',base+'/itens',json={'item_id':ids['GUE-0001'],'quantidade':1})
        k=(await api('GET',base)).json()['carrinho'];assert (k['subtotal'],k['taxa_entrega'],k['taxa_origem'],k['frete_gratis'],k['total'])==(102.0,0.0,'frete_gratis',True,102.0)
        r=await api('POST','/pedidos',json={'conversation_id':c});assert r.status_code==201,r.text
        p=r.json()['pedido'];assert (p['bairro'],p['taxa_entrega'],p['taxa_origem'],p['frete_gratis'],p['total'])==('Vila Mariana',0.0,'frete_gratis',True,102.0)
        # Bairro mantido pelo cliente: segue com taxa a confirmar, que não soma.
        c2=await conversa('cliente2');b2=f'/carrinho/{c2}'
        await api('POST',b2+'/itens',json={'item_id':ids['GUE-0001'],'quantidade':1})
        r=await api('PUT',b2+'/entrega',json={'unidade':'patio_guedala','modalidade':'delivery','endereco':'Rua D, 4','bairro':'Tatuapé',**PAGA});assert motivo(r)=='bairro_nao_reconhecido'
        r=await api('PUT',b2+'/entrega',json={'unidade':'patio_guedala','modalidade':'delivery','endereco':'Rua D, 4','bairro':'Tatuapé','bairro_confirmado':True,**PAGA});assert r.status_code==200,r.text
        p=(await api('POST','/pedidos',json={'conversation_id':c2})).json()['pedido'];assert (p['bairro'],p['taxa_entrega'],p['taxa_origem'],p['total'])==('Tatuapé',None,'a_confirmar',34.0)
        # Retirada não tem área.
        c3=await conversa('cliente3');b3=f'/carrinho/{c3}'
        await api('POST',b3+'/itens',json={'item_id':ids['GUE-0001'],'quantidade':1})
        r=await api('PUT',b3+'/entrega',json={'unidade':'patio_guedala','modalidade':'retirada',**PAGA});k=r.json()['carrinho'];assert (k['taxa_entrega'],k['taxa_origem'],k['faltando'])==(None,None,[])
        # Consulta: não grava nada.
        q=lambda **kw: api('GET','/entrega',params={'conversation_id':c3,'unidade':'patio_guedala',**kw})
        j=(await q(bairro='Vila Mariana')).json();assert (j['atende'],j['taxa'],j['origem_taxa'],j['frete_gratis_acima'])==(True,8.0,'bairro',100.0)
        j=(await q(bairro='Paraisópolis')).json();assert (j['atende'],j['motivo'])==(False,'fora_area')
        j=(await q(bairro='Tatuapé')).json();assert (j['motivo'],j['pedir'])==('bairro_nao_reconhecido','confirmar_bairro')
        assert (await q()).json()['erro']['detalhes']=={'campo':'bairro','motivo':'obrigatorio'}
        assert (await q(cep='123')).json()['erro']['detalhes']['campo']=='cep'
        # A taxa de uma unidade não vale para outra.
        assert (await api('GET','/entrega',params={'conversation_id':c3,'unidade':'morumbi'})).json()['origem_taxa']=='a_confirmar'
    cenario_cat3(monkeypatch,corpo)


def test_confirmacao_vale_so_para_o_bairro_confirmado(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        uid=await conn.fetchval("SELECT id FROM tenant_unidades WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        U=f'/admin/v1/tenants/{T}/unidades/{uid}'
        assert (await client.put(U+'/taxas',json={'itens':[{'tipo':'bairro','bairro':'Vila Mariana','taxa':8}]})).status_code==200
        assert (await client.patch(U,json={'taxa_modo':'bairro'})).status_code==200
        c=await conversa('cliente1');base=f'/carrinho/{c}'
        await api('POST',base+'/itens',json={'item_id':ids['GUE-0001'],'quantidade':1})
        r=await api('PUT',base+'/entrega',json={'unidade':'patio_guedala','modalidade':'delivery','endereco':'Rua D, 4','bairro':'Tatuapé','bairro_confirmado':True,**PAGA});assert r.status_code==200,r.text
        # Mesmo bairro, com outra grafia e sem repetir a confirmação: continua confirmado.
        r=await api('PUT',base+'/entrega',json={'endereco':'Rua D, 40','bairro':'tatuape'});assert r.status_code==200,r.text
        # Outro bairro: o cliente não confirmou este, e a recusa não muda o carrinho.
        r=await api('PUT',base+'/entrega',json={'bairro':'Mooca'});assert (r.status_code,motivo(r))==(422,'bairro_nao_reconhecido')
        k=await conn.fetchrow('SELECT bairro,bairro_confirmado FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2',T,c)
        assert (k['bairro'],k['bairro_confirmado'])==('tatuape',True)
        # Bairro da lista e depois um desconhecido: a confirmação antiga não volta a valer.
        r=await api('PUT',base+'/entrega',json={'bairro':'Vila Mariana'});assert r.status_code==200,r.text
        r=await api('PUT',base+'/entrega',json={'bairro':'Tatuapé'});assert (r.status_code,motivo(r))==(422,'bairro_nao_reconhecido')
    cenario_cat3(monkeypatch,corpo)


def test_cardapio_publico_cota_a_taxa_e_o_pedido_confere(monkeypatch):
    monkeypatch.setenv('PAINEL_PUBLIC_URL','https://painel.exemplo');monkeypatch.setenv('API_KEY_PEPPER','p'*32)
    async def corpo(conn,client,api,ids,conversa,relogio):
        uid=await conn.fetchval("SELECT id FROM tenant_unidades WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        U=f'/admin/v1/tenants/{T}/unidades/{uid}';P='/api/v1/publico/cardapio'
        assert (await client.put(U+'/taxas',json={'itens':[{'tipo':'bairro','bairro':'Vila Mariana','taxa':8},{'tipo':'bairro','bairro':'Paraisópolis','atendido':False}]})).status_code==200
        assert (await client.patch(U,json={'taxa_modo':'bairro','frete_gratis_acima':100})).status_code==200
        c=await conversa('cliente1');url,_=await CARD.garantir_link(conn,T,c);tok=url.split('#')[-1]
        u=next(x for x in (await client.post(P+'/ler',json={'token':tok})).json()['unidades'] if x['codigo']=='patio_guedala')
        assert (u['taxa_por_area'],u['frete_gratis_acima'])==(True,100.0)
        cota=lambda **kw: client.post(P+'/taxa',json={'token':tok,'unidade':'patio_guedala',**kw})
        assert (await cota(bairro='vila mariana')).json()=={'atende':True,'motivo':None,'taxa':8.0,'origem':'bairro','pedir':None}
        assert (await cota(bairro='Paraisópolis')).json()['motivo']=='fora_area'
        assert (await cota(bairro='Tatuapé')).json()['pedir']=='confirmar_bairro'
        assert (await cota(bairro='Tatuapé',bairro_confirmado=True)).json()['origem']=='a_confirmar'
        corpo_={'token':tok,'unidade':'patio_guedala','itens':[{'item_id':ids['GUE-0001'],'quantidade':1}],'modalidade':'delivery','forma_pagamento':'pix'}
        # Página adulterada: bairro fora da área não fecha, e nada fica gravado.
        r=await client.post(P+'/pedido',json={**corpo_,'endereco':'Rua A, 1','bairro':'Paraisópolis','total_visto':34});assert (r.status_code,motivo(r))==(422,'fora_area')
        assert await conn.fetchval('SELECT count(*) FROM pedidos WHERE tenant_id=$1',T)==0
        # Total sem a taxa: o servidor confere e mostra o certo.
        r=await client.post(P+'/pedido',json={**corpo_,'endereco':'Rua A, 1','bairro':'Vila Mariana','total_visto':34});assert (r.status_code,r.json()['erro']['detalhes']['total'])==(409,42.0)
        r=await client.post(P+'/pedido',json={**corpo_,'endereco':'Rua A, 1','bairro':'Vila Mariana','cep':'04101000','total_visto':42});assert r.status_code==201,r.text
        p=await conn.fetchrow('SELECT bairro,cep,taxa_entrega,taxa_origem,total FROM pedidos WHERE tenant_id=$1',T)
        assert (p['bairro'],p['cep'],float(p['taxa_entrega']),p['taxa_origem'],float(p['total']))==('Vila Mariana','04101000',8.0,'bairro',42.0)
    cenario_cat3(monkeypatch,corpo)
