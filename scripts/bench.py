"""Simula clientes mandando mensagens picadas e mede o tempo até a resposta.

Uso:
  pip install httpx
  python bench.py --url http://127.0.0.1:8100 --admin-key SUA_CHAVE --clientes 5

Mede, por cliente: da ÚLTIMA mensagem enviada até a resposta aparecer no histórico
(é o tempo que o cliente percebe). Ao final, mostra também /metricas do servidor.
"""
import argparse
import asyncio
import random
import statistics
import time
import uuid

import httpx

ROTEIROS = [
    ["oi", "boa noite", "vocês entregam no morumbi?"],
    ["olá", "queria ver o cardápio"],
    ["oi tudo bem", "quero pedir", "um combinado de salmão", "pra entrega"],
    ["qual o horário de vocês hoje?"],
    ["boa tarde", "vocês fazem catering para evento?", "umas 50 pessoas"],
]


async def cliente(c: httpx.AsyncClient, url: str, h: dict, idx: int, timeout: float) -> float | None:
    usuario = f"bench-{idx}-{uuid.uuid4().hex[:6]}"
    roteiro = random.choice(ROTEIROS)
    for i, texto in enumerate(roteiro):
        if i:
            await asyncio.sleep(random.uniform(0.6, 2.0))  # ritmo de digitação
        r = await c.post(f"{url}/test/mensagem", headers=h, json={"usuario": usuario, "texto": texto})
        r.raise_for_status()
    t_ultima = time.time()

    conv = f"test:{usuario}"
    while time.time() - t_ultima < timeout:
        await asyncio.sleep(0.3)
        msgs = (await c.get(f"{url}/test/conversa/{conv}", headers=h)).json()
        if any(m["origem"] == "bot" for m in msgs):
            dt = time.time() - t_ultima
            print(f"  {usuario}: {len(roteiro)} msgs -> resposta em {dt:.1f}s")
            return dt
    print(f"  {usuario}: SEM RESPOSTA em {timeout}s")
    return None


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8100")
    p.add_argument("--admin-key", required=True)
    p.add_argument("--clientes", type=int, default=5)
    p.add_argument("--timeout", type=float, default=90)
    a = p.parse_args()
    h = {"X-Admin-Key": a.admin_key}

    async with httpx.AsyncClient(timeout=30) as c:
        print(f"Simulando {a.clientes} clientes simultâneos...")
        tempos = await asyncio.gather(*(cliente(c, a.url, h, i, a.timeout) for i in range(a.clientes)))
        ok = sorted(t for t in tempos if t is not None)
        if ok:
            print(f"\nRespondidos: {len(ok)}/{len(tempos)}")
            print(f"p50: {statistics.median(ok):.1f}s | max: {max(ok):.1f}s")
        print("\nServidor (/metricas):")
        print((await c.get(f"{a.url}/metricas", headers=h)).json())


if __name__ == "__main__":
    asyncio.run(main())
