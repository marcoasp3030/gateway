"""Limites de entrada independentes do provedor de mensagens."""
import hashlib
import ipaddress

from starlette.responses import JSONResponse

from seguranca import REDES_INTERNAS


class LimiteCorpo:
    """Conta bytes reais, inclusive em requisições sem Content-Length."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        tamanho = headers.get(b"content-length", b"")
        if tamanho.isdigit() and int(tamanho) > self.max_bytes:
            return await self.recusar(scope, receive, send)
        partes, total = [], 0
        while True:
            evento = await receive()
            if evento["type"] == "http.disconnect":
                return
            parte = evento.get("body", b"")
            total += len(parte)
            if total > self.max_bytes:
                return await self.recusar(scope, receive, send)
            partes.append(parte)
            if not evento.get("more_body", False):
                break
        corpo = b"".join(partes)
        entregue = False

        async def receber():
            nonlocal entregue
            if entregue:
                return await receive()
            entregue = True
            return {"type": "http.request", "body": corpo, "more_body": False}

        await self.app(scope, receber, send)

    async def recusar(self, scope, receive, send):
        resposta = JSONResponse({"detail": "corpo grande demais"}, status_code=413)
        await resposta(scope, receive, send)


def ip_cliente(host: str | None, headers) -> str:
    """O proxy interno sobrescreve X-Real-IP; origem pública não pode forjá-lo."""
    try:
        origem = ipaddress.ip_address(host or "")
        if any(origem in rede for rede in REDES_INTERNAS) and headers.get("x-real-ip"):
            return str(ipaddress.ip_address(headers["x-real-ip"]))
        return str(origem)
    except ValueError:
        return host or "desconhecido"


# Token bucket atômico: webhooks 30/s com capacidade 60; API 10/s, capacidade 20.
# Relógio do Redis evita divergência entre processos. TTL limita chaves ociosas.
_BUCKET = """
local tempo = redis.call('TIME')
local agora = tonumber(tempo[1]) + tonumber(tempo[2]) / 1000000
local taxa, capacidade = tonumber(ARGV[1]), tonumber(ARGV[2])
local anterior = redis.call('HMGET', KEYS[1], 'tokens', 'em')
local tokens = tonumber(anterior[1]) or capacidade
local em = tonumber(anterior[2]) or agora
tokens = math.min(capacidade, tokens + math.max(0, agora - em) * taxa)
local permitido = 0
if tokens >= 1 then tokens = tokens - 1; permitido = 1 end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'em', agora)
redis.call('EXPIRE', KEYS[1], 60)
return permitido
"""


async def permitir(redis, grupo: str, ip: str) -> bool:
    taxa, capacidade = (30, 60) if grupo == "webhooks" else (10, 20)
    identificador = hashlib.sha256(ip.encode()).hexdigest()
    return bool(await redis.eval(_BUCKET, 1, f"limite:{grupo}:{identificador}", taxa, capacidade))
