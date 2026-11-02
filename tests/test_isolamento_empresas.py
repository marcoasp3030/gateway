"""Isolamento entre empresas: a sessão de uma empresa ataca todas as rotas com ids reais de outra. Nada pode vazar nem mudar.
Rota nova entra sozinha na varredura. Ids no corpo têm a lista própria, porque corpo inválido para na validação."""
import re
from starlette.routing import Route
import limites
from test_cat3a import cenario_cat3,T
from test_admin import criar
from test_fase4_pedidos import CSV,ativo

R='rival'
H={'X-Role':'gestor','X-Tenant-Scope':R,'X-Actor':'rival'}
UR=f'/admin/v1/tenants/{R}'
CANARIO='canario_vitima'
LITERAIS={'entidade':['agentes','canais','api-keys'],'status':['em_preparo','confirmado','cancelado'],'tipo':['logo'],'modulo':['catalogo'],
          'versao':['1'],'segmento_id':['geral'],'modelo_id':['generico_teste']}
PELO_NOME={'conv':'conversas','conversation_id':'conversas','conversa_id':'conversas','item_id':'catalogo_itens','prato_id':'catalogo_pratos',
           'categoria_id':'catalogo_categorias','agente_id':'agentes','agente':'agentes','canal':'tenant_canais','numero':'numero','uid':'tenant_unidades'}


async def tabelas(conn):
    return [r[0] for r in await conn.fetch("""SELECT table_name FROM information_schema.columns WHERE table_schema=current_schema()
        AND column_name='tenant_id' AND table_name NOT IN ('audit_log','gestao_alteracoes') ORDER BY 1""")]


async def foto(conn):
    s={'tenants':await conn.fetchval('SELECT md5(x::text) FROM tenants x WHERE id=$1',T)}
    for t in await tabelas(conn):
        s[t]=await conn.fetchval(f"SELECT md5(coalesce(string_agg(x::text,'|' ORDER BY x::text),'')) FROM {t} x WHERE tenant_id=$1",T)
    return s


async def ids_de(conn,tenant):
    pool={}
    for t in await tabelas(conn):
        if await conn.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=$1 AND column_name='id'",t):
            pool[t]=[r[0] for r in await conn.fetch(f'SELECT id::text FROM {t} WHERE tenant_id=$1 ORDER BY id::text LIMIT 2',tenant)]
    pool['conversas']=[r[0] for r in await conn.fetch('SELECT conversation_id FROM conversas WHERE tenant_id=$1 LIMIT 2',tenant)]
    pool['numero']=[r[0] for r in await conn.fetch('SELECT numero FROM pedidos WHERE tenant_id=$1 LIMIT 2',tenant)]
    return pool


def candidatos(nome,pool):
    if nome in LITERAIS: return LITERAIS[nome]
    if pool.get(PELO_NOME.get(nome)): return pool[PELO_NOME[nome]]
    return [v for vs in pool.values() for v in vs] or ['1']


def caminhos(rota,pool):
    nomes=[n for n in re.findall(r'\{(\w+)',rota) if n not in ('tenant_id','tenant')]
    def montar(valores):
        return re.sub(r'\{(\w+)(?::\w+)?\}',lambda m:R if m.group(1) in ('tenant_id','tenant') else valores[m.group(1)].replace('/','%2F'),rota)
    primeiros={n:candidatos(n,pool)[0] for n in nomes}
    for n in nomes:
        for v in candidatos(n,pool): yield montar({**primeiros,n:v})


def test_cotas_separam_as_empresas():
    ip='203.0.113.9'
    a=limites.cotas('webhooks',ip,'/webhooks/uazapi/can_a',{});b=limites.cotas('webhooks',ip,'/webhooks/uazapi/can_b',{})
    assert a[1][0]!=b[1][0] and a[0]==b[0]   # mesmo teto de IP, cota própria por canal
    a=limites.cotas('api',ip,'/api/v1/pedidos',{'x-api-key':'a'});b=limites.cotas('api',ip,'/api/v1/pedidos',{'x-api-key':'b'})
    assert a[1][0]!=b[1][0] and a[0]==b[0]
    assert limites.cotas('api',ip,'/api/v1/publico/cardapio/ler',{})==[(ip,10,20)]
    assert limites.ip_cliente('172.18.0.5',{'x-real-ip':ip})==ip   # o painel informa o cliente
    assert limites.ip_cliente('8.8.8.8',{'x-real-ip':ip})=='8.8.8.8'   # origem pública não forja


def test_sessao_e_chave_de_uma_empresa_nao_alcancam_outra(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        app=client._transport.app;U=f'/admin/v1/tenants/{T}';iv=ids['GUE-0001']
        conv=await conversa('cliente1');await conversa('cliente2')
        await api('POST',f'/carrinho/{conv}/itens',json={'item_id':ids['GUE-0002'],'quantidade':1})
        await api('PUT',f'/carrinho/{conv}/entrega',json={'unidade':'patio_guedala','modalidade':'retirada'})
        assert (await api('POST','/pedidos',json={'conversation_id':conv})).status_code==201
        assert (await client.post(f'{U}/conversas/{conv}/notas',json={'texto':CANARIO})).status_code==201
        assert (await client.post(f'{U}/etiquetas',json={'nome':CANARIO})).status_code==201
        assert (await client.post(f'{U}/catalogo/pratos',json={'itens':[iv]})).status_code==201
        grupo={'nome':'G','minimo':0,'maximo':1,'opcoes':[{'nome':'o','preco_extra':0}]}
        assert (await client.put(f'{U}/catalogo/itens/{iv}/opcoes',json={'grupos':[grupo]})).status_code==200
        await conn.execute('UPDATE catalogo_itens SET nome=$2 WHERE tenant_id=$1 AND id=$3',T,CANARIO,iv)
        await criar(client,R)
        await conn.execute("INSERT INTO modulos_tenant(tenant_id,modulo,ativo) VALUES ($1,'catalogo',true),($1,'pedidos',true) ON CONFLICT DO NOTHING",R)
        assert (await client.post(f'{UR}/catalogo/importar',params={'simulacao':'false'},content=CSV.encode())).status_code==200
        assert (await client.post(f'{UR}/unidades',json={'nome':'Rival','codigo':'rival_u'})).status_code==201
        ag=await conn.fetchval('SELECT id FROM agentes WHERE tenant_id=$1',R);ca=await conn.fetchval('SELECT id FROM tenant_canais WHERE tenant_id=$1',R)
        await ativo(client,R,ag,ca)
        assert (await client.post(f'{UR}/agentes/{ag}/simular',json={'canal_id':ca,'identificador':'rival1','texto':'oi'})).status_code==200
        vit=await ids_de(conn,T);riv=await ids_de(conn,R)
        ir=int(riv['catalogo_itens'][0]);cv=int(vit['catalogo_categorias'][0]);cr=int(riv['catalogo_categorias'][0])
        conv_r=riv['conversas'][0];ag_v=vit['agentes'][0];ca_v=vit['tenant_canais'][0]
        et=int(await conn.fetchval('SELECT id FROM etiquetas WHERE tenant_id=$1',T))
        gv=await conn.fetchval('SELECT id FROM catalogo_grupos WHERE tenant_id=$1',T);ov=await conn.fetchval('SELECT id FROM catalogo_opcoes WHERE tenant_id=$1',T)
        assert (await client.post(f'{UR}/catalogo/pratos',json={'itens':[ir]})).status_code==201
        prv=await conn.fetchval('SELECT id FROM catalogo_pratos WHERE tenant_id=$1',R)
        antes=await foto(conn);vazou=[]

        async def tenta(metodo,url,**kw):
            try: r=await client.request(metodo,url,headers=kw.pop('headers',H),**kw)
            except (AttributeError,LookupError): return None   # dublê de Redis do teste ou falha fora do assunto: nada respondido
            if CANARIO in r.text or f'{T}:' in r.text: vazou.append((metodo,url,r.status_code,r.text[:160]))
            return r

        # Ids da vítima no caminho.
        rotas=sorted({(m,r.path) for r in app.routes if isinstance(r,Route) and r.path.startswith('/admin/v1/tenants/{') for m in r.methods-{'HEAD','OPTIONS'}})
        assert len(rotas)>150
        for metodo,rota in rotas:
            if not re.search(r'\{(?!tenant)',rota): continue
            for url in caminhos(rota,vit):
                await tenta(metodo,url,**({} if metodo=='GET' else {'json':{}}))
        # Ids da vítima em corpos bem formados: nenhum pode ser aceito.
        for metodo,url,b in [
            ('POST','/catalogo/copiar',{'itens':[iv],'destino':'rival_u'}),('POST','/catalogo/copiar',{'categoria_id':cv,'destino':'rival_u'}),
            ('POST','/catalogo/copiar',{'itens':[ir],'destino':'patio_guedala'}),
            ('POST','/catalogo/lote',{'acao':'arquivar','itens':[iv]}),('POST','/catalogo/lote',{'acao':'reajustar','percentual':10,'itens':[iv]}),
            ('POST','/catalogo/lote',{'acao':'mover','categoria_id':cv,'itens':[ir]}),('POST','/catalogo/pratos',{'itens':[iv]}),
            ('POST',f'/catalogo/pratos/{prv}/itens',{'itens':[iv]}),('POST','/catalogo/itens',{'unidade':'rival_u','categoria_id':cv,'nome':'x','preco':1}),
            ('PATCH',f'/catalogo/itens/{ir}',{'categoria_id':cv}),('POST',f'/catalogo/itens/{ir}/duplicar',{'categoria_id':cv}),
            ('PUT',f'/catalogo/itens/{ir}/opcoes',{'grupos':[{**grupo,'id':gv}]}),
            ('PUT',f'/catalogo/itens/{ir}/opcoes',{'grupos':[{**grupo,'opcoes':[{'id':ov,'nome':'o','preco_extra':0}]}]}),
            ('PUT','/catalogo/categorias/ordem',{'ids':[cv,cr]}),('POST','/canais',{'nome':'x','tipo':'api','agente_id':ag_v}),
            ('POST','/api-keys',{'nome':'x','escopos':['eventos:ler'],'agente_id':ag_v}),
            ('POST',f'/agentes/{ag}/simular',{'canal_id':ca_v,'identificador':'x','texto':'oi'}),
            ('PUT',f'/conversas/{conv_r}/etiquetas',{'ids':[et]})]:
            r=await tenta(metodo,UR+url,json=b)
            sem_efeito=r.status_code==200 and all(not x.get('ok') for x in r.json().get('resultados',[])) and 'resultados' in r.json()
            assert r.status_code>=400 or sem_efeito,(metodo,url,r.status_code,r.text[:200])
        # Truques de endereço e cabeçalho.
        for url in (f'/admin/v1/tenants/{T}',f'/admin/v1//tenants/{T}',f'/admin/v1/tenants//{T}',f'/admin/v1/tenants/{R}/../{T}',f'/admin/v1/tenants/{R}/%2e%2e/{T}',
                    f'/admin/v1/tenants/{T}%2F',f'/admin//v1/tenants/{T}',f'/admin/v1/tenants/%62ykoji',f'/admin/v1/tenants/{T}/agentes'):
            r=await tenta('GET',url);assert r.status_code in (403,404),(url,r.status_code)
        assert (await tenta('GET',U,headers={**H,'X-Tenant-Scope':f'{T}, {R}'})).status_code==404
        # Chave de API da rival.
        r=await client.post(f'{UR}/api-keys',json={'nome':'k','escopos':['notificar','eventos:ler','ferramentas:catalogo','ferramentas:pedidos']})
        kr=r.json()['chave'];num=vit['numero'][0]
        for metodo,url,b in [('GET',f'/carrinho/{conv}',None),('POST',f'/carrinho/{conv}/itens',{'item_id':iv,'quantidade':1}),('POST',f'/carrinho/{conv_r}/itens',{'item_id':iv,'quantidade':1}),
                             ('DELETE',f'/carrinho/{conv}',None),('PUT',f'/carrinho/{conv}/entrega',{'unidade':'patio_guedala','modalidade':'retirada'}),
                             ('POST','/pedidos',{'conversation_id':conv}),('GET',f'/pedidos?conversation_id={conv}',None),('GET',f'/pedidos/{num}?conversation_id={conv}',None),
                             ('POST',f'/pedidos/{num}/cancelar?conversation_id={conv}',{'motivo':'x'}),('GET',f'/conversas/{conv}/mensagens',None),
                             ('POST',f'/conversas/{conv}/notificar',{'evento':'x','instrucao':'x'})]:
            r=await tenta(metodo,'/api/v1'+url,json=b,headers={'X-API-Key':kr,'Authorization':''})
            assert r.status_code>=400,(metodo,url,r.status_code,r.text[:200])
        assert vazou==[],vazou[:5]
        depois=await foto(conn)
        assert [t for t in antes if antes[t]!=depois[t]]==[]
    cenario_cat3(monkeypatch,corpo)
