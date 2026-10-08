"""Testes unitários de segurança (não usam banco). Rodar: pytest -q tests/test_seguranca.py"""
import asyncio
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import seguranca as s  # noqa: E402


# SEG01 — falhar fechado
def test_SEG01_segredo_vazio_curto_ou_exemplo_e_recusado():
    p = s.validar_segredos({"A": "", "B": "curto", "C": "troque-admin-key-123456", "D": "a" * 32})
    assert len(p) == 3 and not any(x.startswith("D") for x in p)


# SEG04 — comparação segura e falha fechada
def test_SEG04_segredo_confere():
    assert s.segredo_confere("abc", "abc")
    assert not s.segredo_confere("abd", "abc")
    assert not s.segredo_confere("", "")          # esperado vazio nunca confere
    assert not s.segredo_confere(None, "abc")


# SEG02 — rotas internas
@pytest.mark.parametrize("path,interna", [
    ("/admin/v1/tenants", True), ("/test/mensagem", True), ("/metricas", True),
    ("/docs", True), ("/openapi.json", True),
    ("/webhooks/uazapi", False), ("/api/cardapio/busca", False), ("/health", False),
    ("/administrador", False),
])
def test_SEG02_classificacao_de_rotas(path, interna):
    assert s.rota_interna(path) is interna


def test_SEG02_chamada_via_nginx_nunca_e_interna():
    assert s.chamada_interna("127.0.0.1", {})
    assert s.chamada_interna("172.18.0.1", {})               # painel/host via rede Docker
    assert not s.chamada_interna("172.18.0.1", {"x-forwarded-for": "200.1.2.3"})
    assert not s.chamada_interna("127.0.0.1", {"x-real-ip": "200.1.2.3"})
    assert not s.chamada_interna("200.1.2.3", {})
    assert not s.chamada_interna(None, {})


# SEG03 — SSRF
@pytest.mark.parametrize("url", [
    "http://exemplo.com/x",                 # não https
    "https://127.0.0.1/x", "https://10.0.0.5/x", "https://172.17.0.1/x",
    "https://192.168.1.1/x", "https://169.254.169.254/latest",   # metadados de nuvem
    "https://[::1]/x", "https://localhost/x", "https://db.internal/x",
    "https://user:pass@exemplo.com/x", "https://exemplo.com:5432/x",
])
def test_SEG03_urls_internas_recusadas(url):
    with pytest.raises(s.UrlRecusada):
        s.validar_formato_url(url)


def test_SEG03_lista_de_dominios():
    assert s.validar_formato_url("https://api.skyone.cloud/x", ["skyone.cloud"]) == "api.skyone.cloud"
    with pytest.raises(s.UrlRecusada):
        s.validar_formato_url("https://skyone.cloud.atacante.com/x", ["skyone.cloud"])


def test_SEG03_dns_para_ip_privado_e_recusado(monkeypatch):
    async def falso_getaddrinfo(host, port, type=0):
        return [(2, 1, 6, "", ("10.0.0.7", 443))]
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(loop, "getaddrinfo", falso_getaddrinfo)
    with pytest.raises(s.UrlRecusada):
        loop.run_until_complete(s.validar_url_saida("https://parece-publico.com/hook"))
    loop.close()


# SEG05 — segredos fora do log
def test_SEG05_query_string_mascarada():
    r = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                          '%s "%s %s"', ("1.2.3.4", "POST", "/webhooks/uazapi?s=SEGREDO123&x=1"), None)
    s.FiltroSegredos().filter(r)
    assert "SEGREDO123" not in r.getMessage() and "s=***" in r.getMessage()
