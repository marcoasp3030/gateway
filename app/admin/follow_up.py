"""FUP1-a: follow-ups no painel — tarefas da conversa, cancelar, e opt-out do contato (docs/ESPEC_FUP1A.md, seção 6).
Ver e cancelar: admin e membro da empresa. Opt-out: membro registra; só admin e plataforma reativam. Tudo auditado."""
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Annotated
from pydantic import Field, StringConstraints
from .modelos import Estrito
from nucleo.autenticacao import painel, exigir_admin
from nucleo.banco import Repositorio
from nucleo import follow_up

router = APIRouter(prefix='/admin/v1', dependencies=[Depends(painel)])


class Cancelar(Estrito):
    motivo: str = Field('painel', pattern=r'^[a-z0-9_]{1,60}$')


class Reativar(Estrito):
    # Obrigatório; o gateway tira dado pessoal antes de gravar (limpeza da LGPD1).
    motivo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=follow_up.MAX_MOTIVO)]


async def _existe(request, tenant, tabela, ident):
    repo = Repositorio(request.app.state.db)
    if not await repo.tenant(tenant): raise HTTPException(404, 'TENANT_NOT_FOUND')
    linha = await repo.obter(tenant, tabela, ident)
    if not linha: raise HTTPException(404, 'NOT_FOUND')
    return linha


@router.get('/tenants/{tenant}/conversas/{conv}/follow-ups')
async def da_conversa(request: Request, tenant: str, conv: str, limite: int = 50):
    """Tarefas da conversa (atendimento atual e anteriores), da mais recente para a mais antiga, e o opt-out do contato."""
    if not 1 <= limite <= 200: raise HTTPException(400, 'VALIDATION_ERROR')
    c = await _existe(request, tenant, 'conversas', conv)
    db = request.app.state.db
    rows = await db.fetch('SELECT * FROM follow_up_tarefas WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT $3', tenant, conv, limite)
    return {'itens': [follow_up.para_o_painel(r) for r in rows], 'atendimento_atual': c.get('atendimento_id'),
            'opt_out': await follow_up.opt_out_do_painel(db, tenant, c['contato_id']) if c.get('contato_id') else None}


@router.post('/tenants/{tenant}/conversas/{conv}/follow-ups/cancelar')
async def cancelar(request: Request, tenant: str, conv: str, body: Cancelar | None = None, actor=Depends(painel)):
    await _existe(request, tenant, 'conversas', conv)
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', conv)
        n = await follow_up.cancelar(conn, tenant, conv, (body or Cancelar()).motivo, actor)
    return {'ok': True, 'cancelados': n}


@router.get('/tenants/{tenant}/contatos/{cid}/opt-out-follow-up')
async def ver_opt_out(request: Request, tenant: str, cid: str):
    await _existe(request, tenant, 'contatos', cid)
    return await follow_up.opt_out_do_painel(request.app.state.db, tenant, cid)


@router.put('/tenants/{tenant}/contatos/{cid}/opt-out-follow-up')
async def registrar_opt_out(request: Request, tenant: str, cid: str, actor=Depends(painel)):
    await _existe(request, tenant, 'contatos', cid)
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await follow_up.registrar_opt_out(conn, tenant, cid, 'painel', actor)
        return await follow_up.opt_out_do_painel(conn, tenant, cid)


@router.delete('/tenants/{tenant}/contatos/{cid}/opt-out-follow-up', dependencies=[Depends(exigir_admin)])
async def reativar(request: Request, tenant: str, cid: str, body: Reativar, actor=Depends(painel)):
    await _existe(request, tenant, 'contatos', cid)
    async with request.app.state.db.acquire() as conn, conn.transaction():
        if not await follow_up.remover_opt_out(conn, tenant, cid, actor, body.motivo): raise HTTPException(404, 'SEM_OPT_OUT')
        return await follow_up.opt_out_do_painel(conn, tenant, cid)
