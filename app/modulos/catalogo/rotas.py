import os
from decimal import Decimal,InvalidOperation
from typing import Any
from fastapi import APIRouter,Depends,Request,HTTPException,Query
from fastapi.encoders import jsonable_encoder
from pydantic import AwareDatetime,BaseModel,ConfigDict
from starlette.responses import JSONResponse
from nucleo.autenticacao import contexto_da_chave,painel,exigir_admin
from nucleo.banco import Repositorio
from admin.servico import auditar
from modulos import habilitado
from . import busca as mod_busca
from . import disponibilidade as disp
from .importacao import importar,ArquivoInvalido,MAX_BYTES,TIPOS
router=APIRouter()


def publico(r):
    """Item como o agente vê: preço nulo quando não há preço cadastrado (nunca zero)."""
    return {'item_id':r['id'],'codigo':r['codigo'],'unidade':r['unidade'],'categoria':r['categoria'],'nome':r['nome'],
            'descricao':r['descricao'],'preco':float(r['preco']) if r['preco'] is not None else None,
            'alcoolico':r['alcoolico'],'delivery':r['delivery'],'tipo':r['tipo'],'observacao':r['observacao']}


# Itens por página quando a busca é por categoria (PED2).
POR_PAGINA=10
# Categorias por página (DISP1): 9, para sobrar na lista do WhatsApp (10 linhas) a opção "Ver mais categorias".
CATEGORIAS_POR_PAGINA=9
# adicionar_item só aceita item que apareceu numa busca desta conversa nos últimos N minutos (DISP1).
ITEM_BUSCADO_MIN=max(1,int(os.getenv('ITEM_BUSCADO_MIN','30') or 30))
COLUNAS_DO_ITEM='id,codigo,unidade,categoria,nome,descricao,preco,alcoolico,delivery,tipo,observacao,disponibilidade'
MAX_SUGESTOES=5


def vendavel(r):
    """O que pode entrar em pedido: ativo, com preço e que não seja só do salão (mesma regra do carrinho)."""
    return r['preco'] is not None and r['delivery']!='nao' and r['tipo'] in ('item','adicional')


def contar_categorias(rows):
    """[{categoria, qtd_itens}] dos itens que podem ser pedidos, na ordem do cardápio importado
    (a categoria aparece na posição do seu primeiro item no arquivo)."""
    contagem={};primeiro={}
    for r in rows:
        if vendavel(r):
            contagem[r['categoria']]=contagem.get(r['categoria'],0)+1
            primeiro[r['categoria']]=min(primeiro.get(r['categoria'],r['id']),r['id'])
    return [{'categoria':c,'qtd_itens':contagem[c]} for c in sorted(contagem,key=lambda c:(primeiro[c],mod_busca.normalizar(c)))]


async def marcar_vistos(db,tenant,conversa,ids):
    """Guarda que estes itens apareceram numa busca da conversa. Falha aqui não derruba a busca."""
    if not ids: return
    try:
        await db.execute('''INSERT INTO catalogo_vistos(tenant_id,conversation_id,item_id) SELECT $1,$2,unnest($3::bigint[])
            ON CONFLICT (tenant_id,conversation_id,item_id) DO UPDATE SET visto_em=now()''',tenant,conversa,list(ids))
    except Exception:
        import logging;logging.getLogger('gateway.catalogo').warning('Itens da busca não marcados como vistos')


async def com_disponibilidade(db,tenant,conversa,itens):
    """Itens como o agente vê, com `disponivel`, o motivo e a disponibilidade em palavras; os indisponíveis vão para o fim.
    Devolve (itens, data de referência usada na conferência)."""
    conf=await disp.da_conversa(db,tenant,conversa);saida=[];referencia=None
    for r in itens:
        a=await conf.item(r);referencia=referencia or a['data_referencia']
        saida.append({**publico(r),'disponivel':a['disponivel'],'indisponivel_motivo':a['motivo'],'disponibilidade':a['disponibilidade']})
    saida.sort(key=lambda i: not i['disponivel'])
    return saida,referencia


def da_categoria(rows,categoria):
    """(itens vendáveis da categoria pedida, nomes das categorias achadas). Compara sem acento e sem maiúsculas;
    sem nome igual, vale o que contém o texto ("temaki" acha "Temakis — cone" e "Temakis — hand roll")."""
    alvo=mod_busca.normalizar(categoria);bons=[r for r in rows if vendavel(r)]
    nomes={r['categoria']:mod_busca.normalizar(r['categoria']) for r in bons}
    achadas=[c for c,n in nomes.items() if n==alvo] or ([c for c,n in nomes.items() if alvo and (alvo in n or n in alvo)])
    return [r for r in bons if r['categoria'] in achadas],sorted(achadas,key=mod_busca.normalizar)


@router.get('/api/v1/catalogo/busca')
async def busca(request:Request,conversation_id:str,q:str=Query('',max_length=200),unidade:str|None=Query(None,max_length=40),
                categorias:bool=False,categoria:str|None=Query(None,max_length=120),pagina:int|None=Query(None,ge=1,le=1000),
                ctx=Depends(contexto_da_chave('ferramentas:catalogo'))):
    """Sem parâmetros novos, a busca de sempre. `categorias=true` lista as categorias da unidade com a quantidade de
    itens; `categoria=<nome>` devolve os itens daquela categoria, 10 por página, só os que têm preço (PED2)."""
    repo=Repositorio(request.app.state.db);t=ctx['tenant_id']
    await habilitado(repo,t,'catalogo')
    conv=await repo.obter(t,'conversas',conversation_id)
    if not conv or (ctx['agente_id'] and conv['agente_id']!=ctx['agente_id']): raise HTTPException(404,'NOT_FOUND')
    cfg=await request.app.state.configuracoes.efetiva(t,conv['agente_id'])
    # Módulo que vale para o agente da conversa: permitido no modelo, ligado na empresa e no agente (MULTI1-a).
    if 'catalogo' not in cfg['modulos']: raise HTTPException(404,'NOT_FOUND')
    por_categoria=categorias or bool((categoria or '').strip())
    if por_categoria and not unidade:
        # Sem unidade na chamada, vale a do carrinho aberto da conversa; sem carrinho, o agente precisa dizer qual.
        unidade=await repo.db.fetchval("SELECT unidade FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2 AND status='aberto'",t,conversation_id)
        if not unidade: raise HTTPException(400,{'campo':'unidade'})
    # Só itens ativos. A ordenação por relevância é feita aqui (sem acento e tolerante), não no banco.
    rows=[dict(r) for r in await repo.db.fetch(f'''SELECT {COLUNAS_DO_ITEM}
        FROM catalogo_itens WHERE tenant_id=$1 AND ativo AND ($2::text IS NULL OR unidade=$2) ORDER BY id LIMIT 5000''',t,unidade)]
    # Para o registro das Skills (DISP1): só o que não é dado de cliente. O texto buscado (q) fica de fora.
    ref={'unidade':unidade,'categorias':bool(categorias),'categoria':(categoria or '').strip()[:120] or None,'pagina':pagina};request.state.skill_ref=ref
    async def responder(achados,**campos):
        itens,referencia=await com_disponibilidade(repo.db,t,conversation_id,achados)
        await marcar_vistos(repo.db,t,conversation_id,[i['item_id'] for i in itens])
        ref['qtd_resultados']=len(itens)
        return {**campos,'data_referencia':referencia,'itens':itens}
    if not por_categoria: return await responder(mod_busca.buscar(rows,q))
    if not rows and not await repo.db.fetchval('SELECT EXISTS(SELECT 1 FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2 AND ativo)',t,unidade):
        raise HTTPException(404,'NOT_FOUND')
    lista=contar_categorias(rows)
    if categorias:
        # Sem `pagina`, todas (como sempre); com `pagina`, 9 por vez e `tem_mais` (DISP1).
        inicio=(pagina-1)*CATEGORIAS_POR_PAGINA if pagina else 0
        parte=lista[inicio:inicio+CATEGORIAS_POR_PAGINA] if pagina else lista
        ref['qtd_resultados']=len(parte)
        return {'unidade':unidade,'total_itens':sum(c['qtd_itens'] for c in lista),'tem_cardapio':bool(lista),'total_categorias':len(lista),
                'pagina':pagina,'por_pagina':CATEGORIAS_POR_PAGINA if pagina else None,
                'tem_mais':bool(pagina) and inicio+CATEGORIAS_POR_PAGINA<len(lista),'categorias':parte}
    pagina=pagina or 1
    achados,nomes=da_categoria(rows,categoria)
    if q.strip(): achados=mod_busca.buscar(achados,q,limite=5000)
    else: achados.sort(key=lambda r:(mod_busca.normalizar(r['categoria']),mod_busca.normalizar(r['nome']),r['id']))
    # Os indisponíveis vão para o fim ANTES de paginar: a primeira página traz o que pode ser pedido.
    conf=await disp.da_conversa(repo.db,t,conversation_id)
    marcados=[(r,(await conf.item(r))['disponivel']) for r in achados]
    achados=[r for r,ok in marcados if ok]+[r for r,ok in marcados if not ok]
    inicio=(pagina-1)*POR_PAGINA;parte=achados[inicio:inicio+POR_PAGINA]
    extras={'sugestoes':[]} if not nomes else {}
    if not nomes:
        # Categoria que não existe: as parecidas, para o agente oferecer (ou chamar com categorias=true).
        import difflib
        por_nome={mod_busca.normalizar(c['categoria']):c['categoria'] for c in lista}
        extras['sugestoes']=[por_nome[n] for n in difflib.get_close_matches(mod_busca.normalizar(categoria),list(por_nome),MAX_SUGESTOES,0.5)]
    return await responder(parte,unidade=unidade,categoria=categoria.strip(),categorias_encontradas=nomes,pagina=pagina,por_pagina=POR_PAGINA,
                           total=len(achados),tem_mais=inicio+POR_PAGINA<len(achados),**extras)


admin=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])


async def _empresa(db,tenant):
    if not await Repositorio(db).tenant(tenant): raise HTTPException(404,'NOT_FOUND')


@admin.post('/tenants/{tenant_id}/catalogo/importar')
async def importar_csv(request:Request,tenant_id:str,simulacao:bool=True,actor=Depends(painel)):
    """Corpo: o arquivo CSV (UTF-8, com ou sem BOM). simulacao=true (padrão) só relata; nada é gravado.
    Com erro em qualquer linha, nada é gravado mesmo com simulacao=false."""
    await _empresa(request.app.state.db,tenant_id)
    corpo=b''
    async for parte in request.stream():
        corpo+=parte
        if len(corpo)>MAX_BYTES: raise HTTPException(413,'Arquivo maior que 2 MB')
    async with request.app.state.db.acquire() as conn,conn.transaction():
        # Uma importação por vez na empresa.
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','catalogo:'+tenant_id)
        try: relatorio=await importar(conn,tenant_id,corpo,simulacao)
        except ArquivoInvalido as erro: raise HTTPException(400,{'campo':'arquivo','motivo':str(erro)}) from None
        if relatorio['aplicado']: await auditar(conn,tenant_id,actor,'catalogo.importado',tenant_id)
    return relatorio


@admin.get('/tenants/{tenant_id}/catalogo/itens')
async def itens(request:Request,tenant_id:str,unidade:str|None=None,tipo:str|None=None,ativo:bool|None=None,
                q:str=Query('',max_length=200),limite:int=50,cursor:int=0):
    await _empresa(request.app.state.db,tenant_id)
    if not 1<=limite<=200 or cursor<0 or (tipo and tipo not in TIPOS): raise HTTPException(400,'VALIDATION_ERROR')
    rows=await request.app.state.db.fetch('''SELECT id,codigo,unidade,categoria,nome,descricao,preco,alcoolico,delivery,tipo,observacao,ativo,atualizado_em,disponibilidade
        FROM catalogo_itens WHERE tenant_id=$1 AND ($2::text IS NULL OR unidade=$2) AND ($3::text IS NULL OR tipo=$3)
        AND ($4::boolean IS NULL OR ativo=$4) AND id>$5 ORDER BY id LIMIT 5000''',tenant_id,unidade,tipo,ativo,cursor)
    achados=[dict(r) for r in rows]
    if q.strip(): achados=mod_busca.buscar(achados,q,limite=5000);achados.sort(key=lambda r:r['id'])
    pagina=achados[:limite]
    return {'itens':await com_agora(request.app.state.db,tenant_id,pagina),
            'proximo_cursor':pagina[-1]['id'] if len(achados)>limite else None}


def do_painel(r):
    """Item como o painel vê: o que o agente vê + ativo, data e a disponibilidade cadastrada (com o resumo em palavras)."""
    return {**publico(r),'ativo':r['ativo'],'atualizado_em':r['atualizado_em'],'disponibilidade':disp.publica(disp.ler(r['disponibilidade']))}


COLUNAS_DO_PAINEL='id,codigo,unidade,categoria,nome,descricao,preco,alcoolico,delivery,tipo,observacao,ativo,atualizado_em,disponibilidade'
MAX_NOME=120
MAX_DESCRICAO=300
MAX_PRECO=Decimal('99999.99')


async def com_agora(db,tenant,rows):
    """Itens como o painel vê, com a situação AGORA (CAT2): `disponivel_agora`, `indisponivel_motivo` e `data_referencia`.
    Mesma conferência da Skill (dia, feriado da unidade e horário). Item que não pode ser vendido (inativo, sem preço,
    só do salão) sai como não disponível, com o motivo que o adicionar_item daria."""
    from modulos.pedidos import agendamento
    from modulos.pedidos.servico import vendavel as motivo_de_venda
    conf=disp.Conferencia(db,tenant,agendamento.agora_utc(),await db.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant));saida=[]
    for r in rows:
        a=await conf.item(r);motivo=motivo_de_venda(r) or a['motivo']
        saida.append({**do_painel(r),'disponivel_agora':motivo is None,'indisponivel_motivo':motivo,'data_referencia':a['data_referencia']})
    return saida


class ItemDoCatalogo(BaseModel):
    """Edição de um item pelo painel (CAT2). Todos os campos são opcionais e independentes: o que não vem fica como está.
    `atualizado_em` (o valor lido pelo painel) liga a conferência de edição concorrente."""
    model_config=ConfigDict(extra='forbid')
    nome:Any=None
    descricao:Any=None
    preco:Any=None
    ativo:Any=None
    categoria:Any=None
    disponibilidade:Any=None
    atualizado_em:AwareDatetime|None=None


def _campo(nome):
    return HTTPException(400,{'campo':nome})


def _preco_do_painel(valor):
    """Nulo = sem preço. Número maior que zero, com até 2 casas decimais."""
    if valor is None: return None
    if isinstance(valor,bool) or not isinstance(valor,(int,float,str)): raise _campo('preco')
    try: d=Decimal(str(valor).strip().replace(',','.'))
    except InvalidOperation: raise _campo('preco') from None
    if not d.is_finite() or d<=0 or d>MAX_PRECO or d!=d.quantize(Decimal('0.01')): raise _campo('preco')
    return d.quantize(Decimal('0.01'))


async def _validar_edicao(conn,tenant,atual,dados):
    """{coluna: valor novo} só do que veio no corpo. 400 com `detalhes.campo` no primeiro campo que não serve."""
    enviados=dados.model_fields_set-{'atualizado_em'};novos={}
    if not enviados: raise _campo('corpo')
    if 'nome' in enviados:
        if not isinstance(dados.nome,str) or not dados.nome.strip() or len(dados.nome.strip())>MAX_NOME: raise _campo('nome')
        novos['nome']=dados.nome.strip()
    if 'descricao' in enviados:
        if dados.descricao is not None and (not isinstance(dados.descricao,str) or len(dados.descricao.strip())>MAX_DESCRICAO): raise _campo('descricao')
        # Só espaços vale como vazio: apaga.
        novos['descricao']=(dados.descricao or '').strip() or None
    if 'preco' in enviados: novos['preco']=_preco_do_painel(dados.preco)
    if 'ativo' in enviados:
        if not isinstance(dados.ativo,bool): raise _campo('ativo')
        novos['ativo']=dados.ativo
    if 'categoria' in enviados:
        if not isinstance(dados.categoria,str) or not dados.categoria.strip(): raise _campo('categoria')
        # Só categoria que já existe na mesma unidade (sem acento e sem maiúsculas); grava com a grafia que já existe.
        existentes={mod_busca.normalizar(r['categoria']):r['categoria'] for r in await conn.fetch(
            'SELECT DISTINCT categoria FROM catalogo_itens WHERE tenant_id=$1 AND unidade=$2',tenant,atual['unidade'])}
        achada=existentes.get(mod_busca.normalizar(dados.categoria))
        if not achada: raise _campo('categoria')
        novos['categoria']=achada
    if 'disponibilidade' in enviados:
        if dados.disponibilidade is not None and not isinstance(dados.disponibilidade,dict): raise _campo('disponibilidade')
        try: novos['disponibilidade']=disp.validar(dados.disponibilidade)
        except ValueError: raise _campo('disponibilidade') from None
    return novos


def _para_o_historico(coluna,valor):
    if coluna=='preco': return float(valor) if valor is not None else None
    if coluna=='disponibilidade': return disp.ler(valor)
    return valor


@admin.get('/tenants/{tenant_id}/catalogo/itens/{item_id}')
async def item_do_catalogo(request:Request,tenant_id:str,item_id:int):
    """Um item, com a situação agora. Qualquer papel lê."""
    await _empresa(request.app.state.db,tenant_id)
    row=await request.app.state.db.fetchrow(f'SELECT {COLUNAS_DO_PAINEL} FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant_id,item_id)
    if row is None: raise HTTPException(404,'NOT_FOUND')
    return (await com_agora(request.app.state.db,tenant_id,[row]))[0]


@admin.patch('/tenants/{tenant_id}/catalogo/itens/{item_id}',dependencies=[Depends(exigir_admin)])
async def editar_item(request:Request,tenant_id:str,item_id:int,dados:ItemDoCatalogo,actor=Depends(painel)):
    """Edita nome, descrição, preço, ativo, categoria e disponibilidade (CAT2). Só admin ou plataforma. Unidade e id não mudam.
    Com `atualizado_em`, item que mudou desde a leitura do painel devolve 409 com o item atual. Carrinhos abertos não são tocados:
    o fechar_pedido já confere preço e item ativo."""
    import json
    from admin.alteracoes import registrar
    db=request.app.state.db
    await _empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        atual=await conn.fetchrow(f'SELECT {COLUNAS_DO_PAINEL} FROM catalogo_itens WHERE tenant_id=$1 AND id=$2 FOR UPDATE',tenant_id,item_id)
        if atual is None: raise HTTPException(404,'NOT_FOUND')
        if dados.atualizado_em is not None and dados.atualizado_em!=atual['atualizado_em']:
            corpo={'erro':{'codigo':'CONFLICT','mensagem':'O item foi alterado por outra pessoa. Confira os dados atuais e salve de novo.',
                           'detalhes':{'campo':'atualizado_em','motivo':'item_alterado'}},'item':(await com_agora(conn,tenant_id,[atual]))[0]}
            return JSONResponse(jsonable_encoder(corpo),status_code=409,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        novos=await _validar_edicao(conn,tenant_id,atual,dados)
        mudou={c:v for c,v in novos.items() if _para_o_historico(c,v)!=_para_o_historico(c,atual[c])}
        row=atual
        if mudou:
            colunas=list(mudou)
            valores=[json.dumps(mudou[c]) if c=='disponibilidade' and mudou[c] is not None else mudou[c] for c in colunas]
            atribuicoes=','.join(f'{c}=${n}'+('::jsonb' if c=='disponibilidade' else '') for n,c in enumerate(colunas,3))
            row=await conn.fetchrow(f'UPDATE catalogo_itens SET {atribuicoes},atualizado_em=clock_timestamp() WHERE tenant_id=$1 AND id=$2 RETURNING {COLUNAS_DO_PAINEL}',
                                    tenant_id,item_id,*valores)
            # Histórico com antes e depois só do que mudou, e quem mudou (X-Actor).
            await registrar(conn,tenant_id,actor,'catalogo_item',item_id,'alterado',{c:_para_o_historico(c,atual[c]) for c in colunas},
                            {c:_para_o_historico(c,mudou[c]) for c in colunas})
            await auditar(conn,tenant_id,actor,'catalogo.item_alterado',str(item_id))
        return (await com_agora(conn,tenant_id,[row]))[0]


@admin.get('/tenants/{tenant_id}/catalogo/categorias')
async def categorias_da_unidade(request:Request,tenant_id:str,unidade:str=Query(...,max_length=40)):
    """Categorias de uma unidade na ordem do cardápio importado, com as contagens. Só leitura; qualquer papel."""
    db=request.app.state.db
    await _empresa(db,tenant_id)
    rows=await db.fetch("""SELECT categoria,min(id) AS primeiro,count(*) AS qtd_itens,count(*) FILTER (WHERE ativo) AS itens_ativos,
        count(*) FILTER (WHERE ativo AND preco IS NOT NULL AND delivery<>'nao' AND tipo IN ('item','adicional')) AS itens_no_cardapio
        FROM catalogo_itens WHERE tenant_id=$1 AND unidade=$2 GROUP BY 1 ORDER BY 2""",tenant_id,unidade)
    if not rows and not await db.fetchval('SELECT EXISTS(SELECT 1 FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2)',tenant_id,unidade):
        raise HTTPException(404,'NOT_FOUND')
    return {'unidade':unidade,'categorias':[{'categoria':r['categoria'],'ordem':n,'qtd_itens':r['qtd_itens'],'itens_ativos':r['itens_ativos'],
                                             'itens_no_cardapio':r['itens_no_cardapio']} for n,r in enumerate(rows,1)]}


class PreviaDeDisponibilidade(BaseModel):
    model_config=ConfigDict(extra='forbid')
    unidade:str
    disponibilidade:Any=None


@admin.post('/tenants/{tenant_id}/catalogo/disponibilidade/previa')
async def previa_da_disponibilidade(request:Request,tenant_id:str,dados:PreviaDeDisponibilidade):
    """Como uma regra de disponibilidade ficaria, sem gravar: o resumo em palavras e se valeria agora naquela unidade
    (com o calendário e os feriados dela). Qualquer papel pode usar."""
    from modulos.pedidos import agendamento
    db=request.app.state.db
    await _empresa(db,tenant_id)
    if dados.disponibilidade is not None and not isinstance(dados.disponibilidade,dict): raise _campo('disponibilidade')
    try: regra=disp.validar(dados.disponibilidade)
    except ValueError: raise _campo('disponibilidade') from None
    if not await db.fetchval("""SELECT EXISTS(SELECT 1 FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2)
            OR EXISTS(SELECT 1 FROM catalogo_itens WHERE tenant_id=$1 AND unidade=$2)""",tenant_id,dados.unidade): raise HTTPException(404,'NOT_FOUND')
    conf=disp.Conferencia(db,tenant_id,agendamento.agora_utc(),await db.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant_id))
    a=await conf.item({'unidade':dados.unidade,'disponibilidade':regra})
    return {'unidade':dados.unidade,'disponibilidade':disp.publica(regra),'resumo':disp.resumo(regra),
            'disponivel_agora':a['disponivel'],'indisponivel_motivo':a['motivo'],'data_referencia':a['data_referencia']}


@admin.get('/tenants/{tenant_id}/catalogo/resumo')
async def resumo(request:Request,tenant_id:str):
    """Contagens por unidade e tipo: total, ativos, com preço e vendáveis (ativo, com preço, delivery != nao)."""
    await _empresa(request.app.state.db,tenant_id)
    rows=await request.app.state.db.fetch('''SELECT unidade,tipo,count(*) total,count(*) FILTER (WHERE ativo) ativos,
        count(*) FILTER (WHERE preco IS NOT NULL) com_preco,
        count(*) FILTER (WHERE ativo AND preco IS NOT NULL AND delivery<>'nao') vendaveis,
        count(*) FILTER (WHERE alcoolico) alcoolicos
        FROM catalogo_itens WHERE tenant_id=$1 GROUP BY 1,2 ORDER BY 1,2''',tenant_id)
    return {'itens':[dict(r) for r in rows]}
