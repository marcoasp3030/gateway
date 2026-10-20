"""ENC1-a: atendimentos de uma conversa no painel — histórico, encerrar e reabrir (docs/ESPEC_ENC1A.md, seção 2.5).
Admin e membro da própria empresa (decisão 6). Encerrar e reabrir são auditados e não mandam nada ao cliente."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from .modelos import Estrito
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from nucleo import atendimentos

router = APIRouter(prefix='/admin/v1', dependencies=[Depends(painel)])


class Encerrar(Estrito):
    motivo: Literal['manual'] = 'manual'


async def _conversa(request, tenant, conv):
    repo = Repositorio(request.app.state.db)
    if not await repo.tenant(tenant): raise HTTPException(404, 'TENANT_NOT_FOUND')
    c = await repo.obter(tenant, 'conversas', conv)
    if not c: raise HTTPException(404, 'NOT_FOUND')
    return c


@router.get('/tenants/{tenant}/conversas/{conv}/atendimentos')
async def historico(request: Request, tenant: str, conv: str, limite: int = 20, cursor: int | None = None):
    """Atendimentos da conversa, do mais recente para o mais antigo. cursor = número do último da página anterior."""
    if not 1 <= limite <= 100 or (cursor is not None and cursor < 1): raise HTTPException(400, 'VALIDATION_ERROR')
    c = await _conversa(request, tenant, conv)
    db = request.app.state.db
    rows = await db.fetch("""SELECT a.*,
            (SELECT count(*) FROM mensagens m WHERE m.tenant_id=a.tenant_id AND m.atendimento_id=a.id) AS mensagens
        FROM atendimentos a WHERE a.tenant_id=$1 AND a.conversation_id=$2 AND ($3::int IS NULL OR a.numero<$3)
        ORDER BY a.numero DESC LIMIT $4""", tenant, conv, cursor, limite + 1)
    # O que os módulos registraram em cada atendimento (pedidos: os pedidos dele), numa consulta por módulo.
    from modulos import ganchos
    dos_modulos = {}
    for _, f in ganchos('resumo_dos_atendimentos'):
        for at, dados in (await f(db, tenant, [r['id'] for r in rows[:limite]])).items(): dos_modulos.setdefault(at, {}).update(dados)
    itens = []
    for r in rows[:limite]:
        atual = atendimentos.para_o_painel(r, c if r['id'] == c.get('atendimento_id') else None)
        itens.append({**atual, 'motivo_inicio': r['motivo_inicio'], 'desfecho': r['desfecho'], 'resumo': r['resumo'],
                      'mensagens': r['mensagens'], **dos_modulos.get(r['id'], {})})
    return {'itens': itens, 'proximo_cursor': rows[limite - 1]['numero'] if len(rows) > limite else None}


@router.post('/tenants/{tenant}/conversas/{conv}/encerrar')
async def encerrar(request: Request, tenant: str, conv: str, body: Encerrar | None = None, actor=Depends(painel)):
    await _conversa(request, tenant, conv)
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', conv)
        a = await atendimentos.encerrar(conn, tenant, conv, (body or Encerrar()).motivo, None, actor)
        if a is None: raise HTTPException(409, 'SEM_ATENDIMENTO_ABERTO')
    return {'ok': True, 'atendimento': atendimentos.para_o_painel(a)}


@router.post('/tenants/{tenant}/conversas/{conv}/reabrir')
async def reabrir(request: Request, tenant: str, conv: str, actor=Depends(painel)):
    await _conversa(request, tenant, conv)
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', conv)
        a = await atendimentos.reabrir(conn, tenant, conv, actor)
        if a is None: raise HTTPException(409, 'NADA_A_REABRIR')
        c = await Repositorio(conn).obter(tenant, 'conversas', conv)
    return {'ok': True, 'atendimento': atendimentos.para_o_painel(a, c)}
