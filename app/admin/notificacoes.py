import json
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import Field
from .modelos import Estrito
from nucleo.autenticacao import contexto_da_chave
from nucleo.banco import Repositorio
from nucleo.pipeline import sanitizar
from .servico import auditar
router=APIRouter()

class Notificacao(Estrito):
    evento:str=Field(min_length=1,max_length=100)
    dados:dict=Field(default_factory=dict)
    instrucao:str=Field(min_length=1,max_length=20000)

@router.post('/api/v1/conversas/{conv}/notificar',status_code=202)
async def notificar(request:Request,conv:str,body:Notificacao,ctx=Depends(contexto_da_chave('notificar'))):
    t=ctx['tenant_id'];repo=Repositorio(request.app.state.db)
    conversa=await repo.obter(t,'conversas',conv)
    if not conversa or (ctx['agente_id'] and conversa['agente_id']!=ctx['agente_id']): raise HTTPException(404,'NOT_FOUND')
    cfg=await request.app.state.configuracoes.efetiva(t,conversa['agente_id'])
    async with request.app.state.db.acquire() as c,c.transaction():
        await c.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',conv)
        msg=await c.fetchval("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,evento) VALUES ($1,$2,'entrada','sistema',$3,$4::jsonb) RETURNING id",t,conv,body.instrucao,json.dumps({'evento':body.evento,'dados':sanitizar(body.dados)}))
        await auditar(c,t,ctx['actor'],'conversa.notificada',conv)
    await request.app.state.pipeline.enfileirar(t,conv,cfg)
    return {'ok':True,'mensagem_id':msg,'status':'enfileirada'}
