"""Adaptador uazapi.

ATENÇÃO: os nomes de campos abaixo seguem o formato de webhook/envio mais comum
do uazapi, mas confira com um payload real da sua instância. Todo webhook fica
gravado em `webhook_raw` — use essa tabela para ajustar `normalizar()`.
"""
import logging

import httpx

import config

log = logging.getLogger("uazapi")


def normalizar(payload: dict) -> dict | None:
    """Converte o webhook do uazapi num formato interno. None = ignorar."""
    evento = payload.get("EventType") or payload.get("event")
    if evento and evento != "messages":
        return None

    msg = payload.get("message") or {}
    chatid = msg.get("chatid") or msg.get("chatId") or ""
    if chatid.endswith("@g.us") or msg.get("isGroup"):
        return None  # ignora grupos

    telefone = chatid.split("@")[0] or str(msg.get("sender", "")).split("@")[0]
    if not telefone:
        return None

    texto = msg.get("text")
    if not texto and isinstance(msg.get("content"), str):
        texto = msg["content"]

    return {
        "telefone": telefone,
        "nome": msg.get("senderName"),
        "texto": (texto or "").strip(),
        "tipo": msg.get("messageType") or msg.get("type"),
        "message_id": msg.get("messageid") or msg.get("id"),
        "from_me": bool(msg.get("fromMe")),
        # True quando a mensagem foi enviada pela API (pelo próprio bot)
        "via_api": bool(msg.get("wasSentByApi")),
    }


async def _post(client: httpx.AsyncClient, path: str, body: dict) -> None:
    r = await client.post(
        f"{config.UAZAPI_BASE_URL}{path}",
        headers={"token": config.UAZAPI_TOKEN},
        json=body,
        timeout=15,
    )
    if r.status_code >= 400:
        log.warning("uazapi %s -> %s %s", path, r.status_code, r.text[:300])


async def digitando(client: httpx.AsyncClient, telefone: str) -> None:
    await _post(client, "/message/presence",
                {"number": telefone, "presence": "composing", "delay": 4000})


async def enviar_texto(client: httpx.AsyncClient, telefone: str, texto: str) -> None:
    await _post(client, "/send/text", {"number": telefone, "text": texto})
