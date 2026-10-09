import json
from fastapi import APIRouter, Depends, HTTPException, Request
from nucleo.autenticacao import painel, contexto_da_chave
from nucleo.banco import Repositorio, registro
from .servico import ENTIDADES, limpar, auditar

router = APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
api = APIRouter(prefix='/api/v1')


def pagina(itens,limite):
    return {'itens':itens[:limite],'proximo_cursor':str(itens[limite-1]['id']) if len(itens)>limite else None}


@router.get('/tenants')
async def tenants(request:Request,limite:int=50,cursor:str|None=None):
    if not 1<=limite<=100: raise HTTPException(400,'Limite inválido')
    # Catálogo global restrito à chave de serviço do painel.
    rows=await request.app.state.db.fetch('SELECT * FROM tenants WHERE ($1::text IS NULL OR id>$1) ORDER BY id LIMIT $2',cursor,limite+1)
    return pagina([dict(r) for r in rows],limite)


@router.post('/tenants',status_code=201)
async def criar_tenant(request:Request,dados:dict,actor=Depends(painel)):
    return await request.app.state.admin.salvar_tenant(dados,actor)


@router.get('/tenants/{tenant_id}')
async def tenant(request:Request,tenant_id:str):
    row=await Repositorio(request.app.state.db).tenant(tenant_id)
    if not row: raise HTTPException(404,'TENANT_NOT_FOUND')
    return row


@router.patch('/tenants/{tenant_id}')
async def alterar_tenant(request:Request,tenant_id:str,dados:dict,actor=Depends(painel)):
    return await request.app.state.admin.salvar_tenant(dados,actor,tenant_id)


@router.get('/tenants/{tenant_id}/config')
async def config(request:Request,tenant_id:str):
    await tenant(request,tenant_id)
    return await Repositorio(request.app.state.db).obter(tenant_id,'tenant_config',tenant_id)


@router.put('/tenants/{tenant_id}/config')
async def salvar_config(request:Request,tenant_id:str,dados:dict,actor=Depends(painel)):
    return await request.app.state.admin.salvar_config(tenant_id,dados,actor)


@router.get('/tenants/{tenant_id}/auditoria')
async def auditoria(request:Request,tenant_id:str,limite:int=50,cursor:str|None=None):
    await tenant(request,tenant_id)
    return {'itens':await Repositorio(request.app.state.db).listar(tenant_id,'audit_log',limite,cursor)}


@router.get('/tenants/{tenant_id}/{entidade}')
async def listar(request:Request,tenant_id:str,entidade:str,limite:int=50,cursor:str|None=None):
    if entidade not in ENTIDADES: raise HTTPException(404,'NOT_FOUND')
    await tenant(request,tenant_id)
    # Repo limita a 100; buscar limite+1 com teto 100.
    if not 1<=limite<=99: raise HTTPException(400,'Limite deve ser 1–99')
    if entidade=='agentes':
        rows=await request.app.state.db.fetch("""SELECT a.*,
            CASE WHEN c.aberto_ate>now() THEN 'aberto' WHEN c.aberto_ate IS NOT NULL THEN 'meio_aberto' ELSE 'fechado' END AS circuito_estado,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('id',ch.id,'tipo',ch.tipo,'ativo',ch.ativo) ORDER BY ch.id)
                FROM tenant_canais ch WHERE ch.tenant_id=a.tenant_id AND ch.agente_id=a.id),'[]'::jsonb) AS canais
            FROM agentes a LEFT JOIN agente_circuitos c ON c.tenant_id=a.tenant_id AND c.agente_id=a.id
            WHERE a.tenant_id=$1 AND ($2::text IS NULL OR a.id>$2) ORDER BY a.id LIMIT $3""",tenant_id,cursor,limite+1)
        rows=[{**registro(r),'canais':json.loads(r['canais']) if isinstance(r['canais'],str) else r['canais']} for r in rows]
    else:
        rows=await Repositorio(request.app.state.db).listar(tenant_id,ENTIDADES[entidade][0],limite+1,cursor)
    return pagina(limpar(rows),limite)


@router.post('/tenants/{tenant_id}/{entidade}',status_code=201)
async def criar(request:Request,tenant_id:str,entidade:str,dados:dict,actor=Depends(painel)):
    if entidade not in ENTIDADES: raise HTTPException(404,'NOT_FOUND')
    return await request.app.state.admin.salvar(tenant_id,entidade,dados,actor)


@router.get('/tenants/{tenant_id}/{entidade}/{identidade}')
async def obter(request:Request,tenant_id:str,entidade:str,identidade:str):
    if entidade not in ENTIDADES: raise HTTPException(404,'NOT_FOUND')
    row=await Repositorio(request.app.state.db).obter(tenant_id,ENTIDADES[entidade][0],identidade)
    if not row: raise HTTPException(404,'NOT_FOUND')
    result=limpar(row)
    if entidade=='agentes': result['configuracao_efetiva']=await request.app.state.configuracoes.efetiva(tenant_id,identidade)
    return result


@router.patch('/tenants/{tenant_id}/{entidade}/{identidade}')
async def alterar(request:Request,tenant_id:str,entidade:str,identidade:str,dados:dict,actor=Depends(painel)):
    if entidade not in ENTIDADES: raise HTTPException(404,'NOT_FOUND')
    return await request.app.state.admin.salvar(tenant_id,entidade,dados,actor,identidade)


@router.delete('/tenants/{tenant_id}/api-keys/{identidade}')
async def revogar(request:Request,tenant_id:str,identidade:str,actor=Depends(painel)):
    async with request.app.state.db.acquire() as conn, conn.transaction():
        result=await conn.fetchrow('UPDATE tenant_api_keys SET revogada_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING id',tenant_id,identidade)
        if not result: raise HTTPException(404,'NOT_FOUND')
        await auditar(conn,tenant_id,actor,'api-keys.revogada',identidade)
    return {'ok':True}


@api.get('/conversas/{conversation_id}/mensagens')
async def mensagens(request:Request,conversation_id:str,antes:int|None=None,limite:int=100,ctx=Depends(contexto_da_chave('eventos:ler'))):
    repo=Repositorio(request.app.state.db)
    conv=await repo.obter(ctx['tenant_id'],'conversas',conversation_id)
    if not conv or (ctx['agente_id'] and conv['agente_id']!=ctx['agente_id']): raise HTTPException(404,'NOT_FOUND')
    if not 1<=limite<=100: raise HTTPException(400,{'campo':'limite'})
    if antes is not None and not 1<=antes<=9223372036854775807: raise HTTPException(400,{'campo':'antes'})
    rows=await request.app.state.db.fetch('SELECT id,direcao,origem,conteudo,criado_em FROM mensagens '
        'WHERE tenant_id=$1 AND conversation_id=$2 AND ($3::bigint IS NULL OR id<$3) ORDER BY id DESC LIMIT $4',ctx['tenant_id'],conversation_id,antes,limite+1)
    return {'itens':[dict(r) for r in reversed(rows[:limite])],'tem_mais':len(rows)>limite}
