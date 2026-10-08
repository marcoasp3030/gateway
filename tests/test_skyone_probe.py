import asyncio
import importlib.util
from pathlib import Path

import httpx

spec = importlib.util.spec_from_file_location("skyone_probe", Path(__file__).resolve().parents[1] / "scripts/skyone_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_payloads_isolam_instrucao_sem_historico_real():
    casos = probe.payloads("idiomas", 60)
    for caso in ("idiomas", "latencia", "tempo"):
        for _, payload in probe.payloads(caso, 60):
            assert payload["session_id"] == payload["request_id"]
            assert payload["historico"] == []
    a, b = casos[0][1].copy(), casos[1][1].copy()
    assert a.pop("instrucoes_agente") != b.pop("instrucoes_agente")
    assert all(a.pop(k) != b.pop(k) for k in ("request_id", "session_id", "conversation_id"))
    assert a == b and set(a) == {"message", "historico"}
    assert a["historico"] == []



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
            return await probe.executar("https://teste.invalid/", "",
                                        probe.payloads("idiomas", 60), 5, client)

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
            return await probe.executar("https://127.0.0.1/", "",
                                        probe.payloads("tempo", 60), 5, client)

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
            return await probe.executar("https://teste.invalid/?s=segredo", "",
                                        probe.payloads("tempo", 300), 1, client)

    resultado = asyncio.run(executar())[0]
    assert resultado["erro"] == "ReadTimeout"
    assert "segredo" not in str(resultado)


def test_probe_contrato_real_sem_autenticacao(monkeypatch):
    import json

    async def validar(url):
        pass

    def responder(request):
        assert 'authorization' not in request.headers
        corpo = json.loads(request.content)
        assert set(corpo) == {'message', 'session_id', 'conversation_id', 'request_id', 'instrucoes_agente', 'historico'}
        assert corpo['historico'] == []
        assert corpo['session_id'] == corpo['request_id']
        return httpx.Response(200, json={
            'resposta': 'Posso ajudar com suas dúvidas.', 'request_id': corpo['request_id'],
            'conversation_id': corpo['conversation_id'], 'acao': 'nenhuma',
            'status': 'success', 'tipo': 'text'})

    monkeypatch.setattr(probe, 'validar_url_saida', validar)

    async def executar():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            return await probe.executar('https://teste.invalid/', '', probe.payloads('latencia', 60), 5, client)

    resultados = asyncio.run(executar())
    assert len(resultados) == 3
    assert all(r['contrato_ok'] and r['autenticacao'] == 'ausente' for r in resultados)


def test_probe_http_200_nao_comprova_contrato(monkeypatch):
    async def validar(url):
        pass

    monkeypatch.setattr(probe, 'validar_url_saida', validar)

    async def executar():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={'resposta': 'ok', 'request_id': 'incorreto'}))) as client:
            return await probe.executar('https://teste.invalid/', '', probe.payloads('idiomas', 60), 5, client)

    assert all(r['ok_http'] and not r['contrato_ok'] for r in asyncio.run(executar()))


def test_probe_token_configurado_nao_e_enviado_sem_formato_confirmado():
    import pytest
    with pytest.raises(ValueError, match='pendente'):
        asyncio.run(probe.executar('https://teste.invalid/', 'segredo', [], 5, None))


def test_probe_nao_herda_token_de_producao(monkeypatch, capsys):
    import json
    monkeypatch.setenv('SKYONE_TOKEN', 'nao-usar-credencial-de-producao')
    monkeypatch.setenv('SKYONE_PROBE_TOKEN', '')
    monkeypatch.setattr(probe.sys, 'argv', ['probe', '--dry-run'])
    assert asyncio.run(probe.main()) == 0
    relatorio = json.loads(capsys.readouterr().out)
    assert relatorio['autenticacao'] == 'ausente'
    assert 'nao-usar-credencial' not in str(relatorio)
