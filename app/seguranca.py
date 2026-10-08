"""Funções de segurança usadas pelo gateway.

Regras (ver PLANO_CURSOR.md, seção 2.2):
- SEG01 falhar fechado: segredo ausente ou com valor de exemplo impede a subida.
- SEG02 rotas administrativas só pela rede interna, nunca via Nginx público.
- SEG03 URLs de saída validadas contra SSRF.
- SEG04 comparação de segredos em tempo constante.
- SEG05 segredos nunca em log (query string mascarada).
"""
import asyncio
import hmac
import ipaddress
import logging
import re
import socket
from urllib.parse import urlsplit

# ---------------- SEG01: falhar fechado ----------------

VALORES_DE_EXEMPLO = ("troque", "changeme", "example", "exemplo", "secret", "senha")


def validar_segredos(segredos: dict[str, str], minimo: int = 16) -> list[str]:
    """Devolve a lista de problemas. Vazia = tudo certo."""
    problemas = []
    for nome, valor in segredos.items():
        if not valor:
            problemas.append(f"{nome} vazio")
        elif len(valor) < minimo:
            problemas.append(f"{nome} curto demais (mínimo {minimo} caracteres)")
        elif any(valor.lower().startswith(p) for p in VALORES_DE_EXEMPLO):
            problemas.append(f"{nome} ainda está com valor de exemplo")
    return problemas


# ---------------- SEG04: comparação em tempo constante ----------------

def segredo_confere(recebido: str | None, esperado: str | None) -> bool:
    """Falha fechado: esperado vazio nunca confere."""
    if not esperado or recebido is None:
        return False
    return hmac.compare_digest(recebido.encode(), esperado.encode())


# ---------------- SEG02: rotas administrativas só internas ----------------

PREFIXOS_INTERNOS = ("/admin", "/test", "/metricas", "/docs", "/redoc", "/openapi.json")

REDES_INTERNAS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
)]


def rota_interna(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in PREFIXOS_INTERNOS)


def chamada_interna(client_host: str | None, headers) -> bool:
    """Interna = origem em rede privada E sem passar pelo Nginx público.

    O Nginx sempre envia X-Forwarded-For/X-Real-IP (ver docs/nginx-gateway.conf).
    O painel, chamando direto 127.0.0.1:8100, não envia. Só o IP não basta,
    porque o Nginx do host também chega ao container por IP privado.
    """
    if headers.get("x-forwarded-for") or headers.get("x-real-ip") or headers.get("forwarded"):
        return False
    try:
        ip = ipaddress.ip_address(client_host or "")
    except ValueError:
        return False
    return any(ip in rede for rede in REDES_INTERNAS)


# ---------------- SEG03: SSRF ----------------

class UrlRecusada(ValueError):
    pass


def _ip_publico(ip: ipaddress._BaseAddress) -> bool:
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified
                or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped
                    and not _ip_publico(ip.ipv4_mapped)))


def validar_formato_url(url: str, dominios_permitidos: list[str] | None = None) -> str:
    """Validação sem rede (para salvar configuração). Devolve o host."""
    partes = urlsplit(url)
    if partes.scheme != "https":
        raise UrlRecusada("só https é permitido")
    if partes.username or partes.password:
        raise UrlRecusada("credenciais na URL não são permitidas")
    host = (partes.hostname or "").lower().rstrip(".")
    if not host:
        raise UrlRecusada("URL sem host")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".internal") \
            or host.endswith(".local"):
        raise UrlRecusada("host interno não é permitido")
    if partes.port not in (None, 443):
        raise UrlRecusada("só a porta 443 é permitida")
    try:
        ip_literal = ipaddress.ip_address(host)
    except ValueError:
        ip_literal = None  # é um nome, não um IP
    if ip_literal is not None and not _ip_publico(ip_literal):
        raise UrlRecusada("IP privado ou reservado não é permitido")
    if dominios_permitidos and not any(
            host == d or host.endswith("." + d) for d in dominios_permitidos):
        raise UrlRecusada("domínio fora da lista permitida")
    return host


async def validar_url_saida(url: str, dominios_permitidos: list[str] | None = None) -> None:
    """Validação completa, no momento da chamada: formato + DNS resolvendo só para IP público.

    Chamar imediatamente antes de cada requisição de saída (protege contra DNS rebinding
    entre o cadastro e o uso). As requisições de saída também não seguem redirecionamentos.
    """
    host = validar_formato_url(url, dominios_permitidos)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UrlRecusada(f"não foi possível resolver o host: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _ip_publico(ip):
            raise UrlRecusada(f"o host resolve para endereço não público ({ip})")


# ---------------- SEG05: segredos fora dos logs ----------------

_PARAMS_SENSIVEIS = re.compile(r"([?&](?:s|token|key|secret|api_key)=)[^&\s\"]+", re.I)


def mascarar(texto: str) -> str:
    return _PARAMS_SENSIVEIS.sub(r"\1***", texto)


class FiltroSegredos(logging.Filter):
    """Mascara parâmetros sensíveis de query string em qualquer linha de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(mascarar(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = mascarar(record.msg)
        return True
