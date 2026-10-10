"""Histórico das alterações de gestão (unidades, horários, cardápio...): quem, quando, antes e depois.
Guarda só os campos que mudaram. São dados de configuração do negócio; telefones vão mascarados."""
import json
from datetime import date,datetime
from decimal import Decimal
from fastapi import APIRouter,Depends,Request,HTTPException
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio

MASCARADOS=('whatsapp_pedidos','telefone_whatsapp','whatsapp_alerta_transbordo')
router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])


def _valor(campo,v):
    if v is not None and campo in MASCARADOS:
        d=''.join(c for c in str(v) if c.isdigit());return d[:4]+'****'+d[-4:] if len(d)>=10 else '****'
    if isinstance(v,(datetime,date)): return v.isoformat()
    if isinstance(v,Decimal): return float(v)
    if isinstance(v,str) and v[:1] in '[{':
        try: return json.loads(v)
        except ValueError: return v
    return v


def diferenca(antes,depois):
    """(antes, depois) só com os campos que mudaram; None quando um dos lados não existe."""
    a,d=dict(antes or {}),dict(depois or {})
    campos=[c for c in sorted(set(a)|set(d)) if _valor(c,a.get(c))!=_valor(c,d.get(c))]
    return ({c:_valor(c,a.get(c)) for c in campos} if antes is not None else None,
            {c:_valor(c,d.get(c)) for c in campos} if depois is not None else None)


async def registrar(conn,tenant,actor,entidade,entidade_id,acao,antes=None,depois=None):
    """Chamar dentro da transação da alteração. Não grava nada se nada mudou."""
    a,d=diferenca(antes,depois)
    if antes is not None and depois is not None and not a and not d: return False
    await conn.execute('''INSERT INTO gestao_alteracoes(tenant_id,actor,entidade,entidade_id,acao,antes,depois)
        VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)''',tenant,actor,entidade,str(entidade_id),acao,
        json.dumps(a,ensure_ascii=False) if a is not None else None,json.dumps(d,ensure_ascii=False) if d is not None else None)
    return True


@router.get('/tenants/{tenant_id}/gestao/alteracoes')
async def lista(request:Request,tenant_id:str,entidade:str|None=None,entidade_id:str|None=None,limite:int=50,cursor:int=0):
    if not await Repositorio(request.app.state.db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    if not 1<=limite<=200 or cursor<0: raise HTTPException(400,'VALIDATION_ERROR')
    rows=await request.app.state.db.fetch('''SELECT id,actor,entidade,entidade_id,acao,antes,depois,criado_em FROM gestao_alteracoes
        WHERE tenant_id=$1 AND ($2::text IS NULL OR entidade=$2) AND ($3::text IS NULL OR entidade_id=$3) AND ($4=0 OR id<$4)
        ORDER BY id DESC LIMIT $5''',tenant_id,entidade,entidade_id,cursor,limite+1)
    def linha(r):
        d=dict(r)
        for c in ('antes','depois'): d[c]=json.loads(d[c]) if isinstance(d[c],str) else d[c]
        return d
    return {'itens':[linha(r) for r in rows[:limite]],'proximo_cursor':rows[limite-1]['id'] if len(rows)>limite else None}
