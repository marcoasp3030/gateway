"""Chamada ao fluxo do agente no Skyone Studio (ou mock para medir só a VPS)."""
import asyncio
import random

import httpx

import config


def _extrair(body) -> dict:
    """Aceita o Retorno do Skyone em formatos diferentes e devolve
    {"resposta": [str, ...], "acao": str}."""
    if isinstance(body, str):
        return {"resposta": [body], "acao": "nenhuma"}
    if isinstance(body, dict) and "resposta" not in body:
        # respostas consolidadas costumam vir dentro de "data"
        for chave in ("data", "body", "result"):
            if isinstance(body.get(chave), dict) and "resposta" in body[chave]:
                body = body[chave]
                break
    if not isinstance(body, dict) or "resposta" not in body:
        raise ValueError(f"Retorno do agente sem 'resposta': {str(body)[:300]}")

    resp = body["resposta"]
    if isinstance(resp, str):
        partes = [p.strip() for p in resp.split("\n\n") if p.strip()]
        resp = partes[:3] if len(partes) > 1 else [resp.strip()]
    return {"resposta": [r for r in resp if r], "acao": body.get("acao") or "nenhuma",
            "motivo": body.get("motivo")}


async def chamar(client: httpx.AsyncClient, payload: dict) -> dict:
    if config.SKYONE_MODE == "mock":
        await asyncio.sleep(random.uniform(config.MOCK_MIN_S, config.MOCK_MAX_S))
        return {"resposta": [f"(mock) Recebi {payload['mensagem']!r}"], "acao": "nenhuma"}

    r = await client.post(config.SKYONE_WEBHOOK_URL, json=payload,
                          timeout=config.SKYONE_TIMEOUT_S)
    r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        body = r.text
    return _extrair(body)
