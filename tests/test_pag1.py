"""PAG1 (docs/ESPEC_PAG1.md): forma de pagamento da lista da unidade e troco em definir_entrega, recusa no fechamento sem pagamento,
pagamento no pedido, no painel, no aviso NOVO PEDIDO e no evento; entrega recusada com delivery_ativo=false."""
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar,motivo

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
FORMAS=[{'codigo':'pix','rotulo':'Pix','aceita_troco':False},{'codigo':'dinheiro','rotulo':'Dinheiro','aceita_troco':True}]


def test_pagamento_no_carrinho_no_fechamento_no_pedido_e_no_aviso():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,outra=await montar(conn,client,app)
            c=await conversa('cliente-pag1');base=f'/carrinho/{c}'
            entrega=lambda **d: api('PUT',base+'/entrega',json=d,pagamento=False)
            fechar=lambda: api('POST','/pedidos',json={'conversation_id':c})
            await api('POST',base+'/itens',json={'item_id':ids['GUE-0002']})          # 150,00
            k=(await entrega(modalidade='retirada')).json()['carrinho']
            assert k['faltando']==['pagamento'] and k['formas_aceitas']==FORMAS and k['forma_pagamento'] is None
            # Sem forma: fechar recusa, com as formas aceitas.
            r=await fechar();assert (r.status_code,motivo(r))==(422,'pagamento_nao_informado')
            assert r.json()['erro']['detalhes']['formas_aceitas']==FORMAS
            # Forma fora da lista (ou inventada): recusa e nada muda.
            for forma in ('cartao_entrega','boleto'):
                r=await entrega(forma_pagamento=forma);assert (r.status_code,motivo(r))==(422,'forma_pagamento_invalida'),forma
                assert r.json()['erro']['detalhes']['formas_aceitas']==FORMAS and r.json()['carrinho']['forma_pagamento'] is None
            assert (await entrega(forma_pagamento='Pix')).status_code==400          # código, não rótulo
            # Troco: só em dinheiro e acima do total.
            r=await entrega(forma_pagamento='pix',troco_para=200);assert (r.status_code,motivo(r))==(422,'troco_so_em_dinheiro')
            r=await entrega(forma_pagamento='dinheiro',troco_para=150);assert (r.status_code,motivo(r))==(422,'troco_menor_que_total')
            assert r.json()['erro']['detalhes']['total']==150.0 and r.json()['carrinho']['forma_pagamento'] is None   # recusa desfaz a chamada toda
            k=(await entrega(forma_pagamento='dinheiro',troco_para=200)).json()['carrinho']
            assert (k['forma_pagamento'],k['pagamento_rotulo'],k['troco_para'],k['troco'],k['pode_fechar'])==('dinheiro','Dinheiro',200.0,50.0,True)
            # O total sobe depois: o troco volta a faltar; no fechamento, recusa.
            await api('POST',base+'/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})   # +68,00 → 218,00
            k=(await api('GET',base)).json()['carrinho'];assert k['faltando']==['troco'] and k['troco'] is None
            r=await fechar();assert (r.status_code,motivo(r))==(422,'troco_menor_que_total') and r.json()['erro']['detalhes']['total']==218.0
            # Trocar de forma tira o troco; troco null = não precisa.
            k=(await entrega(forma_pagamento='pix')).json()['carrinho'];assert (k['forma_pagamento'],k['troco_para'],k['faltando'])==('pix',None,[])
            k=(await entrega(forma_pagamento='dinheiro',troco_para=250)).json()['carrinho'];assert k['troco']==32.0
            # Forma desativada pela unidade depois de escolhida: volta a faltar e o fechamento recusa.
            uid=(await client.get('/admin/v1/tenants/bykoji/unidades')).json()['itens'][0]['id']
            P=f'/admin/v1/tenants/bykoji/unidades/{uid}/pagamentos'
            assert (await client.put(P,json={'itens':[{'codigo':'pix'},{'codigo':'dinheiro','aceita_troco':True,'ativo':False}]})).status_code==200
            assert (await api('GET',base)).json()['carrinho']['faltando']==['pagamento']
            r=await fechar();assert (r.status_code,motivo(r))==(422,'forma_pagamento_invalida')
            assert (await client.put(P,json={'itens':FORMAS})).status_code==200
            # Fecha: o pedido guarda forma, rótulo e troco; o carrinho novo começa sem pagamento.
            await conn.execute("UPDATE tenant_unidades SET whatsapp_pedidos='5511900000091' WHERE tenant_id='bykoji' AND codigo='patio_guedala'")
            r=await fechar();assert r.status_code==201,r.text
            numero=r.json()['numero'];pag=r.json()['pedido']['pagamento']
            assert pag=={'forma':'dinheiro','rotulo':'Dinheiro','troco_para':250.0,'troco':32.0}
            assert (r.json()['carrinho']['forma_pagamento'],r.json()['carrinho']['faltando'])==(None,['itens','modalidade','pagamento'])
            linha=await conn.fetchrow("SELECT forma_pagamento,pagamento_rotulo,troco_para FROM pedidos WHERE tenant_id='bykoji' AND numero=$1",numero)
            assert (linha['forma_pagamento'],linha['pagamento_rotulo'],float(linha['troco_para']))==('dinheiro','Dinheiro',250.0)
            # Aviso NOVO PEDIDO para a unidade.
            texto=await conn.fetchval("SELECT texto FROM avisos_internos WHERE tenant_id='bykoji' AND tipo='pedido_unidade' AND referencia=$1",numero)
            assert 'Pagamento: Dinheiro — troco para R$ 250,00 (levar R$ 32,00 de troco)' in texto
            # Painel: lista e detalhe.
            d=(await client.get(f'/admin/v1/tenants/bykoji/pedidos/{numero}')).json();assert d['pagamento']==pag
            lista=(await client.get('/admin/v1/tenants/bykoji/pedidos')).json()['itens'];assert next(p for p in lista if p['numero']==numero)['pagamento']==pag
            # Esvaziar e trocar de unidade tiram o pagamento.
            await api('POST',base+'/itens',json={'item_id':ids['GUE-0001']})
            await entrega(modalidade='retirada',forma_pagamento='pix')
            assert (await api('DELETE',base)).json()['carrinho']['forma_pagamento'] is None
    banco(cenario)


def test_pix_sem_troco_aviso_simples_unidade_sem_formas_e_delivery_desligado():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,outra=await montar(conn,client,app)
            c=await conversa('cliente-pag1b');base=f'/carrinho/{c}'
            entrega=lambda **d: api('PUT',base+'/entrega',json=d,pagamento=False)
            uid=(await client.get('/admin/v1/tenants/bykoji/unidades')).json()['itens'][0]['id']
            await api('POST',base+'/itens',json={'item_id':ids['GUE-0001']})
            # delivery_ativo=false: entrega recusada (ao definir e ao fechar); null segue.
            assert (await client.patch(f'/admin/v1/tenants/bykoji/unidades/{uid}',json={'delivery_ativo':False})).status_code==200
            r=await entrega(modalidade='delivery',endereco='Rua A, 10');assert (r.status_code,motivo(r))==(422,'delivery_indisponivel')
            assert r.json()['carrinho']['modalidade'] is None
            assert (await client.patch(f'/admin/v1/tenants/bykoji/unidades/{uid}',json={'delivery_ativo':True,'taxa_entrega':12})).status_code==200
            k=(await entrega(modalidade='delivery',endereco='Rua A, 10',forma_pagamento='pix')).json()['carrinho'];assert k['pode_fechar'] and k['total']==46.0
            await conn.execute("UPDATE tenant_unidades SET delivery_ativo=false WHERE tenant_id='bykoji' AND id=$1",uid)
            r=await api('POST','/pedidos',json={'conversation_id':c});assert (r.status_code,motivo(r))==(422,'delivery_indisponivel')
            await conn.execute("UPDATE tenant_unidades SET delivery_ativo=NULL,whatsapp_pedidos='5511900000091' WHERE tenant_id='bykoji' AND id=$1",uid)
            # Troco comparado com o total com a taxa (34 + 12 = 46).
            r=await entrega(forma_pagamento='dinheiro',troco_para=46);assert (r.status_code,motivo(r))==(422,'troco_menor_que_total')
            r=await api('POST','/pedidos',json={'conversation_id':c});assert r.status_code==201,r.text
            numero=r.json()['numero'];assert r.json()['pedido']['pagamento']=={'forma':'pix','rotulo':'Pix','troco_para':None,'troco':None}
            texto=await conn.fetchval("SELECT texto FROM avisos_internos WHERE tenant_id='bykoji' AND tipo='pedido_unidade' AND referencia=$1",numero)
            assert '\nPagamento: Pix\n' in texto and 'troco' not in texto
            # Unidade sem formas cadastradas: nenhuma forma passa (o agente transfere).
            assert (await client.put(f'/admin/v1/tenants/bykoji/unidades/{uid}/pagamentos',json={'itens':[]})).status_code==200
            await api('POST',base+'/itens',json={'item_id':ids['GUE-0001']})
            assert (await entrega(modalidade='retirada')).status_code==200
            r=await entrega(forma_pagamento='pix')
            assert (r.status_code,motivo(r),r.json()['erro']['detalhes']['formas_aceitas'])==(422,'forma_pagamento_invalida',[])
            r=await api('POST','/pedidos',json={'conversation_id':c});assert (r.status_code,motivo(r))==(422,'pagamento_nao_informado')
            # Pedido de antes do PAG1 (sem forma): pagamento null no painel.
            await conn.execute("UPDATE pedidos SET forma_pagamento=NULL,pagamento_rotulo=NULL WHERE tenant_id='bykoji' AND numero=$1",numero)
            assert (await client.get(f'/admin/v1/tenants/bykoji/pedidos/{numero}')).json()['pagamento'] is None
    banco(cenario)
