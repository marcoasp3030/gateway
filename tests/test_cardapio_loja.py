"""Link da loja: o cliente monta o pedido sem conversa e finaliza pelo WhatsApp, com um código."""
from urllib.parse import unquote
import pytest
from modulos.pedidos import cardapio as CARD
from test_cardapio_interativo import P,T,em_banco,motivo_de,pedido
from test_migrations import DSN

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
NUMERO='5511900000500'
CLIENTE='5511900000501'


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setenv('PAINEL_PUBLIC_URL','https://painel.exemplo.test/')
    monkeypatch.setenv('API_KEY_PEPPER','pepper-de-teste')


async def loja(client):
    r=await client.post(f'/admin/v1/tenants/{T}/cardapio/loja');assert r.status_code==200,r.text
    return r.json()


async def conectar(conn):
    """Canal do cenário como WhatsApp conectado. Devolve o id do canal."""
    return await conn.fetchval("UPDATE tenant_canais SET tipo='uazapi',numero_instancia=$2 WHERE tenant_id=$1 RETURNING id",T,NUMERO+'@s.whatsapp.net')


def token_de(j): return j['url'].split('#')[1]


def recebe(client,canal,texto,identificador=CLIENTE,mid='m1'):
    return client._transport.app.state.pipeline.receber(T,canal,{'identificador':identificador,'texto':texto,'message_id':mid,'tipo':'texto'})


def test_link_da_loja_e_fixo_troca_e_nao_e_previa():
    async def cenario(conn,client,conversa,ids,api):
        a=await loja(client);b=await loja(client)
        assert a['url']==b['url'] and a['whatsapp_conectado'] is False
        lido=(await client.post(P+'/ler',json={'token':token_de(a)})).json()
        assert (lido['previa'],lido['loja'],lido['carrinho'])==(False,True,None)
        # Pedido direto é só de link com conversa.
        r=await client.post(P+'/pedido',json=pedido(token_de(a),ids));assert (r.status_code,motivo_de(r))==(403,'previa')
        novo=(await client.post(f'/admin/v1/tenants/{T}/cardapio/loja',json={'renovar':True})).json()
        assert novo['url']!=a['url']
        assert (await client.post(P+'/ler',json={'token':token_de(a)})).status_code==404
        assert await conn.fetchval('SELECT count(*) FROM cardapio_links WHERE tenant_id=$1 AND loja',T)==1
    em_banco(cenario)


def test_sem_whatsapp_conectado_a_pagina_sabe_e_nada_e_guardado():
    async def cenario(conn,client,conversa,ids,api):
        tok=token_de(await loja(client))
        r=await client.post(P+'/finalizar',json=pedido(tok,ids));assert (r.status_code,motivo_de(r))==(409,'whatsapp_indisponivel')
        assert await conn.fetchval('SELECT count(*) FROM cardapio_rascunhos')==0
    em_banco(cenario)


def test_finaliza_pela_mensagem_e_a_conversa_nasce_do_whatsapp():
    async def cenario(conn,client,conversa,ids,api):
        canal=await conectar(conn);tok=token_de(await loja(client))
        r=await client.post(P+'/finalizar',json=pedido(tok,ids));assert r.status_code==201,r.text
        j=r.json();c=j['codigo'];assert j['whatsapp_url'].startswith(f'https://wa.me/{NUMERO}?text=')
        texto=unquote(j['whatsapp_url'].split('text=')[1]);assert texto.endswith(f'#{c}')
        # O ensaio não deixa rastro: nem pedido, nem conversa, nem carrinho.
        assert await conn.fetchval("SELECT count(*) FROM conversas WHERE conversation_id LIKE '%ensaio%'")==0
        assert await conn.fetchval('SELECT count(*) FROM pedidos')==0
        assert await conn.fetchval('SELECT count(*) FROM carrinhos')==0
        situacao=lambda: client.post(P+'/situacao',json={'token':tok,'codigo':c})
        assert (await situacao()).json()=={'estado':'aguardando'}
        s=await recebe(client,canal,texto);assert s['finalizacao']=='pedido',s
        p=await conn.fetchrow('SELECT numero,conversation_id,total,cliente_telefone FROM pedidos WHERE tenant_id=$1',T)
        assert CLIENTE in p['conversation_id'] and float(p['total'])==68.0 and p['numero']==s['pedido']
        # A conversa aparece no painel, com a mensagem, e o agente não é chamado para responder a ela.
        assert await conn.fetchval('SELECT count(*) FROM conversas WHERE conversation_id=$1',p['conversation_id'])==1
        assert await conn.fetchval("SELECT processada_em IS NOT NULL FROM mensagens WHERE conversation_id=$1 AND direcao='entrada'",p['conversation_id'])
        assert await conn.fetchval("SELECT count(*) FROM avisos_internos WHERE tenant_id=$1 AND tipo='pedido_cliente'",T)==1
        assert (await situacao()).json()=={'estado':'recebido','numero':p['numero'],'total':68.0}
        # O endereço e os itens não ficam numa segunda cópia.
        assert await conn.fetchval('SELECT dados::text FROM cardapio_rascunhos WHERE codigo=$1',c)=='{}'
        # Reenvio com outro id e webhook repetido: um pedido só.
        await recebe(client,canal,texto,mid='m2');await recebe(client,canal,texto)
        assert await conn.fetchval('SELECT count(*) FROM pedidos')==1
    em_banco(cenario)


def test_codigo_confere_tudo_antes_de_existir():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1');await conectar(conn);tok=token_de(await loja(client))
        r=await client.post(P+'/finalizar',json=pedido(tok,ids,total_visto=1));assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(409,'preco_alterado')
        r=await client.post(P+'/finalizar',json=pedido(tok,ids,modalidade='delivery',total_visto=68));assert r.status_code==422
        assert await conn.fetchval('SELECT count(*) FROM cardapio_rascunhos')==0
        # Link de conversa não finaliza por código: ele já envia direto.
        url,_=await CARD.garantir_link(conn,T,c)
        r=await client.post(P+'/finalizar',json=pedido(url.split('#')[1],ids));assert (r.status_code,motivo_de(r))==(403,'previa')
    em_banco(cenario)


def test_preco_mudou_ou_codigo_venceu_o_cliente_e_avisado_e_nao_ha_pedido():
    async def cenario(conn,client,conversa,ids,api):
        canal=await conectar(conn);tok=token_de(await loja(client))
        a=(await client.post(P+'/finalizar',json=pedido(tok,ids))).json()['codigo']
        b=(await client.post(P+'/finalizar',json=pedido(tok,ids))).json()['codigo']
        await conn.execute("UPDATE catalogo_itens SET preco=preco+1 WHERE tenant_id=$1 AND id=$2",T,ids['GUE-0001'])
        s=await recebe(client,canal,f'Quero finalizar meu pedido #{a}');assert (s['finalizacao'],s['motivo'])==('recusada','preco_alterado')
        assert (await client.post(P+'/situacao',json={'token':tok,'codigo':a})).json()=={'estado':'recusado','motivo':'preco_alterado'}
        await conn.execute('UPDATE cardapio_rascunhos SET expira_em=now()-interval \'1 minute\' WHERE codigo=$1',b)
        s=await recebe(client,canal,f'pedido #{b.lower()}',mid='m2');assert s['motivo']=='codigo_expirado'
        assert await conn.fetchval('SELECT count(*) FROM pedidos')==0
        textos=[r['texto'] for r in await conn.fetch("SELECT texto FROM avisos_internos WHERE tenant_id=$1 AND tipo='pedido_cliente' ORDER BY id",T)]
        assert len(textos)==2 and 'um preço mudou' in textos[0] and 'código venceu' in textos[1] and 'painel.exemplo.test/cardapio#' in textos[0]
    em_banco(cenario)


def test_mensagem_comum_e_codigo_de_outra_empresa_seguem_para_o_agente():
    async def cenario(conn,client,conversa,ids,api):
        canal=await conectar(conn);tok=token_de(await loja(client))
        c=(await client.post(P+'/finalizar',json=pedido(tok,ids))).json()['codigo']
        for n,texto in enumerate(('oi, quero um pedido','pedido #ZZZZZZ',f'#{c}')):
            s=await recebe(client,canal,texto,mid=f'c{n}');assert 'finalizacao' not in s,texto
        assert await CARD.finalizar_pela_mensagem(conn,'outra_empresa','conv',f'pedido #{c}') is None
        assert await conn.fetchval('SELECT count(*) FROM pedidos')==0
    em_banco(cenario)


def test_cliente_fora_do_piloto_tambem_finaliza():
    async def cenario(conn,client,conversa,ids,api):
        canal=await conectar(conn);tok=token_de(await loja(client))
        r=await client.patch(f'/admin/v1/tenants/{T}/agentes/bykoji_atendimento',json={'modo_piloto':True,'contatos_permitidos':['5511900000599']});assert r.status_code==200,r.text
        assert (await recebe(client,canal,'oi',mid='p0')).get('ignorado') is True
        c=(await client.post(P+'/finalizar',json=pedido(tok,ids))).json()['codigo']
        assert (await recebe(client,canal,f'pedido #{c}',mid='p1'))['finalizacao']=='pedido'
    em_banco(cenario)


def test_link_da_loja_nao_tem_limite_do_link():
    async def cenario(conn,client,conversa,ids,api):
        tok=token_de(await loja(client))
        for _ in range(CARD.LIMITE_POR_MINUTO+5): await client.post(P+'/ler',json={'token':tok})
        assert (await client.post(P+'/ler',json={'token':tok})).status_code==200
    em_banco(cenario)
