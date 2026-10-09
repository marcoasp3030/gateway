"""Operações do painel sobre agentes, canais e atendimento humano."""
import asyncio
import secrets
import time
import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, field_validator
from .modelos import Estrito
from .servico import auditar
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from canais import uazapi

router = APIRouter(prefix='/admin/v1', dependencies=[Depends(painel)])


class Teste(Estrito):
    message: Literal['teste do painel'] = 'teste do painel'


class Pausa(Estrito):
    minutos: int = Field(default=60, ge=1, le=10080, strict=True)


class MensagemHumana(Estrito):
    texto: str = Field(min_length=1, max_length=20000)

    @field_validator('texto')
    @classmethod
    def texto_valido(cls, value):
        if not value.strip(): raise ValueError('Texto vazio')
        return value


async def exigir(request, tenant, tabela, identidade):
    row = await Repositorio(request.app.state.db).obter(tenant, tabela, identidade)
    if not row: raise HTTPException(404, 'NOT_FOUND')
    return row


@router.post('/tenants/{tenant}/agentes/{agente}/testar')
async def testar(request: Request, tenant: str, agente: str, body: Teste | None = None, actor=Depends(painel)):
    await exigir(request, tenant, 'agentes', agente)
    state = request.app.state
    cfg = await state.configuracoes.efetiva(tenant, agente)
    rid = str(uuid.uuid4())
    payload = {'message': 'teste do painel', 'historico': [], 'session_id': rid,
               'request_id': rid, 'conversation_id': f'{tenant}:test:{rid}',
               'tenant_id': tenant, 'agente_id': agente, 'modelo_id': cfg['modelo_id'],
               'instrucoes_agente': cfg['instrucoes_agente'] or ''}
    inicio = time.monotonic()
    try:
        resultado = await asyncio.wait_for(state.pipeline.chamar(
            state.http, Repositorio(state.db), state.cofre, tenant, cfg, payload), timeout=cfg['timeout_s'])
    except Exception:
        async with state.db.acquire() as conn, conn.transaction():
            await auditar(conn, tenant, actor, 'agente.teste_falhou', agente)
        raise HTTPException(502, 'UPSTREAM_ERROR: teste do agente falhou') from None
    async with state.db.acquire() as conn, conn.transaction():
        await auditar(conn, tenant, actor, 'agente.testado', agente)
    return {'ok': True, 'latencia_ms': round((time.monotonic()-inicio)*1000), 'resposta': resultado['resposta']}


@router.post('/tenants/{tenant}/canais/{canal}/rotacionar-segredo')
async def rotacionar(request: Request, tenant: str, canal: str, actor=Depends(painel)):
    state = request.app.state
    segredo = secrets.token_urlsafe(32)
    async with state.db.acquire() as conn, conn.transaction():
        row = await conn.fetchrow('UPDATE tenant_canais SET webhook_secret=$3 WHERE tenant_id=$1 AND id=$2 RETURNING tipo',
                                  tenant, canal, state.cofre.cifrar(tenant, segredo))
        if not row: raise HTTPException(404, 'NOT_FOUND')
        await auditar(conn, tenant, actor, 'canal.segredo_rotacionado', canal)
    return {'id': canal, 'webhook_url': state.infra.public_url.rstrip('/')+'/webhooks/'+row['tipo']+'/'+quote(canal, safe='')+'?s='+segredo}


@router.get('/tenants/{tenant}/conversas')
async def conversas(request: Request, tenant: str, agente: str | None = None, canal: str | None = None,
                    status: Literal['ativa','pausada'] | None = None, busca: str | None = None,
                    desde: datetime | None = None, limite: int = 50, cursor: str | None = None):
    repo = Repositorio(request.app.state.db)
    if not await repo.tenant(tenant): raise HTTPException(404, 'TENANT_NOT_FOUND')
    if agente: await exigir(request, tenant, 'agentes', agente)
    if canal: await exigir(request, tenant, 'tenant_canais', canal)
    if not 1 <= limite <= 100 or (busca and len(busca)>200) or (desde and desde.tzinfo is None):
        raise HTTPException(400, 'VALIDATION_ERROR')
    rows = await request.app.state.db.fetch('''SELECT c.conversation_id,c.agente_id,c.canal_id,c.canal,c.nome,
        c.telefone,c.bot_pausado_ate,c.atualizado_em,
        CASE WHEN c.bot_pausado_ate>now() THEN 'pausada' ELSE 'ativa' END AS status,
        m.conteudo AS ultima_mensagem,m.enviada_em AS ultima_em
        FROM conversas c LEFT JOIN LATERAL (
            SELECT conteudo,enviada_em FROM mensagens WHERE tenant_id=c.tenant_id
            AND conversation_id=c.conversation_id ORDER BY id DESC LIMIT 1) m ON true
        WHERE c.tenant_id=$1 AND ($2::text IS NULL OR c.agente_id=$2)
        AND ($3::text IS NULL OR c.canal_id=$3)
        AND ($4::text IS NULL OR (CASE WHEN c.bot_pausado_ate>now() THEN 'pausada' ELSE 'ativa' END)=$4)
        AND ($5::text IS NULL OR strpos(lower(coalesce(c.nome,'')||' '||coalesce(c.telefone,'')),lower($5))>0)
        AND ($6::timestamptz IS NULL OR c.atualizado_em>=$6)
        AND ($7::text IS NULL OR c.conversation_id>$7)
        ORDER BY c.conversation_id LIMIT $8''', tenant, agente, canal, status, busca, desde, cursor, limite+1)
    return {'itens': [dict(r) for r in rows[:limite]],
            'proximo_cursor': rows[limite-1]['conversation_id'] if len(rows)>limite else None}


async def pausar(request, tenant, conv, minutos, actor, acao):
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', conv)
        row = await conn.fetchrow('''UPDATE conversas SET bot_pausado_ate=
            CASE WHEN $3::integer IS NULL THEN NULL ELSE now()+make_interval(mins=>$3) END,
            atualizado_em=now() WHERE tenant_id=$1 AND conversation_id=$2
            RETURNING bot_pausado_ate,agente_id''', tenant, conv, minutos)
        if not row: raise HTTPException(404, 'NOT_FOUND')
        await auditar(conn, tenant, actor, acao, conv)
    return dict(row)


@router.post('/tenants/{tenant}/conversas/{conv}/assumir')
async def assumir(request: Request, tenant: str, conv: str, body: Pausa | None = None, actor=Depends(painel)):
    row = await pausar(request, tenant, conv, (body or Pausa()).minutos, actor, 'conversa.assumida')
    return {'ok': True, 'bot_pausado_ate': row['bot_pausado_ate']}


@router.post('/tenants/{tenant}/conversas/{conv}/devolver')
async def devolver(request: Request, tenant: str, conv: str, actor=Depends(painel)):
    row = await pausar(request, tenant, conv, None, actor, 'conversa.devolvida')
    cfg = await request.app.state.configuracoes.efetiva(tenant, row['agente_id'])
    # Apenas pendências ainda não processadas podem voltar à fila.
    if await request.app.state.db.fetchval("SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='entrada' AND processada_em IS NULL AND origem IN ('cliente','sistema'))", tenant, conv):
        await request.app.state.pipeline.enfileirar(tenant, conv, cfg)
    return {'ok': True, 'bot_pausado_ate': None}


@router.post('/tenants/{tenant}/conversas/{conv}/mensagens', status_code=201)
async def mensagem(request: Request, tenant: str, conv: str, body: MensagemHumana, actor=Depends(painel)):
    state = request.app.state
    conversa = await exigir(request, tenant, 'conversas', conv)
    cfg = await state.configuracoes.efetiva(tenant, conversa['agente_id'])
    # A pausa fica confirmada mesmo se o provedor recusar a mensagem humana.
    await pausar(request, tenant, conv, cfg['pause_minutes'], actor, 'conversa.assumida')
    try:
        async with state.db.acquire() as conn, conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', conv)
            conversa = await Repositorio(conn).obter(tenant, 'conversas', conv)
            if not conversa: raise HTTPException(404, 'NOT_FOUND')
            repo = Repositorio(conn)
            canal, empresa, cfg = await state.pipeline.contexto(tenant, conversa['canal_id'], repo, Configuracoes(repo))
            if not canal['ativo'] or empresa['status']!='ativo': raise HTTPException(409, 'CHANNEL_UNAVAILABLE')
            await conn.execute('UPDATE conversas SET bot_pausado_ate=now()+make_interval(mins=>$3),atualizado_em=now() WHERE tenant_id=$1 AND conversation_id=$2',tenant,conv,cfg['pause_minutes'])
            if cfg['send_mode']=='live':
                if canal['tipo']!='uazapi': raise HTTPException(501, 'Envio deste canal não implementado')
                await uazapi.enviar(state.http, state.cofre, tenant, canal, conversa['telefone'], body.texto)
            msgid = await conn.fetchval("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em) VALUES ($1,$2,'saida','humano',$3,now()) RETURNING id",tenant,conv,body.texto)
            await auditar(conn, tenant, actor, 'mensagem.humana', conv)
    except HTTPException:
        raise
    except Exception:
        async with state.db.acquire() as conn, conn.transaction():
            await auditar(conn, tenant, actor, 'mensagem.humana_falhou', conv)
        raise HTTPException(502, 'UPSTREAM_ERROR: envio humano falhou') from None
    return {'ok': True, 'mensagem_id': msgid, 'origem': 'humano', 'send_mode': cfg['send_mode']}


@router.get('/tenants/{tenant}/eventos')
async def eventos(request: Request, tenant: str, desde: int = 0, limite: int = 50):
    if not await Repositorio(request.app.state.db).tenant(tenant): raise HTTPException(404, 'TENANT_NOT_FOUND')
    if not 0<=desde<=9223372036854775807 or not 1<=limite<=100: raise HTTPException(400, 'VALIDATION_ERROR')
    # Fluxo de mudanças derivado da auditoria, sem incluir conteúdo/segredos.
    rows = await request.app.state.db.fetch('SELECT id,acao AS tipo,alvo,criado_em FROM audit_log WHERE tenant_id=$1 AND id>$2 ORDER BY id LIMIT $3',tenant,desde,limite+1)
    return {'itens': [dict(r) for r in rows[:limite]], 'proximo_cursor': rows[min(len(rows),limite)-1]['id'] if rows else desde,
            'tem_mais': len(rows)>limite}
