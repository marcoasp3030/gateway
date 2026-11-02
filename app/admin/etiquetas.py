"""Etiquetas da empresa. Criar, renomear e apagar: a partir de supervisor. Aplicar na conversa: qualquer papel que atenda."""
from typing import Literal
import asyncpg
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,field_validator
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from .servico import auditar

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
B='/tenants/{tenant}/etiquetas'
MAXIMO=50
POR_CONVERSA=10
Cor=Literal['roxo','dourado','verde','vermelho','cinza']


def _nome(v):
    v=' '.join(v.split())
    if not 1<=len(v)<=30: raise ValueError('nome inválido')
    return v


class Nova(BaseModel):
    model_config=ConfigDict(extra='forbid')
    nome:str=Field(max_length=60)
    cor:Cor='roxo'
    _n=field_validator('nome')(_nome)


class Alteracao(BaseModel):
    model_config=ConfigDict(extra='forbid')
    nome:str|None=Field(default=None,max_length=60)
    cor:Cor|None=None
    @field_validator('nome')
    @classmethod
    def _n(cls,v): return None if v is None else _nome(v)


class Aplicacao(BaseModel):
    model_config=ConfigDict(extra='forbid')
    ids:list[int]=Field(max_length=POR_CONVERSA)


def _linha(r): return {'id':r['id'],'nome':r['nome'],'cor':r['cor']}


async def _empresa(request,tenant):
    if not await Repositorio(request.app.state.db).tenant(tenant): raise HTTPException(404,'TENANT_NOT_FOUND')


async def das_conversas(db,tenant,ids):
    """{conversa: [etiquetas]} para a lista do painel, em ordem de nome."""
    saida={}
    for r in await db.fetch('''SELECT ce.conversation_id,e.id,e.nome,e.cor FROM conversa_etiquetas ce JOIN etiquetas e ON e.id=ce.etiqueta_id
            WHERE ce.tenant_id=$1 AND ce.conversation_id=ANY($2::text[]) ORDER BY lower(e.nome)''',tenant,ids):
        saida.setdefault(r['conversation_id'],[]).append(_linha(r))
    return saida


@router.get(B)
async def listar(request:Request,tenant:str):
    await _empresa(request,tenant)
    return {'itens':[_linha(r) for r in await request.app.state.db.fetch('SELECT * FROM etiquetas WHERE tenant_id=$1 ORDER BY lower(nome)',tenant)]}


@router.post(B,status_code=201)
async def criar(request:Request,tenant:str,body:Nova,actor=Depends(painel)):
    await _empresa(request,tenant)
    async with request.app.state.db.acquire() as c,c.transaction():
        if await c.fetchval('SELECT count(*) FROM etiquetas WHERE tenant_id=$1',tenant)>=MAXIMO: raise HTTPException(409,{'campo':'limite'})
        try: r=await c.fetchrow('INSERT INTO etiquetas(tenant_id,nome,cor,criado_por) VALUES ($1,$2,$3,$4) RETURNING *',tenant,body.nome,body.cor,actor)
        except asyncpg.UniqueViolationError: raise HTTPException(409,{'campo':'nome'}) from None
        await auditar(c,tenant,actor,'etiqueta.criada',str(r['id']))
    return _linha(r)


@router.patch(B+'/{eid}')
async def alterar(request:Request,tenant:str,eid:int,body:Alteracao,actor=Depends(painel)):
    await _empresa(request,tenant)
    async with request.app.state.db.acquire() as c,c.transaction():
        try: r=await c.fetchrow('UPDATE etiquetas SET nome=COALESCE($3,nome),cor=COALESCE($4,cor) WHERE tenant_id=$1 AND id=$2 RETURNING *',tenant,eid,body.nome,body.cor)
        except asyncpg.UniqueViolationError: raise HTTPException(409,{'campo':'nome'}) from None
        if not r: raise HTTPException(404,'NOT_FOUND')
        await auditar(c,tenant,actor,'etiqueta.alterada',str(eid))
    return _linha(r)


@router.delete(B+'/{eid}')
async def apagar(request:Request,tenant:str,eid:int,actor=Depends(painel)):
    await _empresa(request,tenant)
    async with request.app.state.db.acquire() as c,c.transaction():
        if not await c.fetchval('DELETE FROM etiquetas WHERE tenant_id=$1 AND id=$2 RETURNING id',tenant,eid): raise HTTPException(404,'NOT_FOUND')
        await auditar(c,tenant,actor,'etiqueta.apagada',str(eid))
    return {'ok':True}


@router.put('/tenants/{tenant}/conversas/{conv}/etiquetas')
async def aplicar(request:Request,tenant:str,conv:str,body:Aplicacao,actor=Depends(painel)):
    """Troca o conjunto inteiro da conversa: duas pessoas mexendo ao mesmo tempo ficam com a última escolha."""
    conversa=await Repositorio(request.app.state.db).obter(tenant,'conversas',conv)
    if not conversa: raise HTTPException(404,'NOT_FOUND')
    ids=sorted(set(body.ids))
    async with request.app.state.db.acquire() as c,c.transaction():
        await c.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',conv)
        validas=await c.fetchval('SELECT count(*) FROM etiquetas WHERE tenant_id=$1 AND id=ANY($2::bigint[])',tenant,ids)
        if validas!=len(ids): raise HTTPException(400,{'campo':'ids'})
        await c.execute('DELETE FROM conversa_etiquetas WHERE tenant_id=$1 AND conversation_id=$2 AND NOT etiqueta_id=ANY($3::bigint[])',tenant,conv,ids)
        await c.execute('''INSERT INTO conversa_etiquetas(tenant_id,conversation_id,etiqueta_id,aplicada_por) SELECT $1,$2,x,$4 FROM unnest($3::bigint[]) x
            ON CONFLICT DO NOTHING''',tenant,conv,ids,actor)
        await auditar(c,tenant,actor,'conversa.etiquetas',conv,{'ids':ids})
        return {'itens':(await das_conversas(c,tenant,[conv])).get(conv,[])}
