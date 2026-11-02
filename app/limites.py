"""Limites de entrada independentes do provedor de mensagens."""
import hashlib
import ipaddress

from erros_api import resposta

from seguranca import REDES_INTERNAS


DOCUMENTOS_BYTES = 16 * 1024 * 1024


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
        # ARQ1-a1: só as rotas de envio de documento aceitam até 16 MiB (o arquivo tem teto próprio, conferido depois).
        caminho = scope.get("path", "")
        maximo = DOCUMENTOS_BYTES if caminho == "/api/v1/documentos" or (caminho.startswith("/admin/v1/") and caminho.endswith(("/simular-documento", "/midia", "/cardapio/aparencia/imagem"))) else self.max_bytes
        if tamanho.isdigit() and int(tamanho) > maximo:
            return await self.recusar(scope, receive, send)
        partes, total = [], 0
        while True:
            evento = await receive()
            if evento["type"] == "http.disconnect":
                return
            parte = evento.get("body", b"")
            total += len(parte)
            if total > maximo:
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
        await resposta(413,campo="corpo")(scope, receive, send)


def ip_cliente(host: str | None, headers) -> str:
    """O proxy interno sobrescreve X-Real-IP; origem pública não pode forjá-lo."""
    try:
        origem = ipaddress.ip_address(host or "")
        if any(origem in rede for rede in REDES_INTERNAS) and headers.get("x-real-ip"):
            return str(ipaddress.ip_address(headers["x-real-ip"]))
        return str(origem)
    except ValueError:
        return host or "desconhecido"


# Token bucket atômico. Valores por balde em cotas().
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


def cotas(grupo: str, ip: str, caminho: str, headers) -> list[tuple[str, int, int]]:
    """Baldes de uma chamada: (identificador, por segundo, capacidade). Provedor de mensagens e Skyone falam por um IP com várias
    empresas: a cota é por canal e por chave, para o movimento de uma não recusar a outra. O teto do IP segura chave e canal inventados."""
    if grupo == "webhooks":
        partes = caminho.split("/")
        canal = partes[3] if len(partes) > 3 else ""
        return [(f"teto:{ip}", 300, 600), (f"{ip}|{canal}", 30, 60)]
    chave = headers.get("x-api-key", "")
    if chave:
        return [(f"teto:{ip}", 200, 400), (f"{ip}|{hashlib.sha256(chave.encode()).hexdigest()}", 10, 20)]
    return [(ip, 10, 20)]


async def permitir(redis, grupo: str, ip: str, caminho: str = "", headers: dict | None = None) -> bool:
    for identificador, taxa, capacidade in cotas(grupo, ip, caminho, headers or {}):
        resumo = hashlib.sha256(identificador.encode()).hexdigest()
        if not await redis.eval(_BUCKET, 1, f"limite:{grupo}:{resumo}", taxa, capacidade):
            return False
    return True
