import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from limites import LimiteCorpo, ip_cliente


@pytest.mark.parametrize("headers,partes,status", [
    ([], [b"123", b"456"], 413),
    ([(b"content-length", b"2")], [b"123456"], 413),
    ([(b"content-length", b"6")], [b""], 413),
    ([], [b"12", b"345"], 200),
    ([], [b""], 200),
])
def test_SEG07_limite_conta_bytes_reais(headers, partes, status):
    enviados, recebidos = [], []
    eventos = [{"type": "http.request", "body": parte, "more_body": i < len(partes)-1}
               for i, parte in enumerate(partes)]

    async def receive():
        return eventos.pop(0)

    async def send(evento):
        enviados.append(evento)

    async def app(scope, receive, send):
        recebidos.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    asyncio.run(LimiteCorpo(app, 5)({"type": "http", "headers": headers}, receive, send))
    assert enviados[0]["status"] == status
    if status == 413:
        assert recebidos == []  # nenhuma rota executada com corpo excedido
    else:
        assert recebidos[0]["body"] == b"".join(partes)


def test_SEG07_ip_publico_nao_pode_forjar_origem():
    assert ip_cliente("8.8.8.8", {"x-real-ip": "1.1.1.1"}) == "8.8.8.8"
    assert ip_cliente("172.18.0.6", {"x-real-ip": "1.1.1.1"}) == "1.1.1.1"
    assert ip_cliente("172.18.0.6", {"x-real-ip": "invalido"}) == "172.18.0.6"


def test_SEG07_desconexao_nao_chama_rota():
    async def receive():
        return {"type": "http.disconnect"}

    async def nao_chamar(*args):
        pytest.fail("cliente desconectado não deve executar a rota")

    asyncio.run(LimiteCorpo(nao_chamar, 5)({"type": "http"}, receive, nao_chamar))
