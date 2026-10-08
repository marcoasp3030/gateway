"""Fase 0.3: diagnostica um fluxo de teste sem ativar o atendimento live.

Credenciais somente por ambiente. --dry-run não faz chamadas externas.
O relatório omite URL e token; o conteúdo de teste não contém dados de clientes.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from seguranca import UrlRecusada, mascarar, validar_segredos, validar_url_saida


def payloads(token: str, caso: str, espera_s: int) -> list[tuple[str, dict]]:
    base = {
        "token": token,
        "request_id": str(uuid.uuid4()),
        "conversation_id": "probe:" + uuid.uuid4().hex,
        "canal": "test",
        "mensagem": "Diga uma frase curta explicando como você pode ajudar.",
        "historico": [],
        "cliente": {"nome": "Teste de integração", "unidade_preferida": None},
        "carrinho": {"itens": [], "total": 0},
    }
    if caso == "tempo":
        return [(f"tempo_{espera_s}s", {**base, "probe_delay_s": espera_s,
                                      "instrucoes_agente": "Responda somente: teste concluído."})]
    return [
        ("ingles", {**base, "instrucoes_agente": "Responda somente em inglês."}),
        ("portugues", {**base, "instrucoes_agente": "Responda somente em português."}),
    ]


def sanitizar(valor, segredos: list[str]):
    if isinstance(valor, dict):
        return {k: "***" if any(s in k.lower() for s in ("token", "secret", "password", "authorization", "api_key"))
                else sanitizar(v, segredos) for k, v in valor.items()}
    if isinstance(valor, list):
        return [sanitizar(v, segredos) for v in valor]
    if isinstance(valor, str):
        for segredo in segredos:
            if segredo:
                valor = valor.replace(segredo, "***")
        return mascarar(valor)
    return valor


async def executar(url: str, token: str, casos, timeout: float, client) -> list[dict]:
    resultados = []
    for nome, payload in casos:
        inicio = time.monotonic()
        resultado = {"caso": nome}
        try:
            await validar_url_saida(url)
            async with client.stream("POST", url, json=payload, timeout=timeout,
                                     follow_redirects=False) as resposta:
                resultado["http_status"] = resposta.status_code
                partes, tamanho = [], 0
                async for parte in resposta.aiter_bytes():
                    tamanho += len(parte)
                    if tamanho > 2 * 1024 * 1024:
                        raise ValueError("Resposta excede 2 MiB")
                    partes.append(parte)
                texto = b"".join(partes).decode("utf-8", errors="replace")
                try:
                    corpo = json.loads(texto)
                except ValueError:
                    corpo = texto
                resultado["corpo"] = sanitizar(corpo, [token, url])
                resultado["ok_http"] = 200 <= resposta.status_code < 300
                if 300 <= resposta.status_code < 400:
                    resultado["observacao"] = "Redirecionamento recusado; conferir a URL do fluxo."
        except (httpx.HTTPError, UrlRecusada, ValueError) as erro:
            # Mensagens de bibliotecas podem conter URL/token; guardar só a classe.
            resultado["erro"] = type(erro).__name__
            resultado["ok_http"] = False
        resultado["duracao_ms"] = round((time.monotonic() - inicio) * 1000)
        resultados.append(resultado)
    return resultados


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--caso", choices=["idiomas", "tempo"], default="idiomas")
    parser.add_argument("--espera-s", type=int, choices=[60, 120, 300], default=60)
    parser.add_argument("--timeout", type=float, default=360)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout deve estar entre 1 e 600 segundos")
    url = os.getenv("SKYONE_PROBE_URL") or os.getenv("SKYONE_WEBHOOK_URL", "")
    token = os.getenv("SKYONE_PROBE_TOKEN") or os.getenv("SKYONE_TOKEN", "")
    casos = payloads(token, args.caso, args.espera_s)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "casos": sanitizar(dict(casos), [token, url])},
                         ensure_ascii=False, indent=2))
        return 0
    problemas = validar_segredos({"SKYONE_PROBE_TOKEN": token})
    if not url:
        problemas.append("Configure SKYONE_PROBE_URL no .env")
    if problemas:
        print(json.dumps({"erros": problemas}, ensure_ascii=False))
        return 2
    async with httpx.AsyncClient(follow_redirects=False) as client:
        resultados = await executar(url, token, casos, args.timeout, client)
    print(json.dumps({"executado_em": datetime.now(timezone.utc).isoformat(),
                      "resultados": resultados,
                      "avaliacao": "Revisar respostas no Logger do Skyone; HTTP 2xx não comprova parametrização nem callback."},
                     ensure_ascii=False, indent=2))
    return 0 if all(r["ok_http"] for r in resultados) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
