"""Debounce por conversa + trava + chamada ao agente + envio + métricas.

Redis:
  buf:{conv}    lista de mensagens pendentes do lote
  first:{conv}  timestamp da 1ª mensagem do lote (para o teto MAX_WAIT_S)
  due           sorted set: conversa -> momento em que o lote deve ser disparado
  lock:{conv}   trava: no máximo 1 chamada ao agente por conversa
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import agente
import config
import uazapi

log = logging.getLogger("pipeline")


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


async def enfileirar(redis, conv_id: str, msg_db_id: int, texto: str) -> None:
    agora = time.time()
    pipe = redis.pipeline()
    pipe.rpush(f"buf:{conv_id}", json.dumps({"id": msg_db_id, "t": texto, "ts": agora}))
    pipe.set(f"first:{conv_id}", agora, nx=True)
    pipe.get(f"first:{conv_id}")
    _, _, primeira = await pipe.execute()
    disparo = min(agora + config.DEBOUNCE_S, float(primeira) + config.MAX_WAIT_S)
    await redis.zadd("due", {conv_id: disparo})


async def agendador(app) -> None:
    """Loop único: dispara lotes vencidos. Rode o uvicorn com 1 worker."""
    redis = app.state.redis
    while True:
        try:
            vencidos = await redis.zrangebyscore("due", 0, time.time())
            for conv_id in vencidos:
                if await redis.set(f"lock:{conv_id}", "1", nx=True, ex=config.LOCK_TTL_S):
                    await redis.zrem("due", conv_id)
                    asyncio.create_task(processar(app, conv_id))
                # se a trava existir, o lote espera a resposta atual terminar
        except Exception:
            log.exception("erro no agendador")
        await asyncio.sleep(0.25)


def _envia_de_verdade(conv_id: str) -> bool:
    return config.SEND_MODE == "live" and conv_id.startswith("wa:")


async def enviar(app, conv_id: str, telefone: str | None, textos: list[str]) -> None:
    for i, texto in enumerate(textos):
        if i:
            await asyncio.sleep(0.8)  # ritmo de balões, parece mais humano
        if _envia_de_verdade(conv_id) and telefone:
            await uazapi.enviar_texto(app.state.http, telefone, texto)
        await app.state.db.execute(
            "INSERT INTO mensagens (conversation_id, direcao, origem, conteudo) "
            "VALUES ($1, 'saida', 'bot', $2)", conv_id, texto)


async def pausar(db, conv_id: str, minutos: int) -> None:
    await db.execute(
        "UPDATE conversas SET bot_pausado_ate = now() + make_interval(mins => $2) "
        "WHERE conversation_id = $1", conv_id, minutos)


async def processar(app, conv_id: str) -> None:
    db, redis = app.state.db, app.state.redis
    try:
        pipe = redis.pipeline(transaction=True)
        pipe.lrange(f"buf:{conv_id}", 0, -1)
        pipe.delete(f"buf:{conv_id}")
        pipe.delete(f"first:{conv_id}")
        brutos, _, _ = await pipe.execute()
        itens = [json.loads(b) for b in brutos]
        if not itens:
            return

        conv = await db.fetchrow(
            "SELECT telefone, nome, unidade_preferida, "
            "(bot_pausado_ate IS NOT NULL AND bot_pausado_ate > now()) AS pausado "
            "FROM conversas WHERE conversation_id = $1", conv_id)
        if conv is None or conv["pausado"]:
            return  # humano atendendo: mensagens já estão no histórico

        telefone = conv["telefone"]
        if _envia_de_verdade(conv_id) and telefone:
            asyncio.create_task(uazapi.digitando(app.state.http, telefone))

        menor_id = min(i["id"] for i in itens)
        hist = await db.fetch(
            "SELECT direcao, conteudo FROM mensagens "
            "WHERE conversation_id = $1 AND id < $2 ORDER BY id DESC LIMIT $3",
            conv_id, menor_id, config.HISTORY_TURNS)
        historico = [{"role": "user" if h["direcao"] == "entrada" else "assistant",
                      "content": h["conteudo"]} for h in reversed(hist)]

        request_id = str(uuid.uuid4())
        payload = {
            "token": config.SKYONE_TOKEN,
            "request_id": request_id,
            "conversation_id": conv_id,
            "canal": conv_id.split(":")[0],
            "mensagem": "\n".join(i["t"] for i in itens),
            "historico": historico,
            "cliente": {"nome": conv["nome"], "unidade_preferida": conv["unidade_preferida"]},
            "carrinho": {"itens": [], "total": 0},  # Fase 2
        }

        primeira = min(i["ts"] for i in itens)
        ultima = max(i["ts"] for i in itens)
        t_ini = time.time()
        aviso, status, erro, resultado = False, "ok", None, None

        tarefa = asyncio.create_task(agente.chamar(app.state.http, payload))
        try:
            feito, _ = await asyncio.wait({tarefa}, timeout=config.SLOW_NOTICE_S)
            if not feito:
                aviso = True
                await enviar(app, conv_id, telefone, [config.AVISO_DEMORA])
            restante = max(config.SKYONE_TIMEOUT_S - config.SLOW_NOTICE_S, 1)
            resultado = await asyncio.wait_for(tarefa, timeout=restante)
        except Exception as e:  # timeout, HTTP, formato
            status, erro = "erro", f"{type(e).__name__}: {e}"[:500]
            log.warning("agente falhou em %s: %s", conv_id, erro)
        t_fim = time.time()

        if resultado:
            await enviar(app, conv_id, telefone, resultado["resposta"])
            if resultado.get("acao") == "transferir_humano":
                await pausar(db, conv_id, config.PAUSE_MINUTES)
                log.info("transbordo solicitado pelo agente: %s (%s)",
                         conv_id, resultado.get("motivo"))
        else:
            await enviar(app, conv_id, telefone, [config.FALLBACK])
        t_env = time.time()

        await db.execute(
            """INSERT INTO turnos (conversation_id, request_id, n_mensagens,
                 primeira_msg_em, ultima_msg_em, agente_inicio, agente_fim, enviado_em,
                 espera_ms, agente_ms, total_ms, aviso_demora, modo_agente,
                 status, erro, resposta)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb)""",
            conv_id, uuid.UUID(request_id), len(itens),
            _dt(primeira), _dt(ultima), _dt(t_ini), _dt(t_fim), _dt(t_env),
            int((t_ini - ultima) * 1000), int((t_fim - t_ini) * 1000),
            int((t_env - ultima) * 1000), aviso, config.SKYONE_MODE,
            status, erro, json.dumps(resultado, ensure_ascii=False) if resultado else None)
    except Exception:
        log.exception("erro processando %s", conv_id)
    finally:
        await redis.delete(f"lock:{conv_id}")


async def limpeza_lgpd(app) -> None:
    """LGPD01: payload bruto de webhook tem retenção curta (WEBHOOK_RAW_DIAS).

    Roda na subida e depois a cada 6 h. A retenção de mensagens por tenant entra na Fase 9.
    """
    while True:
        try:
            r = await app.state.db.execute(
                "DELETE FROM webhook_raw WHERE recebido_em < now() - make_interval(days => $1)",
                config.WEBHOOK_RAW_DIAS)
            log.info("limpeza LGPD webhook_raw: %s", r)
        except Exception:
            log.exception("erro na limpeza LGPD")
        await asyncio.sleep(6 * 3600)
