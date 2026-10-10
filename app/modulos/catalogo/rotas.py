from fastapi import APIRouter,Depends,Request,HTTPException,Query
from nucleo.autenticacao import contexto_da_chave,painel
from nucleo.banco import Repositorio
from admin.servico import auditar
from modulos import habilitado
from . import busca as mod_busca
from .importacao import importar,ArquivoInvalido,MAX_BYTES,TIPOS
router=APIRouter()


def publico(r):
    """Item como o agente vê: preço nulo quando não há preço cadastrado (nunca zero)."""
    return {'item_id':r['id'],'codigo':r['codigo'],'unidade':r['unidade'],'categoria':r['categoria'],'nome':r['nome'],
            'descricao':r['descricao'],'preco':float(r['preco']) if r['preco'] is not None else None,
            'alcoolico':r['alcoolico'],'delivery':r['delivery'],'tipo':r['tipo'],'observacao':r['observacao']}


@router.get('/api/v1/catalogo/busca')
async def busca(request:Request,conversation_id:str,q:str=Query('',max_length=200),unidade:str|None=Query(None,max_length=40),
                ctx=Depends(contexto_da_chave('ferramentas:catalogo'))):
    repo=Repositorio(request.app.state.db);t=ctx['tenant_id']
    await habilitado(repo,t,'catalogo')
    conv=await repo.obter(t,'conversas',conversation_id)
    if not conv or (ctx['agente_id'] and conv['agente_id']!=ctx['agente_id']): raise HTTPException(404,'NOT_FOUND')
    cfg=await request.app.state.configuracoes.efetiva(t,conv['agente_id'])
    modelo=await repo.modelo(t,cfg['modelo_id'],cfg['modelo_versao'])
    if 'catalogo' not in modelo['modulos']: raise HTTPException(404,'NOT_FOUND')
    # Só itens ativos. A ordenação por relevância é feita aqui (sem acento e tolerante), não no banco.
    rows=await repo.db.fetch('''SELECT id,codigo,unidade,categoria,nome,descricao,preco,alcoolico,delivery,tipo,observacao
        FROM catalogo_itens WHERE tenant_id=$1 AND ativo AND ($2::text IS NULL OR unidade=$2) ORDER BY id LIMIT 5000''',t,unidade)
    return {'itens':[publico(r) for r in mod_busca.buscar([dict(r) for r in rows],q)]}


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
    rows=await request.app.state.db.fetch('''SELECT id,codigo,unidade,categoria,nome,descricao,preco,alcoolico,delivery,tipo,observacao,ativo,atualizado_em
        FROM catalogo_itens WHERE tenant_id=$1 AND ($2::text IS NULL OR unidade=$2) AND ($3::text IS NULL OR tipo=$3)
        AND ($4::boolean IS NULL OR ativo=$4) AND id>$5 ORDER BY id LIMIT 5000''',tenant_id,unidade,tipo,ativo,cursor)
    achados=[dict(r) for r in rows]
    if q.strip(): achados=mod_busca.buscar(achados,q,limite=5000);achados.sort(key=lambda r:r['id'])
    pagina=achados[:limite]
    return {'itens':[{**publico(r),'ativo':r['ativo'],'atualizado_em':r['atualizado_em']} for r in pagina],
            'proximo_cursor':pagina[-1]['id'] if len(achados)>limite else None}


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
