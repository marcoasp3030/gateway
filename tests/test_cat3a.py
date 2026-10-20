"""CAT3-a (docs/ESPEC_CAT3.md, decisões de 20/10/2026): itens, categorias, pratos e sugestões de ligação, cópia entre unidades,
esgotado com volta pela hora, operações em lote, histórico e permissões. O agente só vê diferença quando alguém ordena, esgota,
arquiva ou renomeia (o retrato do By Koji prova o resto: tests/test_multi1b_retrato.py)."""
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
import pytest
from modulos.pedidos import agendamento as AG
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar,motivo

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
SP=ZoneInfo('America/Sao_Paulo')
T='bykoji'
ADMIN={'X-Actor':'ana','X-Role':'admin'}
MEMBRO={'X-Actor':'cozinha','X-Role':'membro'}
CAT=f'/admin/v1/tenants/{T}/catalogo'


def det(r): return r.json()['erro']['detalhes']


def cenario_cat3(monkeypatch,corpo):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        relogio=[datetime(2026,10,13,12,0,tzinfo=SP)];monkeypatch.setattr(AG,'agora_utc',lambda: relogio[0].astimezone(timezone.utc))
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            assert (await client.post(f'/admin/v1/tenants/{T}/unidades',json={'nome':'Morumbi','codigo':'morumbi'})).status_code==201
            await conn.execute("UPDATE tenant_unidades SET horarios=$2::jsonb,aceita_retirada=true WHERE tenant_id=$1",T,
                               '{"seg":[["00:00","23:59"]],"ter":[["00:00","23:59"]],"qua":[["00:00","23:59"]],"qui":[["00:00","23:59"]],"sex":[["00:00","23:59"]],"sab":[["00:00","23:59"]],"dom":[["00:00","23:59"]]}')
            await corpo(conn=conn,client=client,api=api,ids=ids,conversa=conversa,relogio=relogio)
    banco(cenario)


async def categoria(conn,unidade,nome):
    return await conn.fetchval('SELECT id FROM catalogo_categorias WHERE tenant_id=$1 AND unidade=$2 AND nome=$3',T,unidade,nome)


def test_migracao_categorias_prefixos_e_gatilho(tmp_path):
    """Itens gravados antes da 046: uma categoria por (unidade, texto), ligada; prefixo da unidade pelos códigos de hoje; depois,
    item gravado só pelo texto (CSV, CAT2, imagem anterior) acha a categoria, ou cria no fim."""
    for arquivo in migrar.PASTA.glob('*.sql'):
        if arquivo.name<'046': (tmp_path/arquivo.name).write_bytes(arquivo.read_bytes())
    async def cenario(conn):
        await migrar.aplicar(conn,tmp_path)
        await conn.execute("INSERT INTO tenant_unidades(id,tenant_id,nome,codigo) VALUES ('u1','bykoji','Pátio','patio_guedala'),('u2','bykoji','Novo','novo')")
        for codigo,unidade,cat,nome in (('GUE-0001','patio_guedala','Entradas','Edamame'),('GUE-0002','patio_guedala','Pratos','Ebi'),
                                        ('GUE-0003','patio_guedala','Entradas','Gyoza')):
            await conn.execute("INSERT INTO catalogo_itens(tenant_id,codigo,unidade,categoria,nome,preco) VALUES ('bykoji',$1,$2,$3,$4,10)",codigo,unidade,cat,nome)
        assert await migrar.aplicar(conn)==['046_cat3a_cardapio.sql']
        cats=await conn.fetch("SELECT id,unidade,nome,posicao FROM catalogo_categorias ORDER BY id")
        assert [(c['unidade'],c['nome'],c['posicao']) for c in cats]==[('patio_guedala','Entradas',None),('patio_guedala','Pratos',None)]
        assert {r['nome']:r['categoria_id'] for r in await conn.fetch('SELECT nome,categoria_id FROM catalogo_itens')}=={'Edamame':cats[0]['id'],'Ebi':cats[1]['id'],'Gyoza':cats[0]['id']}
        assert dict(await conn.fetch("SELECT codigo,prefixo_codigo FROM tenant_unidades"))=={'patio_guedala':'GUE','novo':None}
        # Gatilho: pelo texto acha a existente; texto novo cria a categoria; trocar o id copia o nome.
        await conn.execute("INSERT INTO catalogo_itens(tenant_id,codigo,unidade,categoria,nome,preco) VALUES ('bykoji','GUE-0004','patio_guedala','Entradas','Shimeji',10)")
        await conn.execute("INSERT INTO catalogo_itens(tenant_id,codigo,unidade,categoria,nome,preco) VALUES ('bykoji','GUE-0005','patio_guedala','Bebidas','Água',5)")
        assert await conn.fetchval("SELECT categoria_id FROM catalogo_itens WHERE codigo='GUE-0004'")==cats[0]['id']
        bebidas=await conn.fetchval("SELECT id FROM catalogo_categorias WHERE nome='Bebidas'")
        assert bebidas and await conn.fetchval("SELECT categoria_id FROM catalogo_itens WHERE codigo='GUE-0005'")==bebidas
        await conn.execute("UPDATE catalogo_itens SET categoria_id=$1 WHERE codigo='GUE-0005'",cats[1]['id'])
        assert await conn.fetchval("SELECT categoria FROM catalogo_itens WHERE codigo='GUE-0005'")=='Pratos'
        assert await migrar.aplicar(conn)==[]
    banco(cenario)


def test_itens_criar_duplicar_arquivar_codigo_e_permissoes(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        entradas=await categoria(conn,'patio_guedala','Entradas quentes')
        novo={'unidade':'patio_guedala','categoria_id':entradas,'nome':'Gyoza','preco':'28,90','descricao':'Pastel japonês'}
        # Membro só lê.
        assert (await client.post(f'{CAT}/itens',json=novo,headers=MEMBRO)).status_code==403
        r=await client.post(f'{CAT}/itens',json=novo,headers=ADMIN);assert r.status_code==201,r.text
        g=r.json()
        assert (g['codigo'],g['nome'],g['preco'],g['ativo'],g['categoria'],g['categoria_id'],g['delivery'],g['tipo'],g['prato'],g['arquivado'])==\
               ('GUE-0008','Gyoza',28.9,True,'Entradas quentes',entradas,'sim','item',None,False)
        # Validações: campo e motivo.
        for corpo_ruim,campo_ in (({**novo,'categoria_id':await categoria(conn,'morumbi','Sashimi')},'categoria_id'),({**novo,'unidade':'xx'},'unidade'),
                                  ({**novo,'preco':0},'preco'),({**novo,'nome':' '},'nome'),({**novo,'tipo':'combo'},'tipo'),({**novo,'outro':1},'outro')):
            r=await client.post(f'{CAT}/itens',json=corpo_ruim,headers=ADMIN);assert (r.status_code,det(r)['campo'])==(400,campo_),corpo_ruim
        r=await client.post(f'{CAT}/itens',json={**novo,'codigo':'GUE-0001'},headers=ADMIN);assert (r.status_code,det(r)['motivo'])==(409,'codigo_em_uso')
        # Código editável; único na empresa.
        r=await client.patch(f'{CAT}/itens/{g["item_id"]}',json={'codigo':'GUE-0001'},headers=ADMIN);assert (r.status_code,det(r)['motivo'])==(409,'codigo_em_uso')
        r=await client.patch(f'{CAT}/itens/{g["item_id"]}',json={'codigo':'GYO-1','alcoolico':False,'delivery':'nao'},headers=ADMIN);assert r.status_code==200
        assert (r.json()['codigo'],r.json()['delivery'])==('GYO-1','nao')
        # Unidade sem prefixo cadastrado ganha um (3 letras do código, sem repetir).
        sashimi=await categoria(conn,'morumbi','Sashimi')
        r=await client.post(f'{CAT}/itens',json={'unidade':'morumbi','categoria_id':sashimi,'nome':'Hot'},headers=ADMIN);assert r.json()['codigo']=='MOR-0002'
        # Duplicar: código novo, inativo, para revisar; em outra unidade, a categoria de mesmo nome é criada.
        r=await client.post(f'{CAT}/itens/{ids["GUE-0001"]}/duplicar',json={'unidade':'morumbi'},headers=ADMIN);assert r.status_code==201,r.text
        d=r.json();assert (d['codigo'],d['ativo'],d['unidade'],d['categoria'],d['preco'])==('MOR-0003',False,'morumbi','Entradas quentes',34.0)
        assert await categoria(conn,'morumbi','Entradas quentes')==d['categoria_id']
        # Arquivar: some do agente e da lista padrão, não pode ser pedido; nunca apaga.
        c=await conversa('cliente-arq')
        r=await client.post(f'{CAT}/itens/{ids["GUE-0001"]}/arquivar',headers=ADMIN);assert r.status_code==200 and r.json()['arquivado'] is True
        assert (await api('GET','/catalogo/busca',params={'conversation_id':c,'q':'edamame'})).json()['itens']==[]
        r=await api('POST',f'/carrinho/{c}/itens',json={'item_id':ids['GUE-0001']});assert motivo(r)=='item_inativo'
        lista=(await client.get(f'{CAT}/itens',params={'unidade':'patio_guedala'})).json()['itens']
        assert ids['GUE-0001'] not in [i['item_id'] for i in lista]
        assert [i['item_id'] for i in (await client.get(f'{CAT}/itens',params={'arquivados':'true'})).json()['itens']]==[ids['GUE-0001']]
        assert (await client.post(f'{CAT}/itens/{ids["GUE-0001"]}/desarquivar',headers=ADMIN)).json()['arquivado'] is False
        assert (await api('GET','/catalogo/busca',params={'conversation_id':c,'q':'edamame'})).json()['itens'][0]['item_id']==ids['GUE-0001']
        # Histórico com quem fez.
        acoes=[r['acao'] for r in await conn.fetch("SELECT acao FROM gestao_alteracoes WHERE tenant_id=$1 AND entidade='catalogo_item' AND actor='ana' ORDER BY id",T)]
        assert acoes.count('criado')==3 and 'arquivado' in acoes and 'desarquivado' in acoes
        # Outra empresa: 404.
        from test_admin import criar
        await criar(client,'alpha')
        assert (await client.post(f'/admin/v1/tenants/alpha/catalogo/itens/{ids["GUE-0002"]}/arquivar',headers=ADMIN)).status_code==404
    cenario_cat3(monkeypatch,corpo)


def test_categorias_ordem_renomear_arquivar_e_o_agente(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        c=await conversa('cliente-cat')
        cats=lambda: api('GET','/catalogo/busca',params={'conversation_id':c,'categorias':'true','unidade':'patio_guedala'})
        antes=[x['categoria'] for x in (await cats()).json()['categorias']]
        assert antes==['Entradas quentes','Pratos quentes','Cervejas','Carpaccios']
        lista=(await client.get(f'{CAT}/categorias',params={'unidade':'patio_guedala'})).json()['categorias']
        assert [x['ordem'] for x in lista]==list(range(1,8)) and all(x['posicao'] is None for x in lista)
        # Criar: nome único na unidade (sem acento/maiúsculas).
        r=await client.post(f'{CAT}/categorias',json={'unidade':'patio_guedala','nome':'ENTRADAS QUENTES'},headers=ADMIN);assert (r.status_code,det(r)['motivo'])==(409,'nome_em_uso')
        r=await client.post(f'{CAT}/categorias',json={'unidade':'patio_guedala','nome':'Combinados'},headers=ADMIN);assert r.status_code==201
        combinados=r.json()['id']
        assert (await client.post(f'{CAT}/categorias',json={'unidade':'patio_guedala','nome':'X'},headers=MEMBRO)).status_code==403
        # Ordenar categorias: vale para o agente.
        ids_cat={x['nome']:x['id'] for x in (await client.get(f'{CAT}/categorias',params={'unidade':'patio_guedala'})).json()['categorias']}
        r=await client.put(f'{CAT}/categorias/ordem',json={'unidade':'patio_guedala','categorias':[ids_cat['Cervejas'],ids_cat['Carpaccios']]},headers=ADMIN);assert r.status_code==200
        assert [x['categoria'] for x in (await cats()).json()['categorias']]==['Cervejas','Carpaccios','Entradas quentes','Pratos quentes']
        r=await client.put(f'{CAT}/categorias/ordem',json={'unidade':'morumbi','categorias':[ids_cat['Cervejas']]},headers=ADMIN);assert det(r)['motivo']=='categoria_de_outra_unidade'
        # Itens dentro da categoria: sem ordem, pelo nome; ordenados, pela posição.
        r=await client.post(f'{CAT}/itens',json={'unidade':'patio_guedala','categoria_id':ids_cat['Entradas quentes'],'nome':'Agedashi','preco':20},headers=ADMIN)
        agedashi=r.json()['item_id'];assert r.json()['posicao'] is None
        nomes=lambda: api('GET','/catalogo/busca',params={'conversation_id':c,'categoria':'Entradas quentes','unidade':'patio_guedala'})
        assert [i['nome'] for i in (await nomes()).json()['itens']]==['Agedashi','Edamame']
        r=await client.put(f'{CAT}/categorias/{ids_cat["Entradas quentes"]}/ordem',json={'itens':[ids['GUE-0001']]},headers=ADMIN);assert r.status_code==200
        assert [i['nome'] for i in (await nomes()).json()['itens']]==['Edamame','Agedashi']
        # Mover de categoria pelo PATCH: entra no fim (a categoria destino não tem ordem: fica sem posição).
        r=await client.patch(f'{CAT}/itens/{agedashi}',json={'categoria_id':combinados},headers=ADMIN);assert (r.json()['categoria'],r.json()['posicao'])==('Combinados',None)
        # Renomear: itens com o nome novo; o nome antigo ainda acha a categoria na busca do agente.
        r=await client.patch(f'{CAT}/categorias/{ids_cat["Cervejas"]}',json={'nome':'Bebidas'},headers=ADMIN);assert r.json()['nomes_anteriores']==['Cervejas']
        assert await conn.fetchval('SELECT categoria FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',T,ids['GUE-0004'])=='Bebidas'
        b=(await api('GET','/catalogo/busca',params={'conversation_id':c,'categoria':'cervejas','unidade':'patio_guedala'})).json()
        assert b['categorias_encontradas']==['Bebidas'] and [i['nome'] for i in b['itens']]==['Sapporo']
        # Arquivar com itens: precisa dizer o que fazer; mover leva os itens.
        r=await client.post(f'{CAT}/categorias/{ids_cat["Bebidas"] if "Bebidas" in ids_cat else ids_cat["Cervejas"]}/arquivar',headers=ADMIN)
        assert (r.status_code,det(r)['motivo'])==(409,'categoria_com_itens')
        r=await client.post(f'{CAT}/categorias/{ids_cat["Cervejas"]}/arquivar',json={'itens':'mover','destino':combinados},headers=ADMIN);assert r.status_code==200
        assert r.json()['itens_movidos']==[ids['GUE-0004']] and r.json()['arquivada'] is True
        assert 'Bebidas' not in [x['nome'] for x in (await client.get(f'{CAT}/categorias',params={'unidade':'patio_guedala'})).json()['categorias']]
        assert 'Bebidas' in [x['nome'] for x in (await client.get(f'{CAT}/categorias',params={'unidade':'patio_guedala','arquivadas':'true'})).json()['categorias']]
        assert (await client.post(f'{CAT}/categorias/{ids_cat["Cervejas"]}/desarquivar',headers=ADMIN)).json()['arquivada'] is False
        assert await conn.fetchval("SELECT count(*) FROM gestao_alteracoes WHERE tenant_id=$1 AND entidade='catalogo_categoria'",T)>=6
    cenario_cat3(monkeypatch,corpo)


def test_pratos_sugestoes_confirmar_ignorar_aplicar_em_e_copiar(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        sashimi=await categoria(conn,'morumbi','Sashimi')
        # Edamame também no Morumbi (nome com grafia diferente) e Sapporo duas vezes no Morumbi.
        for nome in ('EDAMAME!','Sapporo','sapporo'):
            assert (await client.post(f'{CAT}/itens',json={'unidade':'morumbi','categoria_id':sashimi,'nome':nome,'preco':30},headers=ADMIN)).status_code==201
        s=(await client.get(f'{CAT}/pratos/sugestoes')).json()
        assert [g['chave'] for g in s['grupos']]==['edamame','sapporo'] and s['total']==2
        ed,sap=s['grupos']
        assert ed['unidades']==['morumbi','patio_guedala'] and ed['conflito'] is None and ed['prato'] is None and len(ed['itens'])==2
        assert sap['conflito']=='varios_na_unidade'
        # Ignorar em lote some da lista; volta com ignoradas=true; restaurar devolve.
        assert (await client.post(f'{CAT}/pratos/sugestoes/ignorar',json={'chaves':['sapporo']},headers=MEMBRO)).status_code==403
        r=await client.post(f'{CAT}/pratos/sugestoes/ignorar',json={'chaves':['sapporo','nada']},headers=ADMIN)
        assert r.json()['resultados']==[{'chave':'sapporo','ok':True},{'chave':'nada','ok':False,'motivo':'sugestao_inexistente'}]
        assert [g['chave'] for g in (await client.get(f'{CAT}/pratos/sugestoes')).json()['grupos']]==['edamame']
        assert [g['chave'] for g in (await client.get(f'{CAT}/pratos/sugestoes',params={'ignoradas':'true'})).json()['grupos']]==['sapporo']
        assert (await client.post(f'{CAT}/pratos/sugestoes/restaurar',json={'chaves':['sapporo']},headers=ADMIN)).json()=={'restauradas':['sapporo']}
        # Confirmar em lote: o grupo com dois na mesma unidade precisa dos itens escolhidos.
        sap_ids=[i['item_id'] for i in sap['itens']]
        r=await client.post(f'{CAT}/pratos/sugestoes/confirmar',json={'grupos':[{'chave':'edamame'},{'chave':'sapporo'},{'chave':'sapporo','itens':sap_ids[:2]}]},headers=ADMIN)
        res=r.json()['resultados']
        assert res[0]['ok'] and res[0]['prato']['codigo']=='P-0001' and sorted(res[0]['itens_ligados'])==sorted(i['item_id'] for i in ed['itens'])
        assert (res[1]['ok'],res[1]['motivo'])==(False,'varios_na_unidade') and res[2]['ok'] and res[2]['prato']['codigo']=='P-0002'
        # Sobrou o terceiro Sapporo: a sugestão continua, já com o prato, e com o aviso de que a unidade dele já está no prato.
        [resto]=(await client.get(f'{CAT}/pratos/sugestoes')).json()['grupos']
        assert (resto['chave'],resto['prato']['codigo'],resto['conflito'])==('sapporo','P-0002','varios_na_unidade')
        prato=(await client.get(f'{CAT}/pratos/{res[0]["prato"]["id"]}')).json()
        assert [i['unidade'] for i in prato['itens']]==['morumbi','patio_guedala']
        mor_edamame=next(i['item_id'] for i in prato['itens'] if i['unidade']=='morumbi')
        # Aplicar a todas: preço e disponibilidade vão; código e categoria nunca.
        r=await client.patch(f'{CAT}/itens/{ids["GUE-0001"]}',json={'preco':36,'codigo':'GUE-0100','aplicar_em':'todas'},headers=ADMIN);assert r.status_code==200,r.text
        assert r.json()['aplicado_em']==[{'unidade':'morumbi','item_id':mor_edamame,'ok':True,'alterados':['preco']}]
        assert await conn.fetchval('SELECT (preco,codigo)::text FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',T,mor_edamame)=='(36.00,MOR-0002)'
        r=await client.patch(f'{CAT}/itens/{ids["GUE-0002"]}',json={'preco':1,'aplicar_em':'todas'},headers=ADMIN);assert det(r)['motivo']=='sem_prato'
        r=await client.patch(f'{CAT}/itens/{ids["GUE-0001"]}',json={'preco':1,'aplicar_em':['einstein']},headers=ADMIN);assert det(r)['motivo']=='unidade_sem_item_do_prato'
        # Item de outra unidade repetida não entra.
        r=await client.post(f'{CAT}/pratos/{res[0]["prato"]["id"]}/itens',json={'itens':[sap_ids[2]]},headers=ADMIN);assert det(r)['motivo'] in ('unidade_repetida','item_ja_ligado')
        # Copiar para outra unidade: inativo, código do destino, ligado; quem já tem cópia no destino é pulado.
        r=await client.post(f'{CAT}/copiar',json={'itens':[ids['GUE-0001'],ids['GUE-0002']],'destino':'morumbi'},headers=ADMIN);res=r.json()['resultados']
        assert (res[0]['ok'],res[0]['motivo'])==(False,'ja_existe') and res[1]['ok'] and res[1]['codigo'].startswith('MOR-')
        copia=await conn.fetchrow('SELECT ativo,prato_id,categoria,unidade FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',T,res[1]['novo_item_id'])
        ebi_prato=await conn.fetchval('SELECT prato_id FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',T,ids['GUE-0002'])
        assert (copia['ativo'],copia['prato_id'],copia['categoria'],copia['unidade'])==(False,ebi_prato,'Pratos quentes','morumbi') and ebi_prato
        # Prato com itens não é apagado; sem itens, sim.
        assert det(await client.delete(f'{CAT}/pratos/{ebi_prato}',headers=ADMIN))['motivo']=='prato_com_itens'
        for i in (ids['GUE-0002'],res[1]['novo_item_id']): assert (await client.delete(f'{CAT}/pratos/{ebi_prato}/itens/{i}',headers=ADMIN)).status_code==200
        assert (await client.delete(f'{CAT}/pratos/{ebi_prato}',headers=ADMIN)).json()=={'ok':True}
        assert [p['codigo'] for p in (await client.get(f'{CAT}/pratos')).json()['itens']]==['P-0001','P-0002']
    cenario_cat3(monkeypatch,corpo)


def test_esgotado_volta_pela_hora_membro_pode_e_o_agente_ve(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        c=await conversa('cliente-esg');ed=ids['GUE-0001']
        # Membro (cozinha) esgota; até as 18h de hoje no fuso da unidade.
        r=await client.post(f'{CAT}/itens/{ed}/esgotar',json={'ate':'2026-10-13T18:00','motivo':'acabou o edamame'},headers=MEMBRO);assert r.status_code==200,r.text
        g=r.json();assert (g['disponivel_agora'],g['indisponivel_motivo'],g['esgotado_motivo'])==(False,'esgotado','acabou o edamame')
        assert (await client.post(f'{CAT}/itens/{ed}/arquivar',headers=MEMBRO)).status_code==403
        for ruim in ({'ate':'2026-10-13T11:00'},{'ate':'amanha'},{'ate':'2026-12-30T10:00'},{'outro':1}):
            assert (await client.post(f'{CAT}/itens/{ed}/esgotar',json=ruim,headers=MEMBRO)).status_code==400,ruim
        # Agente: indisponível com o motivo e quando volta; adicionar recusa com o item_indisponivel de hoje.
        i=(await api('GET','/catalogo/busca',params={'conversation_id':c,'q':'edamame'})).json()['itens'][0]
        assert (i['disponivel'],i['indisponivel_motivo'],i['disponivel_a_partir'],i['esgotado_resumo'])==(False,'esgotado','2026-10-13T18:00-03:00','esgotado; volta hoje às 18h')
        r=await api('POST',f'/carrinho/{c}/itens',json={'item_id':ed})
        assert (r.status_code,motivo(r),r.json()['erro']['detalhes']['disponivel_a_partir'])==(422,'item_indisponivel','2026-10-13T18:00-03:00')
        # Pedido agendado para depois da volta: disponível (vale a hora do pedido).
        assert (await api('PUT',f'/carrinho/{c}/entrega',json={'unidade':'patio_guedala','modalidade':'retirada','horario':'hoje às 19h'})).status_code==200
        assert (await api('POST',f'/carrinho/{c}/itens',json={'item_id':ed})).status_code==200
        # Sem agendamento, agora: o item no carrinho aparece em itens_indisponiveis e o fechamento recusa.
        await api('PUT',f'/carrinho/{c}/entrega',json={'horario':None})
        k=(await api('GET',f'/carrinho/{c}')).json()['carrinho']
        assert [x['motivo'] for x in k['itens_indisponiveis']]==['esgotado']
        r=await api('POST','/pedidos',json={'conversation_id':c});assert motivo(r)=='item_indisponivel'
        # Volta sozinho na hora, sem tarefa.
        relogio[0]=datetime(2026,10,13,18,0,tzinfo=SP)
        assert (await api('GET','/catalogo/busca',params={'conversation_id':c,'q':'edamame'})).json()['itens'][0]['disponivel'] is True
        # Repor antes da hora; fim do dia; lista dos esgotados.
        relogio[0]=datetime(2026,10,13,12,0,tzinfo=SP)
        r=await client.post(f'{CAT}/itens/{ed}/esgotar',headers=MEMBRO);assert r.json()['esgotado_ate'].startswith('2026-10-14T02:59:59')
        assert [x['item_id'] for x in (await client.get(f'{CAT}/itens',params={'esgotados':'true'})).json()['itens']]==[ed]
        assert (await client.post(f'{CAT}/itens/{ed}/repor',headers=MEMBRO)).json()['esgotado_ate'] is None
        assert (await client.get(f'{CAT}/itens',params={'esgotados':'true'})).json()['itens']==[]
        assert await conn.fetchval("SELECT count(*) FROM gestao_alteracoes WHERE tenant_id=$1 AND entidade='catalogo_item' AND actor='cozinha'",T)==3
    cenario_cat3(monkeypatch,corpo)


def test_lote_reajuste_simulacao_falha_isolada_e_mover(monkeypatch):
    async def corpo(conn,client,api,ids,conversa,relogio):
        itens=[ids['GUE-0001'],ids['GUE-0002'],ids['GUE-0003']]   # o terceiro não tem preço
        assert (await client.post(f'{CAT}/lote',json={'itens':itens,'acao':'reajustar','percentual':10},headers=MEMBRO)).status_code==403
        r=await client.post(f'{CAT}/lote',json={'itens':itens,'acao':'reajustar','percentual':7.5,'simulacao':True},headers=ADMIN);assert r.status_code==200,r.text
        assert r.json()['resultados']==[{'item_id':itens[0],'ok':True,'antes':{'preco':34.0},'depois':{'preco':36.55}},
                                        {'item_id':itens[1],'ok':True,'antes':{'preco':150.0},'depois':{'preco':161.25}},
                                        {'item_id':itens[2],'ok':False,'motivo':'sem_preco'}]
        assert await conn.fetchval('SELECT preco FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',T,itens[0])==34
        r=await client.post(f'{CAT}/lote',json={'itens':itens,'acao':'reajustar','valor':-2},headers=ADMIN)
        assert [x['ok'] for x in r.json()['resultados']]==[True,True,False]
        assert await conn.fetchval('SELECT preco FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',T,itens[0])==32
        lotes=await conn.fetch("SELECT acao,depois FROM gestao_alteracoes WHERE tenant_id=$1 AND entidade='catalogo_lote'",T)
        assert [l['acao'] for l in lotes]==['reajustar']
        # Mover: outra unidade não; pausar e arquivar em lote.
        entradas=await categoria(conn,'patio_guedala','Entradas quentes')
        r=await client.post(f'{CAT}/lote',json={'itens':[ids['GUE-0002'],ids['MOR-0001']],'acao':'mover','categoria_id':entradas},headers=ADMIN)
        assert [(x['ok'],x.get('motivo')) for x in r.json()['resultados']]==[(True,None),(False,'unidade_diferente')]
        assert await conn.fetchval('SELECT categoria FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',T,ids['GUE-0002'])=='Entradas quentes'
        r=await client.post(f'{CAT}/lote',json={'itens':itens[:2],'acao':'pausar'},headers=ADMIN);assert all(x['ok'] for x in r.json()['resultados'])
        assert await conn.fetchval('SELECT count(*) FROM catalogo_itens WHERE tenant_id=$1 AND id=ANY($2::int[]) AND NOT ativo',T,itens[:2])==2
        for ruim in ({'itens':itens,'acao':'apagar'},{'itens':[],'acao':'pausar'},{'itens':itens,'acao':'reajustar'},{'itens':itens,'acao':'reajustar','percentual':10,'valor':1},
                     {'itens':[1]*2,'acao':'pausar'},{'itens':itens,'acao':'pausar','percentual':1}):
            assert (await client.post(f'{CAT}/lote',json=ruim,headers=ADMIN)).status_code==400,ruim
    cenario_cat3(monkeypatch,corpo)
