"""Fase 4, bloco 2: carrinho e pré-pedido (regras 1–3, SEG06 e os testes do plano)."""
import json
import pytest
from agentes import skyone
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila,ativo
from test_fase4_catalogo import CAB

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')

CSV=CAB+'''
GUE-0001,patio_guedala,Entradas quentes,Edamame,,34,item,nao,a_confirmar,sim,
GUE-0002,patio_guedala,Pratos quentes,Ebi no Tempurá,Tempurá de camarão,150,item,nao,a_confirmar,sim,
GUE-0003,patio_guedala,Temakis,Toro,,,item,nao,a_confirmar,sim,preço sob consulta
GUE-0004,patio_guedala,Cervejas,Sapporo,,55,item,sim,a_confirmar,sim,
GUE-0005,patio_guedala,Carpaccios,Adicional de azeite trufado,,12,adicional,nao,a_confirmar,sim,
GUE-0006,patio_guedala,Almoço executivo — opções,Sunomono,,,opcao_executivo,nao,a_confirmar,nao,opção do almoço executivo
GUE-0007,patio_guedala,Sobremesas,Purin,,32,item,nao,nao,sim,só no salão
MOR-0001,morumbi,Sashimi,Shake,Salmão,30,item,nao,a_confirmar,sim,
'''


async def montar(conn,client,app):
    """bykoji com catálogo, unidade, canal API, conversas e chave das Skills."""
    await conn.execute("INSERT INTO modulos_tenant(tenant_id,modulo,ativo) VALUES ('bykoji','catalogo',true),('bykoji','pedidos',true) ON CONFLICT (tenant_id,modulo) DO UPDATE SET ativo=true")
    can=(await client.post('/admin/v1/tenants/bykoji/canais',json={'nome':'API','tipo':'api','agente_id':'bykoji_atendimento'})).json()['id']
    await ativo(client,'bykoji','bykoji_atendimento',can)
    async def conversa(usuario):
        return (await client.post('/admin/v1/tenants/bykoji/agentes/bykoji_atendimento/simular',json={'canal_id':can,'identificador':usuario,'texto':'oi','message_id':'m-'+usuario})).json()['conversation_id']
    assert (await client.post('/admin/v1/tenants/bykoji/catalogo/importar',params={'simulacao':'false'},content=CSV.encode())).json()['aplicado']
    assert (await client.post('/admin/v1/tenants/bykoji/unidades',json={'nome':'Pátio Guedala','codigo':'patio_guedala'})).status_code==201
    def segredo(r): return next(v for k,v in r.json().items() if isinstance(v,str) and k in ('chave','api_key','valor','segredo','key'))
    k=segredo(await client.post('/admin/v1/tenants/bykoji/api-keys',json={'nome':'skills','escopos':['ferramentas:catalogo','ferramentas:pedidos']}))
    outra=segredo(await client.post('/admin/v1/tenants/bykoji/api-keys',json={'nome':'so-catalogo','escopos':['ferramentas:catalogo']}))
    ids={r['codigo']:r['id'] for r in await conn.fetch("SELECT codigo,id FROM catalogo_itens WHERE tenant_id='bykoji'")}
    async def api(metodo,caminho,chave=k,**kw):
        return await client.request(metodo,'/api/v1'+caminho,headers={'X-API-Key':chave,'Authorization':''},**kw)
    return conversa,ids,api,outra


def motivo(r): return r.json()['erro']['detalhes']['motivo']


def test_carrinho_regras_de_item_unidade_e_total():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,outra=await montar(conn,client,app)
            c=await conversa('cliente1');base=f'/carrinho/{c}'
            # Carrinho vazio: nada criado, diz o que falta.
            r=(await api('GET',base)).json()['carrinho']
            assert (r['itens'],r['subtotal'],r['total'],r['pode_fechar'],r['faltando'])==([],None,None,False,['itens','modalidade'])
            assert await conn.fetchval('SELECT count(*) FROM carrinhos')==0
            # Adiciona: toda escrita devolve o carrinho completo; o preço vem do catálogo.
            r=await api('POST',base+'/itens',json={'item_id':ids['GUE-0001'],'quantidade':2});assert r.status_code==200,r.text
            k=r.json()['carrinho'];assert (k['unidade'],k['subtotal'],k['total'],k['numero_itens'])==('patio_guedala',68.0,68.0,2)
            assert k['itens'][0]['preco_unitario']==34.0 and k['itens'][0]['subtotal']==68.0 and k['itens'][0]['codigo']=='GUE-0001'
            # O agente não consegue informar preço: campo fora do contrato é recusado.
            assert (await api('POST',base+'/itens',json={'item_id':ids['GUE-0002'],'preco_unitario':1})).status_code==400
            k=(await api('POST',base+'/itens',json={'item_id':ids['GUE-0002']})).json()['carrinho']
            assert k['subtotal']==218.0 and [i['subtotal'] for i in k['itens']]==[68.0,150.0]   # total = soma dos itens
            # Mesmo item e mesma observação somam; observação diferente vira outra linha.
            k=(await api('POST',base+'/itens',json={'item_id':ids['GUE-0001']})).json()['carrinho']
            assert [(i['codigo'],i['quantidade']) for i in k['itens']]==[('GUE-0001',3),('GUE-0002',1)]
            k=(await api('POST',base+'/itens',json={'item_id':ids['GUE-0001'],'observacao':'sem sal'})).json()['carrinho']
            assert [(i['codigo'],i['quantidade'],i['observacao']) for i in k['itens']][-1]==('GUE-0001',1,'sem sal') and k['subtotal']==286.0
            # Regra 1: sem preço, inativo/opção do executivo e delivery=nao não entram; inexistente → 404.
            for codigo,esperado,status in (('GUE-0003','sem_preco',422),('GUE-0006','item_inativo',422),('GUE-0007','sem_delivery',422)):
                r=await api('POST',base+'/itens',json={'item_id':ids[codigo]})
                assert (r.status_code,motivo(r))==(status,esperado) and r.json()['carrinho']['subtotal']==286.0,codigo
            assert 'pedido_para_confirmar' in (await api('POST',base+'/itens',json={'item_id':ids['GUE-0003']})).json()['erro']['mensagem']
            r=await api('POST',base+'/itens',json={'item_id':999999});assert (r.status_code,motivo(r))==(404,'item_inexistente')
            # Regra 2: item de outra unidade é recusado.
            r=await api('POST',base+'/itens',json={'item_id':ids['MOR-0001']});assert (r.status_code,motivo(r))==(422,'unidade_diferente')
            # Altera quantidade e observação; quantidade 0 remove; remover linha.
            l1,l2,l3=[i['id'] for i in k['itens']]
            k=(await api('PATCH',f'{base}/itens/{l1}',json={'quantidade':1})).json()['carrinho'];assert k['itens'][0]['quantidade']==1 and k['subtotal']==218.0
            k=(await api('PATCH',f'{base}/itens/{l2}',json={'observacao':'bem passado'})).json()['carrinho'];assert k['itens'][1]['observacao']=='bem passado' and k['itens'][1]['quantidade']==1
            k=(await api('PATCH',f'{base}/itens/{l3}',json={'quantidade':0})).json()['carrinho'];assert len(k['itens'])==2
            k=(await api('DELETE',f'{base}/itens/{l2}')).json()['carrinho'];assert [i['codigo'] for i in k['itens']]==['GUE-0001'] and k['subtotal']==34.0
            r=await api('DELETE',f'{base}/itens/{l2}');assert (r.status_code,motivo(r))==(404,'linha_inexistente')
            assert (await api('PATCH',f'{base}/itens/{l1}',json={'quantidade':100})).status_code==400
            # Esvaziar solta a unidade: dá para começar de novo em outra.
            k=(await api('DELETE',base)).json()['carrinho'];assert (k['itens'],k['unidade'],k['subtotal'])==([],None,None)
            assert (await api('POST',base+'/itens',json={'item_id':ids['MOR-0001']})).json()['carrinho']['unidade']=='morumbi'
            # Conversa inexistente → 404; chave sem o escopo → 403; sem chave → 401. Cabeçalho de empresa não muda nada.
            assert (await api('GET','/carrinho/bykoji:x:nao-existe')).status_code==404
            assert (await api('POST','/carrinho/bykoji:x:nao-existe/itens',json={'item_id':ids['GUE-0001']})).status_code==404
            assert (await api('GET',base,chave=outra)).status_code==403
            assert (await client.get('/api/v1'+base,headers={'Authorization':'','X-Tenant-ID':'bykoji'})).status_code==401
            # Carrinho de uma conversa não aparece em outra.
            c2=await conversa('cliente2');assert (await api('GET',f'/carrinho/{c2}')).json()['carrinho']['itens']==[]
    banco(cenario)


def test_fechar_pedido_exigencias_alcool_minimo_e_preco_do_banco():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,outra=await montar(conn,client,app)
            assert (await client.post('/admin/v1/tenants/bykoji/webhooks-saida',json={'url':'https://painel.exemplo.com/gw','eventos':['pedido.criado','pedido.cancelado'],'ativo':True})).status_code==201
            c=await conversa('cliente1');base=f'/carrinho/{c}'
            fechar=lambda conv=c: api('POST','/pedidos',json={'conversation_id':conv})
            r=await fechar();assert (r.status_code,motivo(r))==(422,'carrinho_vazio')
            await api('POST',base+'/itens',json={'item_id':ids['GUE-0002']})
            await api('POST',base+'/itens',json={'item_id':ids['GUE-0004'],'quantidade':2})      # cerveja
            r=await fechar();assert (r.status_code,motivo(r))==(422,'falta_modalidade') and r.json()['carrinho']['faltando']==['modalidade','maioridade_confirmada']
            # Delivery sem endereço → 422.
            k=(await api('PUT',base+'/entrega',json={'modalidade':'delivery'})).json()['carrinho'];assert k['faltando']==['endereco','maioridade_confirmada']
            r=await fechar();assert (r.status_code,motivo(r))==(422,'falta_endereco')
            k=(await api('PUT',base+'/entrega',json={'endereco':'Rua A, 10','horario':'20h','observacao':'interfone 12'})).json()['carrinho']
            assert (k['modalidade'],k['endereco'],k['horario'])==('delivery','Rua A, 10','20h')     # só muda o que veio
            # Regra 3: bebida alcoólica só fecha com maioridade confirmada.
            r=await fechar();assert (r.status_code,motivo(r))==(422,'falta_maioridade') and r.json()['carrinho']['tem_alcoolico']
            k=(await api('PUT',base+'/entrega',json={'maioridade_confirmada':True})).json()['carrinho'];assert k['pode_fechar'] and k['total']==260.0
            # Pedido mínimo e retirada da unidade.
            uid=(await client.get('/admin/v1/tenants/bykoji/unidades')).json()['itens'][0]['id']
            await client.patch(f'/admin/v1/tenants/bykoji/unidades/{uid}',json={'pedido_minimo':300,'aceita_retirada':False})
            r=await fechar();assert (r.status_code,motivo(r))==(422,'abaixo_do_minimo')
            r=await api('PUT',base+'/entrega',json={'modalidade':'retirada'});assert (r.status_code,motivo(r))==(422,'retirada_indisponivel')
            await client.patch(f'/admin/v1/tenants/bykoji/unidades/{uid}',json={'pedido_minimo':None,'taxa_entrega':15},headers={'X-Role':'admin'})
            assert (await api('GET',base)).json()['carrinho']['total']==275.0     # subtotal 260 + taxa 15
            # Preço sempre do banco: mudou no catálogo → carrinho atualizado volta para confirmar; depois fecha com o preço novo.
            await conn.execute("UPDATE catalogo_itens SET preco=160 WHERE tenant_id='bykoji' AND codigo='GUE-0002'")
            r=await fechar();assert (r.status_code,motivo(r))==(409,'preco_alterado') and r.json()['carrinho']['subtotal']==270.0
            assert await conn.fetchval('SELECT count(*) FROM pedidos')==0
            # Item que ficou inativo depois de entrar no carrinho impede o fechamento.
            await conn.execute("UPDATE catalogo_itens SET ativo=false WHERE tenant_id='bykoji' AND codigo='GUE-0004'")
            r=await fechar();assert (r.status_code,motivo(r))==(422,'item_inativo')
            await conn.execute("UPDATE catalogo_itens SET ativo=true WHERE tenant_id='bykoji' AND codigo='GUE-0004'")
            r=await fechar();assert r.status_code==201,r.text
            p=r.json()['pedido']
            assert p['status']=='aguardando_loja' and p['numero'].isdigit() and len(p['numero'])>=5
            assert (p['subtotal'],p['taxa_entrega'],p['total'],p['unidade'],p['modalidade'],p['endereco'],p['tem_alcoolico'])==(270.0,15.0,285.0,'patio_guedala','delivery','Rua A, 10',True)
            assert [(i['codigo'],i['quantidade'],i['preco_unitario'],i['subtotal']) for i in p['itens']]==[('GUE-0002',1,160.0,160.0),('GUE-0004',2,55.0,110.0)]
            assert sum(i['subtotal'] for i in p['itens'])==p['subtotal']
            # Carrinho fechado: o da conversa volta vazio; fechar de novo não cria outro pedido.
            assert r.json()['carrinho']['itens']==[] and (await fechar()).status_code==422
            linha=await conn.fetchrow('SELECT * FROM pedidos');assert linha['cliente_telefone']=='cliente1' and linha['maioridade_confirmada']
            assert await conn.fetchval("SELECT status FROM carrinhos WHERE id=$1",linha['carrinho_id'])=='fechado'
            # Webhook de saída e auditoria; o evento não leva telefone nem endereço.
            ev=await conn.fetchrow("SELECT evento,corpo FROM webhook_entregas WHERE tenant_id='bykoji' ORDER BY id DESC LIMIT 1")
            corpo=json.loads(ev['corpo']);assert ev['evento']=='pedido.criado' and corpo['dados']['numero']==p['numero'] and corpo['dados']['total']==285.0
            assert 'Rua A' not in ev['corpo'] and 'cliente1' not in ev['corpo'].replace(c,'')
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id='bykoji' AND acao='pedido.criado' AND alvo=$1",p['numero'])==1
            # Reimportar o catálogo não mexe em carrinho nem em pedido.
            assert (await client.post('/admin/v1/tenants/bykoji/catalogo/importar',params={'simulacao':'false'},content=CSV.encode())).json()['aplicado']
            assert await conn.fetchval('SELECT count(*) FROM pedidos')==1 and (await api('GET',f"/pedidos/{p['numero']}",params={'conversation_id':c})).json()['pedido']['total']==285.0
            # Lista e detalhe administrativos (com telefone, que é interno).
            lista=(await client.get('/admin/v1/tenants/bykoji/pedidos',params={'status':'aguardando_loja'})).json()['itens']
            assert [x['numero'] for x in lista]==[p['numero']] and lista[0]['cliente_telefone']=='cliente1'
            assert (await client.get(f"/admin/v1/tenants/bykoji/pedidos/{p['numero']}")).json()['total']==285.0
            assert (await client.get('/admin/v1/tenants/bykoji/pedidos',params={'status':'inventado'})).status_code==400
            # Retirada não leva endereço nem taxa.
            c3=await conversa('cliente3');await api('POST',f'/carrinho/{c3}/itens',json={'item_id':ids['GUE-0001']})
            await client.patch(f'/admin/v1/tenants/bykoji/unidades/{uid}',json={'aceita_retirada':True})
            await api('PUT',f'/carrinho/{c3}/entrega',json={'modalidade':'retirada','endereco':'não usar'})
            p3=(await fechar(c3)).json()['pedido'];assert (p3['endereco'],p3['taxa_entrega'],p3['total'])==(None,None,34.0)
    banco(cenario)


def test_SEG06_pedido_so_do_dono_e_cancelamento_so_antes_da_unidade_decidir():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,outra=await montar(conn,client,app)
            async def pedido(usuario):
                c=await conversa(usuario);await api('POST',f'/carrinho/{c}/itens',json={'item_id':ids['GUE-0001']})
                await api('PUT',f'/carrinho/{c}/entrega',json={'modalidade':'retirada'})
                return c,(await api('POST','/pedidos',json={'conversation_id':c})).json()['pedido']['numero']
            c1,n1=await pedido('dono');c2,n2=await pedido('outro');assert n1!=n2
            ver=lambda n,c: api('GET',f'/pedidos/{n}',params={'conversation_id':c})
            cancelar=lambda n,c,**d: api('POST',f'/pedidos/{n}/cancelar',json={'conversation_id':c,**d})
            assert (await ver(n1,c1)).json()['pedido']['status']=='aguardando_loja'
            # Outra conversa não vê nem cancela o pedido alheio.
            r=await ver(n1,c2);assert (r.status_code,motivo(r))==(404,'pedido_inexistente')
            r=await cancelar(n1,c2);assert r.status_code==404 and await conn.fetchval("SELECT status FROM pedidos WHERE numero=$1",n1)=='aguardando_loja'
            # O dono cancela enquanto a unidade não decidiu; repetir não muda nada nem duplica o registro.
            r=await cancelar(n1,c1,motivo='desisti');assert r.status_code==200 and r.json()['pedido']['status']=='cancelado' and r.json()['pedido']['motivo']=='desisti'
            assert (await cancelar(n1,c1)).json()['pedido']['status']=='cancelado'
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='pedido.cancelado' AND alvo=$1",n1)==1
            assert await conn.fetchval("SELECT decidido_por FROM pedidos WHERE numero=$1",n1)=='cliente'
            # Depois que a unidade confirmou (ou recusou), o agente não cancela mais, peça o que pedir.
            for decidido in ('confirmado','recusado'):
                await conn.execute("UPDATE pedidos SET status=$2 WHERE numero=$1",n2,decidido)
                r=await cancelar(n2,c2);assert (r.status_code,motivo(r))==(409,'pedido_nao_cancelavel')
                assert await conn.fetchval("SELECT status FROM pedidos WHERE numero=$1",n2)==decidido
            # Pedido fechado não é alterado pelo carrinho: itens novos abrem outro carrinho.
            k=(await api('POST',f'/carrinho/{c2}/itens',json={'item_id':ids['GUE-0002']})).json()['carrinho']
            assert [i['codigo'] for i in k['itens']]==['GUE-0002'] and (await ver(n2,c2)).json()['pedido']['total']==34.0
            assert await conn.fetchval("SELECT count(*) FROM carrinhos WHERE conversation_id=$1",c2)==2
    banco(cenario)


def test_pacote_ao_agente_leva_o_carrinho():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,outra=await montar(conn,client,app)
            c=await conversa('cliente1');repo=Repositorio(conn);cfg=await Configuracoes(repo).efetiva('bykoji','bykoji_atendimento')
            p=await skyone.pacote(repo,'bykoji',c,10**9,'oi',cfg)
            assert p['contexto_modulos']['pedidos']['carrinho']['itens']==[] and p['contexto_modulos']['pedidos']['ultimo_pedido'] is None
            await api('POST',f'/carrinho/{c}/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})
            await api('PUT',f'/carrinho/{c}/entrega',json={'modalidade':'retirada'})
            p=await skyone.pacote(repo,'bykoji',c,10**9,'oi',cfg);k=p['contexto_modulos']['pedidos']['carrinho']
            assert (k['subtotal'],k['unidade'],k['pode_fechar'],k['itens'][0]['nome'])==(68.0,'patio_guedala',True,'Edamame')
            n=(await api('POST','/pedidos',json={'conversation_id':c})).json()['pedido']['numero']
            p=await skyone.pacote(repo,'bykoji',c,10**9,'oi',cfg)
            assert p['contexto_modulos']['pedidos']['ultimo_pedido']['numero']==n and p['contexto_modulos']['pedidos']['ultimo_pedido']['status']=='aguardando_loja'
            json.dumps(p)   # serializável
    banco(cenario)
