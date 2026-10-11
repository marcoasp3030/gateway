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


LIMITE_MAX=200


def _instante(nome,valor):
    """Data e hora ISO 8601 com fuso. Em query string o "+" do fuso chega como espaço: aceito."""
    if valor is None: return None
    try: d=datetime.fromisoformat(valor.strip().replace(' ','+'))
    except ValueError: raise HTTPException(400,{'campo':nome}) from None
    if d.tzinfo is None: raise HTTPException(400,{'campo':nome})
    return d


@router.get('/tenants/{tenant_id}/gestao/alteracoes')
async def lista(request:Request,tenant_id:str,entidade:str|None=None,entidade_id:str|None=None,unidade_id:str|None=None,
                desde:str|None=None,ate:str|None=None,limite:int=50,cursor:int=0):
    """Mais novas primeiro. desde (inclusivo) e ate (exclusivo) com fuso; unidade_id traz a unidade e
    todas as exceções dela (inclusive as já removidas) e não combina com entidade/entidade_id."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    if not 1<=limite<=LIMITE_MAX: raise HTTPException(400,{'campo':'limite'})
    if cursor<0: raise HTTPException(400,{'campo':'cursor'})
    de,ha=_instante('desde',desde),_instante('ate',ate)
    if de and ha and de>ha: raise HTTPException(400,{'campo':'ate'})
    if unidade_id is not None:
        if entidade is not None or entidade_id is not None: raise HTTPException(400,{'campo':'unidade_id'})
        if not await db.fetchval('SELECT EXISTS(SELECT 1 FROM tenant_unidades WHERE tenant_id=$1 AND id=$2)',tenant_id,unidade_id):
            raise HTTPException(404,'NOT_FOUND')
    # Exceção e linha de taxa: entidade_id "<id da unidade>:<data ou id>"; taxas inteiras e pagamentos: o id da unidade.
    rows=await db.fetch("""SELECT id,actor,entidade,entidade_id,acao,antes,depois,criado_em FROM gestao_alteracoes
        WHERE tenant_id=$1 AND ($2::text IS NULL OR entidade=$2) AND ($3::text IS NULL OR entidade_id=$3) AND ($4=0 OR id<$4)
        AND ($6::text IS NULL OR (entidade='unidade' AND entidade_id=$6) OR (entidade IN ('excecao','taxa','pagamento') AND split_part(entidade_id,':',1)=$6))
        AND ($7::timestamptz IS NULL OR criado_em>=$7) AND ($8::timestamptz IS NULL OR criado_em<$8)
        ORDER BY id DESC LIMIT $5""",tenant_id,entidade,entidade_id,cursor,limite+1,unidade_id,de,ha)
    def linha(r):
        d=dict(r)
        for c in ('antes','depois'): d[c]=json.loads(d[c]) if isinstance(d[c],str) else d[c]
        d['criado_em']=d['criado_em'].isoformat()
        return d
    return {'itens':[linha(r) for r in rows[:limite]],'proximo_cursor':rows[limite-1]['id'] if len(rows)>limite else None}
