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
from nucleo.contatos import exibicao, canal_info
from canais import uazapi
from agentes.controle import CredencialRecusada, CircuitoAberto

router = APIRouter(prefix='/admin/v1', dependencies=[Depends(painel)])


class Teste(Estrito):
    message: Literal['teste do painel'] = 'teste do painel'
    # Chama o provedor real só neste teste, mesmo com o agente em mock; o agente não muda.
    usar_provedor_real: bool = False


def conferir_provedor_real(agente, empresa):
    """Provedor real exige Skyone, URL e credencial completa (SEG13); indica o campo que falta."""
    if agente['provedor'] != 'skyone': raise HTTPException(400, {'campo': 'provedor'})
    if not agente['skyone_webhook_url']: raise HTTPException(400, {'campo': 'skyone_webhook_url'})
    tipo = agente['skyone_auth_tipo']
    if tipo == 'nenhuma':
        if not empresa['tenant_teste']: raise HTTPException(400, {'campo': 'skyone_auth'})
        return
    if tipo == 'basic' and not agente['skyone_auth_usuario_enc']:
        raise HTTPException(400, {'campo': 'skyone_auth_usuario'})
    if not agente['skyone_auth_enc']: raise HTTPException(400, {'campo': 'skyone_auth'})
    if tipo == 'cabecalho' and not agente['skyone_auth_cabecalho']:
        raise HTTPException(400, {'campo': 'skyone_auth_cabecalho'})


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
    linha = await exigir(request, tenant, 'agentes', agente)
    state = request.app.state
    cfg = await state.configuracoes.efetiva(tenant, agente)
    real = bool(body and body.usar_provedor_real)
    if real:
        conferir_provedor_real(linha, await Repositorio(state.db).tenant(tenant))
        # Só esta chamada usa o provedor real: cota por URL e circuito valem como no live.
        cfg = {**cfg, 'agente_mode': 'live'}
    sufixo = '_provedor_real' if real else ''

    async def registrar(acao):
        async with state.db.acquire() as conn, conn.transaction():
            await auditar(conn, tenant, actor, acao, agente)

    rid = str(uuid.uuid4())
    payload = {'message': 'teste do painel', 'historico': [], 'session_id': rid,
               'request_id': rid, 'conversation_id': f'{tenant}:test:{rid}',
               'tenant_id': tenant, 'agente_id': agente, 'modelo_id': cfg['modelo_id'],
               'instrucoes_agente': cfg['instrucoes_agente'] or ''}
    inicio = time.monotonic()
    try:
        resultado = await state.pipeline.chamar(
            state.http, Repositorio(state.db), state.cofre, tenant, cfg, payload)
    except (CredencialRecusada,CircuitoAberto) as erro:
        await registrar(f'agente.teste{sufixo}_falhou')
        raise HTTPException(502,str(erro)) from None
    except Exception:
        await registrar(f'agente.teste{sufixo}_falhou')
        raise HTTPException(502, 'UPSTREAM_ERROR: teste do agente falhou') from None
    await registrar(f'agente.testado{sufixo}')
    return {'ok': True, 'latencia_ms': round((time.monotonic()-inicio)*1000), 'resposta': resultado['resposta'],
            'provedor_real': real}


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
                    aguardando: bool | None = None, estado: str | None = None,
                    ordem: Literal['id','recentes'] = 'id',
                    desde: datetime | None = None, limite: int = 50, cursor: str | None = None):
    repo = Repositorio(request.app.state.db)
    if not await repo.tenant(tenant): raise HTTPException(404, 'TENANT_NOT_FOUND')
    if agente: await exigir(request, tenant, 'agentes', agente)
    if canal: await exigir(request, tenant, 'tenant_canais', canal)
    estados = [e for e in (estado or '').split(',') if e] or None
    if (not 1 <= limite <= 100 or (busca and len(busca)>200) or (desde and desde.tzinfo is None)
            or (estados and any(e not in ESTADOS for e in estados))):
        raise HTTPException(400, 'VALIDATION_ERROR')
    # ordem=recentes: cursor próprio "<atualizado_em ISO>|<conversation_id>" (opaco para o painel).
    apos_em = apos_id = None
    if cursor and ordem == 'recentes':
        try:
            bruto, apos_id = cursor.split('|', 1)
            apos_em = datetime.fromisoformat(bruto)
            if apos_em.tzinfo is None or not apos_id: raise ValueError
        except ValueError: raise HTTPException(400, 'VALIDATION_ERROR') from None
    rows = await request.app.state.db.fetch(f'''SELECT c.conversation_id,c.agente_id,c.canal_id,c.canal,c.nome,
        c.telefone,c.bot_pausado_ate,c.atualizado_em,c.aguardando_atendente_desde,c.aguardando_motivo,
        c.encerrada_em,c.desfecho,c.limitado_ate,c.contato_id,c.simulada,
        CASE WHEN c.bot_pausado_ate>now() THEN 'pausada' ELSE 'ativa' END AS status,
        {ESTADO_SQL} AS estado,
        m.conteudo AS ultima_mensagem,m.enviada_em AS ultima_em,m.direcao AS ultima_direcao,m.origem AS ultima_origem,m.tipo AS ultima_tipo,
        ct.nome AS contato_nome,ct.nome_cadastro,ct.nome_informado,ct.nome_perfil,ct.anonimizado_em,
        k.tipo AS canal_tipo,k.nome AS canal_nome,t.tenant_teste,
        coalesce(c.unidade_preferida,ca.unidade) AS unidade_preferida,u.nome AS unidade_preferida_nome,
        p.numero AS pedido_numero,p.status AS pedido_status
        FROM conversas c
        JOIN tenants t ON t.id=c.tenant_id
        LEFT JOIN tenant_canais k ON k.tenant_id=c.tenant_id AND k.id=c.canal_id
        LEFT JOIN contatos ct ON ct.tenant_id=c.tenant_id AND ct.id=c.contato_id
        LEFT JOIN LATERAL (
            SELECT conteudo,enviada_em,direcao,origem,tipo FROM mensagens WHERE tenant_id=c.tenant_id
            AND conversation_id=c.conversation_id ORDER BY id DESC LIMIT 1) m ON true
        LEFT JOIN LATERAL (
            SELECT unidade FROM carrinhos WHERE tenant_id=c.tenant_id AND conversation_id=c.conversation_id
            AND unidade IS NOT NULL ORDER BY id DESC LIMIT 1) ca ON c.unidade_preferida IS NULL
        LEFT JOIN tenant_unidades u ON u.tenant_id=c.tenant_id AND u.codigo=coalesce(c.unidade_preferida,ca.unidade)
        LEFT JOIN LATERAL (
            SELECT numero,status FROM pedidos WHERE tenant_id=c.tenant_id AND conversation_id=c.conversation_id
            AND status NOT IN ('concluido','recusado','cancelado') ORDER BY id DESC LIMIT 1) p ON true
        WHERE c.tenant_id=$1 AND ($2::text IS NULL OR c.agente_id=$2)
        AND ($3::text IS NULL OR c.canal_id=$3)
        AND ($4::text IS NULL OR (CASE WHEN c.bot_pausado_ate>now() THEN 'pausada' ELSE 'ativa' END)=$4)
        AND ($5::text IS NULL OR strpos(lower(concat_ws(' ',c.nome,c.telefone,ct.nome_cadastro,ct.nome_informado,ct.nome_perfil)),lower($5))>0)
        AND ($6::timestamptz IS NULL OR c.atualizado_em>=$6)
        AND ($7::text IS NULL OR $11<>'id' OR c.conversation_id>$7)
        AND ($12::timestamptz IS NULL OR (c.atualizado_em,c.conversation_id)<($12,$13::text))
        AND ($9::boolean IS NULL OR (c.aguardando_atendente_desde IS NOT NULL)=$9)
        AND ($10::text[] IS NULL OR {ESTADO_SQL}=ANY($10))
        ORDER BY {'c.atualizado_em DESC,c.conversation_id DESC' if ordem == 'recentes' else 'c.conversation_id'} LIMIT $8''',
        tenant, agente, canal, status, busca, desde, cursor, limite+1, aguardando, estados, ordem, apos_em, apos_id)
    proximo = None
    if len(rows) > limite:
        r = rows[limite-1]
        proximo = f"{r['atualizado_em'].isoformat()}|{r['conversation_id']}" if ordem == 'recentes' else r['conversation_id']
    itens = [conversa_do_painel(r) for r in rows[:limite]]
    # SOL1: solicitação aberta para a equipe em cada conversa da página (uma consulta só).
    abertas = {}
    for s in await request.app.state.db.fetch("""SELECT conversation_id,id,numero,tipo,status FROM solicitacoes WHERE tenant_id=$1
            AND conversation_id=ANY($2::text[]) AND status IN ('aberta','em_andamento') ORDER BY id""", tenant, [i['conversation_id'] for i in itens]):
        abertas[s['conversation_id']] = {'id': s['id'], 'numero': s['numero'], 'tipo': s['tipo'], 'status': s['status']}
    for i in itens: i['solicitacao_aberta'] = abertas.get(i['conversation_id'])
    return {'itens': itens, 'proximo_cursor': proximo}


ESTADOS = ('encerrada','aguardando_atendente','atendente','bot')
# Um estado só, nesta ordem de precedência (docs/ESPEC_PED1.md, seção 6.2).
ESTADO_SQL = """(CASE WHEN c.encerrada_em IS NOT NULL THEN 'encerrada'
    WHEN c.aguardando_atendente_desde IS NOT NULL THEN 'aguardando_atendente'
    WHEN c.bot_pausado_ate>now() THEN 'atendente' ELSE 'bot' END)"""
CAMPOS_ANTIGOS = ('conversation_id','agente_id','canal_id','canal','nome','telefone','bot_pausado_ate','atualizado_em',
                  'aguardando_atendente_desde','aguardando_motivo','encerrada_em','desfecho','limitado_ate','status',
                  'ultima_mensagem','ultima_em')


def conversa_do_painel(r):
    """Campos de sempre (nada quebra) + os do PED1-c. Contato anonimizado: nomes e telefones nulos."""
    linha = dict(r)
    anon = bool(linha['anonimizado_em'])
    base = {k: linha[k] for k in CAMPOS_ANTIGOS}
    if anon: base.update(nome=None, telefone=None)
    nomes = exibicao({'nome': linha['contato_nome'] or linha['nome'], 'nome_cadastro': linha['nome_cadastro'],
                      'nome_informado': linha['nome_informado'], 'nome_perfil': linha['nome_perfil'],
                      'anonimizado_em': linha['anonimizado_em']},
                     None if anon else linha['telefone'], linha['canal_tipo'] or linha['canal'])
    ultima = None if linha['ultima_em'] is None and linha['ultima_mensagem'] is None else {
        'texto': linha['ultima_mensagem'], 'em': linha['ultima_em'], 'direcao': linha['ultima_direcao'],
        'origem': linha['ultima_origem'], 'tipo': linha['ultima_tipo']}
    return {**base, 'contato_id': linha['contato_id'], **nomes,
            'canal_info': canal_info(linha['canal_id'], linha['canal_tipo'], linha['canal_nome'], linha['tenant_teste'], linha['simulada']),
            'unidade_preferida': linha['unidade_preferida'],
            'unidade_preferida_nome': linha['unidade_preferida_nome'] or linha['unidade_preferida'],
            'estado': linha['estado'], 'ultima': ultima,
            'pedido_aberto': {'numero': linha['pedido_numero'], 'status': linha['pedido_status']} if linha['pedido_numero'] else None}


async def pausar(request, tenant, conv, minutos, actor, acao):
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', conv)
        # Atendente agiu (assumir, devolver, responder): sai de "aguardando atendente".
        row = await conn.fetchrow('''UPDATE conversas SET bot_pausado_ate=
            CASE WHEN $3::integer IS NULL THEN NULL ELSE now()+make_interval(mins=>$3) END,
            aguardando_atendente_desde=NULL,aguardando_motivo=NULL,
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
            marca = (None, None)
            if cfg['send_mode']=='live':
                if canal['tipo']!='uazapi': raise HTTPException(501, 'Envio deste canal não implementado')
                marca = uazapi.marca_do_envio(canal['id'], await uazapi.enviar(state.http, state.cofre, tenant, canal, conversa['telefone'], body.texto))
            msgid = await conn.fetchval("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,message_id,meta) VALUES ($1,$2,'saida','humano',$3,now(),$4,$5::jsonb) RETURNING id",tenant,conv,body.texto,*marca)
            await auditar(conn, tenant, actor, 'mensagem.humana', conv)
    except HTTPException:
        raise
    except Exception:
        async with state.db.acquire() as conn, conn.transaction():
            await auditar(conn, tenant, actor, 'mensagem.humana_falhou', conv)
        raise HTTPException(502, 'UPSTREAM_ERROR: envio humano falhou') from None
    return {'ok': True, 'mensagem_id': msgid, 'origem': 'humano', 'send_mode': cfg['send_mode']}


@router.get('/tenants/{tenant}/eventos/cursor')
async def cursor_eventos(request: Request, tenant: str):
    db = request.app.state.db
    if not await Repositorio(db).tenant(tenant):
        raise HTTPException(404, 'NOT_FOUND')
    # Apenas o agregado; nenhum evento/conteúdo é carregado ou serializado.
    ultimo = await db.fetchval('SELECT COALESCE(MAX(id), 0) FROM audit_log WHERE tenant_id=$1', tenant)
    return {'ultimo_id': ultimo}


@router.get('/tenants/{tenant}/eventos')
async def eventos(request: Request, tenant: str, desde: int = 0, limite: int = 50):
    if not await Repositorio(request.app.state.db).tenant(tenant): raise HTTPException(404, 'TENANT_NOT_FOUND')
    if not 0<=desde<=9223372036854775807 or not 1<=limite<=100: raise HTTPException(400, 'VALIDATION_ERROR')
    # Fluxo de mudanças derivado da auditoria, sem incluir conteúdo/segredos.
    rows = await request.app.state.db.fetch('SELECT id,acao AS tipo,alvo,criado_em FROM audit_log WHERE tenant_id=$1 AND id>$2 ORDER BY id LIMIT $3',tenant,desde,limite+1)
    return {'itens': [dict(r) for r in rows[:limite]], 'proximo_cursor': rows[min(len(rows),limite)-1]['id'] if rows else desde,
            'tem_mais': len(rows)>limite}
