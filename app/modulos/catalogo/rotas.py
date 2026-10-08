from fastapi import APIRouter,Depends,Request,HTTPException,Query
from nucleo.autenticacao import contexto_da_chave
from nucleo.banco import Repositorio
from modulos import habilitado
router=APIRouter()

@router.get('/api/v1/catalogo/busca')
async def busca(request:Request,conversation_id:str,q:str=Query('',max_length=200),ctx=Depends(contexto_da_chave('ferramentas:catalogo'))):
    repo=Repositorio(request.app.state.db);t=ctx['tenant_id']
    await habilitado(repo,t,'catalogo')
    conv=await repo.obter(t,'conversas',conversation_id)
    if not conv or (ctx['agente_id'] and conv['agente_id']!=ctx['agente_id']): raise HTTPException(404,'NOT_FOUND')
    cfg=await request.app.state.configuracoes.efetiva(t,conv['agente_id'])
    modelo=await repo.modelo(t,cfg['modelo_id'],cfg['modelo_versao'])
    if 'catalogo' not in modelo['modulos']: raise HTTPException(404,'NOT_FOUND')
    rows=await repo.db.fetch('SELECT id,unidade,categoria,nome,descricao,preco FROM catalogo_itens WHERE tenant_id=$1 AND ativo AND nome ILIKE $2 ORDER BY id LIMIT 50',t,'%'+q+'%')
    return {'itens':[dict(r) for r in rows]}
