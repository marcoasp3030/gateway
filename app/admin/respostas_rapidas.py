"""Respostas rápidas do atendimento humano. Sem dono: da equipe, escrita a partir de supervisor. Com dono: só a pessoa vê e altera,
qualquer que seja o papel dela (nem a plataforma lê a de outra pessoa). A auditoria guarda só o id, nunca o texto."""
import re
from typing import Literal
import asyncpg
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,field_validator
from nucleo.autenticacao import painel,pode
from nucleo.banco import Repositorio
from .servico import auditar

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
B='/tenants/{tenant_id}/respostas-rapidas'
ATALHO=re.compile(r'^[a-z0-9][a-z0-9_-]{0,29}$')
MAXIMO=500


def _atalho(v):
    v=v.strip().lstrip('/').lower()
    if not ATALHO.fullmatch(v): raise ValueError('atalho inválido')
    return v


def _texto(v):
    v=v.strip()
    if not 1<=len(v)<=2000: raise ValueError('texto inválido')
    return v


class Nova(BaseModel):
    model_config=ConfigDict(extra='forbid')
    atalho:str=Field(max_length=31)
    texto:str=Field(max_length=2100)
    escopo:Literal['equipe','pessoal']='pessoal'
    _a=field_validator('atalho')(_atalho)
    _t=field_validator('texto')(_texto)


class Alteracao(BaseModel):
    model_config=ConfigDict(extra='forbid')
    atalho:str|None=Field(default=None,max_length=31)
    texto:str|None=Field(default=None,max_length=2100)
    @field_validator('atalho')
    @classmethod
    def _a(cls,v): return None if v is None else _atalho(v)
    @field_validator('texto')
    @classmethod
    def _t(cls,v): return None if v is None else _texto(v)


def _linha(r,request):
    return {'id':r['id'],'atalho':r['atalho'],'texto':r['texto'],'escopo':'pessoal' if r['dono'] else 'equipe','usos':r['usos'],
            'atualizado_em':r['atualizado_em'].isoformat(),'editavel':bool(r['dono']) or pode(request,'supervisor')}


async def _empresa(request,tenant_id):
    if not await Repositorio(request.app.state.db).tenant(tenant_id): raise HTTPException(404,'TENANT_NOT_FOUND')


async def _visivel(c,tenant_id,rid,actor):
    """Outra empresa ou resposta pessoal de outra pessoa: não existe para quem pergunta."""
    r=await c.fetchrow('SELECT * FROM respostas_rapidas WHERE tenant_id=$1 AND id=$2 AND (dono IS NULL OR dono=$3) FOR UPDATE',tenant_id,rid,actor)
    if not r: raise HTTPException(404,'NOT_FOUND')
    return r


def _pode_escrever(request,dono):
    if not dono and not pode(request,'supervisor'): raise HTTPException(403,'FORBIDDEN')


@router.get(B)
async def listar(request:Request,tenant_id:str,actor=Depends(painel)):
    await _empresa(request,tenant_id)
    rows=await request.app.state.db.fetch('''SELECT * FROM respostas_rapidas WHERE tenant_id=$1 AND (dono IS NULL OR dono=$2)
        ORDER BY usos DESC,atalho,id LIMIT $3''',tenant_id,actor,MAXIMO*2)
    return {'itens':[_linha(r,request) for r in rows],'proximo_cursor':None}


@router.post(B,status_code=201)
async def criar(request:Request,tenant_id:str,body:Nova,actor=Depends(painel)):
    await _empresa(request,tenant_id)
    dono=actor if body.escopo=='pessoal' else None
    _pode_escrever(request,dono)
    async with request.app.state.db.acquire() as c,c.transaction():
        if await c.fetchval('SELECT count(*) FROM respostas_rapidas WHERE tenant_id=$1 AND dono IS NOT DISTINCT FROM $2',tenant_id,dono)>=MAXIMO:
            raise HTTPException(409,{'campo':'limite'})
        try:
            r=await c.fetchrow('''INSERT INTO respostas_rapidas(tenant_id,atalho,texto,dono,criado_por) VALUES ($1,$2,$3,$4,$5) RETURNING *''',
                               tenant_id,body.atalho,body.texto,dono,actor)
        except asyncpg.UniqueViolationError: raise HTTPException(409,{'campo':'atalho'}) from None
        await auditar(c,tenant_id,actor,'resposta_rapida.criada',str(r['id']),{'escopo':body.escopo})
    return _linha(r,request)


@router.patch(B+'/{rid}')
async def alterar(request:Request,tenant_id:str,rid:int,body:Alteracao,actor=Depends(painel)):
    await _empresa(request,tenant_id)
    async with request.app.state.db.acquire() as c,c.transaction():
        atual=await _visivel(c,tenant_id,rid,actor)
        _pode_escrever(request,atual['dono'])
        try:
            r=await c.fetchrow('''UPDATE respostas_rapidas SET atalho=COALESCE($3,atalho),texto=COALESCE($4,texto),atualizado_em=now()
                WHERE tenant_id=$1 AND id=$2 RETURNING *''',tenant_id,rid,body.atalho,body.texto)
        except asyncpg.UniqueViolationError: raise HTTPException(409,{'campo':'atalho'}) from None
        await auditar(c,tenant_id,actor,'resposta_rapida.alterada',str(rid),{})
    return _linha(r,request)


@router.delete(B+'/{rid}')
async def apagar(request:Request,tenant_id:str,rid:int,actor=Depends(painel)):
    await _empresa(request,tenant_id)
    async with request.app.state.db.acquire() as c,c.transaction():
        atual=await _visivel(c,tenant_id,rid,actor)
        _pode_escrever(request,atual['dono'])
        await c.execute('DELETE FROM respostas_rapidas WHERE tenant_id=$1 AND id=$2',tenant_id,rid)
        await auditar(c,tenant_id,actor,'resposta_rapida.apagada',str(rid),{})
    return {'ok':True}


@router.post(B+'/{rid}/uso')
async def usar(request:Request,tenant_id:str,rid:int,actor=Depends(painel)):
    """Só conta: as mais usadas aparecem primeiro. Qualquer papel, para o que ele vê."""
    await _empresa(request,tenant_id)
    async with request.app.state.db.acquire() as c,c.transaction():
        await _visivel(c,tenant_id,rid,actor)
        await c.execute('UPDATE respostas_rapidas SET usos=usos+1 WHERE tenant_id=$1 AND id=$2',tenant_id,rid)
    return {'ok':True}
