"""Notas internas da conversa. Só a equipe vê: ficam fora de `mensagens`, então não vão ao cliente nem ao histórico do agente.
Não pausam o agente nem mudam a atividade da conversa. Apaga quem escreveu ou, a partir de gestor, a equipe.
A auditoria guarda só o id; a anonimização do contato apaga as notas (texto livre pode ter dado pessoal)."""
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,field_validator
from nucleo.autenticacao import painel,pode
from nucleo.banco import Repositorio
from .servico import auditar

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
B='/tenants/{tenant}/conversas/{conv}/notas'
LIMITE=200


class Nota(BaseModel):
    model_config=ConfigDict(extra='forbid')
    texto:str=Field(max_length=2100)
    @field_validator('texto')
    @classmethod
    def _texto(cls,v):
        v=v.strip()
        if not 1<=len(v)<=2000: raise ValueError('texto inválido')
        return v


def _linha(r,actor,request):
    return {'id':r['id'],'texto':r['texto'],'autor':r['autor'],'atendimento_id':r['atendimento_id'],'criado_em':r['criado_em'].isoformat(),
            'minha':r['autor']==actor,'apagavel':r['autor']==actor or pode(request,'gestor')}


async def _conversa(request,tenant,conv):
    c=await Repositorio(request.app.state.db).obter(tenant,'conversas',conv)
    if not c: raise HTTPException(404,'NOT_FOUND')
    return c


@router.get(B)
async def listar(request:Request,tenant:str,conv:str,atendimento:str|None=None,actor=Depends(painel)):
    await _conversa(request,tenant,conv)
    rows=await request.app.state.db.fetch('''SELECT * FROM (SELECT * FROM conversa_notas WHERE tenant_id=$1 AND conversation_id=$2
        AND ($3::text IS NULL OR atendimento_id=$3) ORDER BY id DESC LIMIT $4) n ORDER BY id''',tenant,conv,atendimento,LIMITE)
    return {'itens':[_linha(r,actor,request) for r in rows]}


@router.post(B,status_code=201)
async def criar(request:Request,tenant:str,conv:str,body:Nota,actor=Depends(painel)):
    c=await _conversa(request,tenant,conv)
    async with request.app.state.db.acquire() as conn,conn.transaction():
        r=await conn.fetchrow('''INSERT INTO conversa_notas(tenant_id,conversation_id,atendimento_id,autor,texto) VALUES ($1,$2,$3,$4,$5) RETURNING *''',
                              tenant,conv,c['atendimento_id'],actor,body.texto)
        await auditar(conn,tenant,actor,'conversa.nota_criada',conv,{'nota':r['id']})
    return _linha(r,actor,request)


@router.delete(B+'/{nid}')
async def apagar(request:Request,tenant:str,conv:str,nid:int,actor=Depends(painel)):
    async with request.app.state.db.acquire() as conn,conn.transaction():
        r=await conn.fetchrow('SELECT autor FROM conversa_notas WHERE tenant_id=$1 AND conversation_id=$2 AND id=$3 FOR UPDATE',tenant,conv,nid)
        if not r: raise HTTPException(404,'NOT_FOUND')
        if r['autor']!=actor and not pode(request,'gestor'): raise HTTPException(403,'FORBIDDEN')
        await conn.execute('DELETE FROM conversa_notas WHERE tenant_id=$1 AND id=$2',tenant,nid)
        await auditar(conn,tenant,actor,'conversa.nota_apagada',conv,{'nota':nid})
    return {'ok':True}
