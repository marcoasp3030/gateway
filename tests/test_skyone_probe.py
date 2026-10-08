import asyncio
import importlib.util
from pathlib import Path

import httpx

spec = importlib.util.spec_from_file_location("skyone_probe", Path(__file__).resolve().parents[1] / "scripts/skyone_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_payloads_isolam_instrucao_sem_historico_real():
    casos = probe.payloads("token-teste", "idiomas", 60)
    a, b = casos[0][1].copy(), casos[1][1].copy()
    assert a.pop("instrucoes_agente") != b.pop("instrucoes_agente")
    assert a == b and a["historico"] == []
    assert a["conversation_id"].startswith("probe:")


def test_SEG05_probe_remove_eco_de_segredos():
    corpo = {"data": {"token": "outro-segredo", "resposta": "eco credencial-configurada"},
             "url": "https://teste.invalid/?s=segredo"}
    resultado = probe.sanitizar(corpo, ["credencial-configurada"])
    assert "credencial-configurada" not in str(resultado)
    assert "outro-segredo" not in str(resultado)
    assert "s=segredo" not in str(resultado)


def test_SEG03_probe_valida_cada_chamada_e_nao_segue_redirect(monkeypatch):
    validadas, chamadas = [], []

    async def validar(url):
        validadas.append(url)

    def responder(request):
        chamadas.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/"})

    monkeypatch.setattr(probe, "validar_url_saida", validar)

    async def executar():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            return await probe.executar("https://teste.invalid/", "segredo",
                                        probe.payloads("segredo", "idiomas", 60), 5, client)

    resultados = asyncio.run(executar())
    assert len(validadas) == len(chamadas) == 2
    assert all(r["http_status"] == 302 and not r["ok_http"] for r in resultados)


def test_SEG03_probe_recusa_url_antes_de_enviar(monkeypatch):
    async def recusar(url):
        raise probe.UrlRecusada("URL privada")

    def nao_enviar(request):
        raise AssertionError("não deveria enviar a requisição")

    monkeypatch.setattr(probe, "validar_url_saida", recusar)

    async def executar():
        async with httpx.AsyncClient(transport=httpx.MockTransport(nao_enviar)) as client:
            return await probe.executar("https://127.0.0.1/", "segredo",
                                        probe.payloads("segredo", "tempo", 60), 5, client)

    resultado = asyncio.run(executar())[0]
    assert resultado["erro"] == "UrlRecusada"
    assert not resultado["ok_http"]


def test_probe_timeout_registra_tipo_sem_url_secreta(monkeypatch):
    async def validar(url):
        pass

    def expirar(request):
        raise httpx.ReadTimeout("URL com segredo", request=request)

    monkeypatch.setattr(probe, "validar_url_saida", validar)

    async def executar():
        async with httpx.AsyncClient(transport=httpx.MockTransport(expirar)) as client:
            return await probe.executar("https://teste.invalid/?s=segredo", "segredo",
                                        probe.payloads("segredo", "tempo", 300), 1, client)

    resultado = asyncio.run(executar())[0]
    assert resultado["erro"] == "ReadTimeout"
    assert "segredo" not in str(resultado)
