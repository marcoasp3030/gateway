"""Registro explícito de capacidades. Cada módulo possui contrato próprio."""
from dataclasses import dataclass
from fastapi import HTTPException
from nucleo.banco import escopo

@dataclass(frozen=True)
class Modulo:
    nome: str
    migracoes: tuple
    rotas: tuple
    escopos: tuple
    contexto: object

async def vazio(repo, tenant, conversa):
    escopo(tenant)
    return {}

async def catalogo_contexto(repo, tenant, conversa):
    escopo(tenant)
    n=await repo.db.fetchval('SELECT count(*) FROM catalogo_itens WHERE tenant_id=$1 AND ativo',tenant)
    return {'itens_ativos':n}

REGISTRO={
 'catalogo':Modulo('catalogo',('006_catalogo.sql',),('/api/v1/catalogo/busca',),('ferramentas:catalogo',),catalogo_contexto),
 'pedidos':Modulo('pedidos',(),(),('ferramentas:pedidos',),vazio),
}

async def habilitado(repo, tenant, nome):
    escopo(tenant)
    if nome not in REGISTRO or not await repo.db.fetchval(
        'SELECT ativo FROM modulos_tenant WHERE tenant_id=$1 AND modulo=$2',tenant,nome):
        raise HTTPException(404,'NOT_FOUND')
    return REGISTRO[nome]

async def contextos(repo,tenant,conversa,cfg):
    modelo=await repo.modelo(tenant,cfg['modelo_id'],cfg['modelo_versao'])
    result={}
    for nome in modelo['modulos']:
        if nome in REGISTRO and await repo.db.fetchval('SELECT ativo FROM modulos_tenant WHERE tenant_id=$1 AND modulo=$2',tenant,nome):
            result[nome]=await REGISTRO[nome].contexto(repo,tenant,conversa)
    return result
