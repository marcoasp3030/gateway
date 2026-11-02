"""Cardápio interativo: link seguro, nenhum dado do cliente na página e pedido pelas regras de sempre."""
import asyncio,json
import httpx,pytest
from modulos.pedidos import cardapio as CARD
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
T='bykoji'
P='/api/v1/publico/cardapio'
ENDERECO='Rua das Acácias, 100'


def em_banco(cenario):
    async def tudo(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            await cenario(conn,client,conversa,ids,api)
    banco(tudo)


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setenv('PAINEL_PUBLIC_URL','https://painel.exemplo.test/')
    monkeypatch.setenv('API_KEY_PEPPER','pepper-de-teste')


async def link(conn,conversa):
    url,_=await CARD.garantir_link(conn,T,conversa)
    assert url.startswith('https://painel.exemplo.test/cardapio#')
    return url.split('#')[1]


def motivo_de(r): return r.json()['erro']['detalhes']['motivo']


def pedido(token,ids,**extra):
    return {'token':token,'unidade':'patio_guedala','itens':[{'item_id':ids['GUE-0001'],'quantidade':2}],'modalidade':'retirada',
            'forma_pagamento':'pix','total_visto':68,**extra}


def test_link_e_o_mesmo_na_conversa_e_o_token_nao_fica_no_banco():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1')
        t1=await link(conn,c);t2=await link(conn,c)
        assert t1==t2 and len(t1)>=22
        linha=await conn.fetchrow('SELECT * FROM cardapio_links WHERE conversation_id=$1',c)
        assert t1 not in json.dumps({k:str(v) for k,v in dict(linha).items()})
        assert await conn.fetchval('SELECT count(*) FROM cardapio_links WHERE conversation_id=$1',c)==1
        # Link quase vencido é trocado.
        await conn.execute("UPDATE cardapio_links SET expira_em=now()+interval '1 hour' WHERE conversation_id=$1",c)
        assert await link(conn,c)!=t1
    em_banco(cenario)


def test_sem_endereco_publico_nao_ha_link(monkeypatch):
    async def cenario(conn,client,conversa,ids,api):
        monkeypatch.delenv('PAINEL_PUBLIC_URL')
        c=await conversa('cliente1')
        assert await CARD.garantir_link(conn,T,c)==(None,None)
    em_banco(cenario)


def test_pagina_nao_mostra_dado_do_cliente_nem_item_que_nao_se_vende():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('5511900000777')
        await api('POST',f'/carrinho/{c}/itens',json={'item_id':ids['GUE-0001']})
        await api('PUT',f'/carrinho/{c}/entrega',json={'modalidade':'delivery','endereco':ENDERECO})
        tok=await link(conn,c)
        r=await client.post(P+'/ler',json={'token':tok});assert r.status_code==200,r.text
        assert r.headers['cache-control']=='no-store'
        assert ENDERECO not in r.text and '5511900000777' not in r.text and 'conversation_id' not in r.text
        d=r.json();assert d['carrinho']['tem_endereco'] is True and d['carrinho']['modalidade']=='delivery'
        assert [i['item_id'] for i in d['carrinho']['itens']]==[ids['GUE-0001']]
        assert d['unidades'][0]['formas'][0]['codigo']=='pix'
        r=await client.post(P+'/unidade',json={'token':tok,'unidade':'patio_guedala'});assert r.status_code==200,r.text
        itens={i['item_id'] for cat in r.json()['categorias'] for i in cat['itens']}
        # Sem preço, opção de executivo e item só do salão não aparecem.
        assert ids['GUE-0001'] in itens and not {ids['GUE-0003'],ids['GUE-0006'],ids['GUE-0007']}&itens
        assert (await client.post(P+'/unidade',json={'token':tok,'unidade':'nao_existe'})).status_code==404
    em_banco(cenario)


def test_pedido_passa_pelas_regras_e_o_segundo_envio_nao_duplica():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1');tok=await link(conn,c)
        r=await client.post(P+'/pedido',json=pedido(tok,ids));assert r.status_code==201,r.text
        numero=r.json()['numero']
        p=await conn.fetchrow('SELECT * FROM pedidos WHERE tenant_id=$1 AND numero=$2',T,numero)
        assert (float(p['total']),p['forma_pagamento'],p['conversation_id'],p['status'])==(68.0,'pix',c,'aguardando_loja')
        # Duplo toque ou reenvio: mesmo número, um pedido só.
        r2=await client.post(P+'/pedido',json=pedido(tok,ids));assert r2.status_code==200 and r2.json()['numero']==numero and r2.json()['repetido']
        assert await conn.fetchval('SELECT count(*) FROM pedidos WHERE conversation_id=$1',c)==1
        assert (await client.post(P+'/ler',json={'token':tok})).json()['usado']['numero']==numero
        assert await conn.fetchval("SELECT count(*) FROM avisos_internos WHERE tipo='pedido_cliente' AND referencia=$1",f'{numero}:recebido')==1
        # O agente enxerga: ação registrada e um link novo para a conversa.
        assert await conn.fetchval("SELECT count(*) FROM skill_chamadas WHERE conversation_id=$1 AND skill='fechar_pedido' AND resultado='ok'",c)>=1
        assert await link(conn,c)!=tok
    em_banco(cenario)


def test_preco_vem_do_catalogo_e_recusa_nao_mexe_no_carrinho():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1')
        await api('POST',f'/carrinho/{c}/itens',json={'item_id':ids['GUE-0002'],'quantidade':3})
        tok=await link(conn,c)
        # Total forjado: nada é gravado e a página recebe o valor certo.
        r=await client.post(P+'/pedido',json=pedido(tok,ids,total_visto=1));assert r.status_code==409,r.text
        assert r.json()['erro']['detalhes']['motivo']=='preco_alterado' and r.json()['erro']['detalhes']['total']==68
        linhas=await conn.fetch('SELECT l.item_id,l.quantidade FROM carrinho_itens l JOIN carrinhos k ON k.id=l.carrinho_id WHERE k.conversation_id=$1',c)
        assert [(l['item_id'],l['quantidade']) for l in linhas]==[(ids['GUE-0002'],3)]
        assert await conn.fetchval('SELECT count(*) FROM pedidos WHERE conversation_id=$1',c)==0
        # Quantidade e campos fora do combinado nem chegam à regra.
        ruim=pedido(tok,ids);ruim['itens'][0]['preco_unitario']=1
        assert (await client.post(P+'/pedido',json=ruim)).status_code in (400,422)
        # Depois da recusa, o mesmo link continua servindo.
        assert (await client.post(P+'/pedido',json=pedido(tok,ids))).status_code==201
    em_banco(cenario)


def test_regras_do_pedido_valem_na_pagina():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1');tok=await link(conn,c)
        def motivo(r): return r.json()['erro']['detalhes']['motivo']
        item=lambda codigo:[{'item_id':ids[codigo],'quantidade':1}]
        # Bebida alcoólica sem confirmar maioridade.
        r=await client.post(P+'/pedido',json=pedido(tok,ids,itens=item('GUE-0004'),total_visto=55));assert motivo(r)=='falta_maioridade'
        r=await client.post(P+'/pedido',json=pedido(tok,ids,itens=item('GUE-0004'),total_visto=55,maioridade_confirmada=True));assert r.status_code==201,r.text
        c2=await conversa('cliente2');tok=await link(conn,c2)
        # Item de outra unidade, item sem preço e item do salão.
        assert motivo(await client.post(P+'/pedido',json=pedido(tok,ids,itens=item('MOR-0001'),total_visto=30)))=='unidade_diferente'
        assert motivo(await client.post(P+'/pedido',json=pedido(tok,ids,itens=item('GUE-0003'))))=='sem_preco'
        assert motivo(await client.post(P+'/pedido',json=pedido(tok,ids,itens=item('GUE-0007'))))=='sem_delivery'
        # Entrega sem endereço, forma que a unidade não aceita e troco menor que o total.
        assert motivo(await client.post(P+'/pedido',json=pedido(tok,ids,modalidade='delivery')))=='falta_endereco'
        assert motivo(await client.post(P+'/pedido',json=pedido(tok,ids,forma_pagamento='cheque')))=='forma_pagamento_invalida'
        assert motivo(await client.post(P+'/pedido',json=pedido(tok,ids,forma_pagamento='dinheiro',troco_para=10)))=='troco_menor_que_total'
        await conn.execute("UPDATE tenant_unidades SET pedido_minimo=100 WHERE tenant_id=$1",T)
        assert motivo(await client.post(P+'/pedido',json=pedido(tok,ids)))=='abaixo_do_minimo'
        assert await conn.fetchval('SELECT count(*) FROM pedidos WHERE conversation_id=$1',c2)==0
    em_banco(cenario)


def test_endereco_dito_na_conversa_vale_sem_aparecer_na_pagina():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1')
        await api('POST',f'/carrinho/{c}/itens',json={'item_id':ids['GUE-0001']})
        await api('PUT',f'/carrinho/{c}/entrega',json={'modalidade':'delivery','endereco':ENDERECO})
        tok=await link(conn,c)
        r=await client.post(P+'/pedido',json=pedido(tok,ids,modalidade='delivery'));assert r.status_code==201,r.text
        assert await conn.fetchval('SELECT endereco FROM pedidos WHERE conversation_id=$1',c)==ENDERECO
    em_banco(cenario)


def test_link_invalido_vencido_limite_e_empresa_fora():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1');tok=await link(conn,c)
        r=await client.post(P+'/ler',json={'token':'x'*22});assert r.status_code==404 and motivo_de(r)=='link_invalido'
        await conn.execute("UPDATE cardapio_links SET expira_em=now()-interval '1 minute' WHERE conversation_id=$1",c)
        r=await client.post(P+'/pedido',json=pedido(tok,ids));assert r.status_code==410 and motivo_de(r)=='link_expirado'
        assert await conn.fetchval('SELECT count(*) FROM pedidos WHERE conversation_id=$1',c)==0
        await conn.execute("UPDATE cardapio_links SET expira_em=now()+interval '1 day' WHERE conversation_id=$1",c)
        await conn.execute("UPDATE cardapio_links SET janela=date_trunc('minute',now()),chamadas=$2 WHERE conversation_id=$1",c,CARD.LIMITE_POR_MINUTO)
        r=await client.post(P+'/ler',json={'token':tok});assert r.status_code==429 and motivo_de(r)=='muitas_chamadas'
        await conn.execute("UPDATE cardapio_links SET chamadas=0 WHERE conversation_id=$1",c)
        await conn.execute("UPDATE tenants SET status='suspenso' WHERE id=$1",T)
        assert (await client.post(P+'/ler',json={'token':tok})).status_code==404
        await conn.execute("UPDATE tenants SET status='ativo' WHERE id=$1",T)
        await conn.execute("UPDATE modulos_tenant SET ativo=false WHERE tenant_id=$1 AND modulo='pedidos'",T)
        assert (await client.post(P+'/ler',json={'token':tok})).status_code==404
    em_banco(cenario)


def test_painel_gera_link_e_previa_nao_fecha_pedido():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1')
        F={'X-Role':'funcionario','X-Actor':'ana@exemplo.test'};S={'X-Role':'supervisor','X-Actor':'sup@exemplo.test'}
        r=await client.post(f'/admin/v1/tenants/{T}/conversas/{c}/cardapio-link',headers=F);assert r.status_code==200,r.text
        assert r.json()['url'].split('#')[1]==await link(conn,c)
        assert (await client.post(f'/admin/v1/tenants/{T}/conversas/{T}:nao:existe/cardapio-link',headers=F)).status_code==404
        assert (await client.post(f'/admin/v1/tenants/{T}/cardapio/previa',headers=F)).status_code==403
        r=await client.post(f'/admin/v1/tenants/{T}/cardapio/previa',headers=S);assert r.status_code==200,r.text
        tok=r.json()['url'].split('#')[1]
        d=(await client.post(P+'/ler',json={'token':tok})).json();assert d['previa'] is True and d['carrinho'] is None
        assert (await client.post(P+'/unidade',json={'token':tok,'unidade':'patio_guedala'})).status_code==200
        r=await client.post(P+'/pedido',json=pedido(tok,ids));assert r.status_code==403 and motivo_de(r)=='previa'
    em_banco(cenario)


def test_anonimizar_o_contato_apaga_o_link():
    async def cenario(conn,client,conversa,ids,api):
        cliente='5511900000778';c=await conversa(cliente);tok=await link(conn,c)
        cid=await conn.fetchval('SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,c)
        r=await client.request('DELETE',f'/admin/v1/tenants/{T}/contatos/{cid}',json={'confirmacao':cliente},
                               headers={'X-Role':'gestor','X-Actor':'gestor@exemplo.test'});assert r.status_code==200,r.text
        assert await conn.fetchval('SELECT count(*) FROM cardapio_links WHERE tenant_id=$1',T)==0
        assert (await client.post(P+'/ler',json={'token':tok})).status_code==404
    em_banco(cenario)


def http_falso(respostas):
    chamadas=[]
    def responder(req):
        chamadas.append(str(req.url));r=respostas[req.url.host]
        if isinstance(r,Exception): raise r
        return r
    return httpx.AsyncClient(transport=httpx.MockTransport(responder)),chamadas


def test_cep_usa_a_segunda_fonte_e_distingue_inexistente_de_fora_do_ar():
    VIA,BR='viacep.com.br','brasilapi.com.br'
    ok=httpx.Response(200,json={'logradouro':' Rua A ','bairro':'Centro','localidade':'São Paulo','uf':'SP','complemento':'nao usar'})
    br=httpx.Response(200,json={'street':'Rua B','neighborhood':'Jardim','city':'Campinas','state':'SP'})
    async def tudo():
        http,chamadas=http_falso({VIA:ok})
        assert await CARD.consultar_cep(http,'01310100')=={'rua':'Rua A','bairro':'Centro','cidade':'São Paulo','uf':'SP'}
        assert len(chamadas)==1
        http,_=http_falso({VIA:httpx.Response(200,json={'erro':'true'})})
        assert await CARD.consultar_cep(http,'00000000') is None
        http,_=http_falso({VIA:httpx.ConnectError('fora'),BR:br})
        assert (await CARD.consultar_cep(http,'13010000'))['cidade']=='Campinas'
        http,_=http_falso({VIA:httpx.Response(500),BR:httpx.Response(404)})
        assert await CARD.consultar_cep(http,'13010000') is None
        http,_=http_falso({VIA:httpx.ConnectError('fora'),BR:httpx.ConnectError('fora')})
        with pytest.raises(RuntimeError): await CARD.consultar_cep(http,'13010000')
    asyncio.run(tudo())


def test_cep_exige_link_valido_e_formato(monkeypatch):
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1');tok=await link(conn,c)
        async def achar(http,cep):
            if cep=='00000000': return None
            if cep=='99999999': raise RuntimeError('fora')
            return {'rua':'Rua A','bairro':'Centro','cidade':'São Paulo','uf':'SP'}
        monkeypatch.setattr(CARD,'consultar_cep',achar)
        assert (await client.post(P+'/cep',json={'token':'x'*22,'cep':'01310100'})).status_code==404
        assert (await client.post(P+'/cep',json={'token':tok,'cep':'01310-100'})).status_code in (400,422)
        assert (await client.post(P+'/cep',json={'token':tok,'cep':'../../x'})).status_code in (400,422)
        r=await client.post(P+'/cep',json={'token':tok,'cep':'01310100'});assert r.status_code==200 and r.json()['rua']=='Rua A'
        r=await client.post(P+'/cep',json={'token':tok,'cep':'00000000'});assert r.status_code==404 and motivo_de(r)=='cep_inexistente'
        r=await client.post(P+'/cep',json={'token':tok,'cep':'99999999'});assert r.status_code==503 and motivo_de(r)=='cep_indisponivel'
        await conn.execute("UPDATE cardapio_links SET janela=date_trunc('minute',now()),chamadas=$2 WHERE conversation_id=$1",c,CARD.LIMITE_POR_MINUTO)
        assert (await client.post(P+'/cep',json={'token':tok,'cep':'01310100'})).status_code==429
    em_banco(cenario)


def test_unidade_fechada_nao_recebe_pedido_mesmo_chamando_a_rota_direto():
    async def cenario(conn,client,conversa,ids,api):
        c=await conversa('cliente1');tok=await link(conn,c)
        await conn.execute("UPDATE tenant_unidades SET pausa_indefinida=true WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        d=(await client.post(P+'/ler',json={'token':tok})).json()
        assert d['unidades'][0]['aberta_agora'] is False
        r=await client.post(P+'/pedido',json=pedido(tok,ids));assert r.status_code==422
        assert r.json()['erro']['detalhes']['motivo']=='unidade_fechada'
        assert await conn.fetchval('SELECT count(*) FROM pedidos WHERE conversation_id=$1',c)==0
        assert await conn.fetchval('SELECT usado_em FROM cardapio_links WHERE conversation_id=$1',c) is None
    em_banco(cenario)
