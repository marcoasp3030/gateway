"""Solicitações para a equipe (SOL1): rotas do painel para listar, contar, ver e atualizar."""
import json
from datetime import datetime
from typing import Literal
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from starlette.responses import JSONResponse
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from nucleo.contatos import exibicao
from nucleo.solicitacoes import STATUS
from .servico import auditar

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
# Só anda para frente; cancelar vale a partir de qualquer ponto antes de concluída.
PROXIMOS={'aberta':('em_andamento','concluida','cancelada'),'em_andamento':('concluida','cancelada'),'concluida':(),'cancelada':()}
SELECAO="""SELECT s.*,v.telefone,v.canal_id,k.tipo AS canal_tipo,v.nome AS nome_conversa,ct.nome AS contato_nome,ct.nome_cadastro,ct.nome_informado,ct.nome_perfil,ct.anonimizado_em,
        CASE WHEN v.encerrada_em IS NOT NULL THEN 'encerrada' WHEN v.aguardando_atendente_desde IS NOT NULL THEN 'aguardando_atendente'
             WHEN v.bot_pausado_ate>now() THEN 'atendente' ELSE 'bot' END AS estado,
        (SELECT a.status FROM avisos_internos a WHERE a.tenant_id=s.tenant_id AND a.tipo='solicitacao' AND a.referencia LIKE s.numero||':%' ORDER BY a.id DESC LIMIT 1) AS alerta_status,
        (SELECT a.enviado_em FROM avisos_internos a WHERE a.tenant_id=s.tenant_id AND a.tipo='solicitacao' AND a.referencia LIKE s.numero||':%' ORDER BY a.id DESC LIMIT 1) AS alerta_em,
        (SELECT json_build_object('numero',p.numero,'status',p.status) FROM pedidos p WHERE p.tenant_id=s.tenant_id AND p.conversation_id=s.conversation_id
            AND p.status NOT IN ('concluido','recusado','cancelado') ORDER BY p.id DESC LIMIT 1) AS pedido_aberto
    FROM solicitacoes s
    LEFT JOIN conversas v ON v.tenant_id=s.tenant_id AND v.conversation_id=s.conversation_id
    LEFT JOIN tenant_canais k ON k.tenant_id=v.tenant_id AND k.id=v.canal_id
    LEFT JOIN contatos ct ON ct.tenant_id=s.tenant_id AND ct.id=s.contato_id"""


class Estrito(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Atualizacao(Estrito):
    status:Literal['em_andamento','concluida','cancelada']|None=None
    nota:str|None=Field(None,max_length=500)


def _json(v): return json.loads(v) if isinstance(v,str) else v


def retrato(r):
    quem=exibicao({'nome':r['contato_nome'] or r['nome_conversa'],'nome_cadastro':r['nome_cadastro'],'nome_informado':r['nome_informado'],'nome_perfil':r['nome_perfil'],
                   'anonimizado_em':r['anonimizado_em'],'telefone':r['telefone']},r['telefone'],r['canal_tipo'])
    return {'id':r['id'],'numero':r['numero'],'status':r['status'],'motivo':r['motivo'],'motivo_informado':r['motivo_informado'],'tipo':r['tipo'],'origem':r['origem'],
            'dados':{c:(_json(r['dados']) or {}).get(c) for c in ('data','pessoas','local','observacao')},
            'cliente':{'nome':quem['nome_exibicao'],'nome_origem':quem['nome_origem'],'telefone':quem['telefone_e164'],'contato_id':r['contato_id']},
            'conversation_id':r['conversation_id'],'conversa':{'estado':r['estado'],'pedido_aberto':_json(r['pedido_aberto'])},
            'alerta':{'status':r['alerta_status'] or 'sem_destino','em':r['alerta_em']},
            'atualizacoes':r['atualizacoes'],'criado_em':r['criado_em'],'atualizado_em':r['atualizado_em'],
            'atendido_por':r['atendido_por'],'concluido_em':r['concluido_em'],'nota':r['nota'],'proximos_status':list(PROXIMOS[r['status']])}


async def _empresa(request,tenant_id):
    if not await Repositorio(request.app.state.db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')


@router.get('/tenants/{tenant_id}/solicitacoes')
async def lista(request:Request,tenant_id:str,status:str|None=None,motivo:str|None=None,tipo:str|None=None,desde:datetime|None=None,ate:datetime|None=None,
                busca:str|None=None,limite:int=50,cursor:int=0):
    await _empresa(request,tenant_id)
    varios=[s for s in (status or '').split(',') if s] or None
    if (not 1<=limite<=100 or cursor<0 or (varios and any(s not in STATUS for s in varios)) or (busca is not None and not 2<=len(busca.strip())<=100)
            or any(d is not None and d.tzinfo is None for d in (desde,ate))): raise HTTPException(400,'VALIDATION_ERROR')
    termo=busca.strip() if busca else None;digitos=''.join(c for c in termo or '' if c.isdigit())
    rows=await request.app.state.db.fetch(SELECAO+"""
        WHERE s.tenant_id=$1 AND ($2::text[] IS NULL OR s.status=ANY($2)) AND ($3::text IS NULL OR s.motivo=$3) AND ($4::text IS NULL OR s.tipo=$4)
        AND ($5::timestamptz IS NULL OR s.criado_em>=$5) AND ($6::timestamptz IS NULL OR s.criado_em<$6) AND ($7=0 OR s.id<$7)
        AND ($8::text IS NULL OR strpos(lower(s.numero),lower($8))>0 OR strpos(lower(concat_ws(' ',v.nome,ct.nome_cadastro,ct.nome_informado,ct.nome_perfil)),lower($8))>0
             OR ($9<>'' AND strpos(coalesce(v.telefone,''),$9)>0))
        ORDER BY s.id DESC LIMIT $10""",tenant_id,varios,motivo,tipo,desde,ate,cursor,termo,digitos,limite+1)
    return {'itens':[retrato(r) for r in rows[:limite]],'proximo_cursor':rows[limite-1]['id'] if len(rows)>limite else None}


@router.get('/tenants/{tenant_id}/solicitacoes/contagem')
async def contagem(request:Request,tenant_id:str):
    """Para o alerta do painel: quantas esperam alguém da equipe e desde quando."""
    await _empresa(request,tenant_id)
    r=await request.app.state.db.fetchrow("""SELECT count(*) FILTER (WHERE status='aberta') AS abertas,count(*) FILTER (WHERE status='em_andamento') AS em_andamento,
        min(criado_em) FILTER (WHERE status='aberta') AS mais_antiga_em FROM solicitacoes WHERE tenant_id=$1""",tenant_id)
    return dict(r)


async def _uma(db,tenant_id,sid):
    r=await db.fetchrow(SELECAO+' WHERE s.tenant_id=$1 AND s.id=$2',tenant_id,sid)
    if not r: raise HTTPException(404,'NOT_FOUND')
    return retrato(r)


@router.get('/tenants/{tenant_id}/solicitacoes/{sid}')
async def detalhe(request:Request,tenant_id:str,sid:int):
    return await _uma(request.app.state.db,tenant_id,sid)


@router.patch('/tenants/{tenant_id}/solicitacoes/{sid}')
async def atualizar(request:Request,tenant_id:str,sid:int,dados:Atualizacao,actor=Depends(painel)):
    """Qualquer papel. Mudar para "em_andamento" grava quem assumiu; status só anda para frente."""
    enviados=dados.model_fields_set
    if not enviados: raise HTTPException(400,'VALIDATION_ERROR')
    async with request.app.state.db.acquire() as conn,conn.transaction():
        s=await conn.fetchrow('SELECT * FROM solicitacoes WHERE tenant_id=$1 AND id=$2 FOR UPDATE',tenant_id,sid)
        if not s: raise HTTPException(404,'NOT_FOUND')
        if dados.status is not None and dados.status!=s['status']:
            if dados.status not in PROXIMOS[s['status']]:
                return JSONResponse({'erro':{'codigo':'CONFLICT','mensagem':'Mudança de status não permitida.','detalhes':{'campo':'status','status':s['status']}}},
                                    status_code=409,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
            await conn.execute("""UPDATE solicitacoes SET status=$3,atualizado_em=now(),
                atendido_por=CASE WHEN $3='em_andamento' OR atendido_por IS NULL THEN $4 ELSE atendido_por END,
                concluido_em=CASE WHEN $3 IN ('concluida','cancelada') THEN now() ELSE concluido_em END WHERE tenant_id=$1 AND id=$2""",tenant_id,sid,dados.status,actor[:100])
            await auditar(conn,tenant_id,actor,'solicitacao.status',s['numero'])
        if 'nota' in enviados:
            await conn.execute('UPDATE solicitacoes SET nota=$3,atualizado_em=now() WHERE tenant_id=$1 AND id=$2',tenant_id,sid,(dados.nota or '').strip() or None)
            await auditar(conn,tenant_id,actor,'solicitacao.nota',s['numero'])
    return await _uma(request.app.state.db,tenant_id,sid)
