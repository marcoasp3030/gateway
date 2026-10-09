"""Indicadores da fila durável na API administrativa (Fase 0.6)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio

router = APIRouter(prefix='/admin/v1', dependencies=[Depends(painel)])


@router.get('/fila')
async def fila_plataforma(request: Request):
    """Visão da plataforma: todas as empresas, agentes em andamento e cota por URL (só hash)."""
    return await request.app.state.fila.indicadores()


async def fila_da_empresa(request, tenant):
    if not await Repositorio(request.app.state.db).tenant(tenant): raise HTTPException(404, 'NOT_FOUND')
    ind = await request.app.state.fila.indicadores()
    vazio = {'aguardando': 0, 'em_processamento': 0, 'mais_antiga_s': 0.0}
    return {'ligada': ind['ligada'], **ind['empresas'].get(tenant, vazio),
            'agentes_em_andamento': ind['agentes_em_andamento'].get(tenant, {})}


@router.get('/tenants/{tenant}/fila')
async def fila_tenant(request: Request, tenant: str):
    return await fila_da_empresa(request, tenant)
