"""Processos da fila durável (Fase 0.6): `python processos.py worker` ou `agendador`.

Mesma imagem da recepção. Com FILA_DURAVEL=off, sobem e mantêm sinais de vida e
indicadores, mas não agendam nem consomem nada.
"""
import asyncio
import logging
import signal
import sys

import asyncpg
import httpx
import redis.asyncio as aioredis

import migrar
import seguranca
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from nucleo.credenciais import Cofre
from nucleo.fila import FilaDuravel
from nucleo.pipeline import Pipeline
from plataforma import Infra

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)
log = logging.getLogger('gateway.processos')


async def principal(papel):
    infra = Infra.ambiente(); infra.validar()
    filtro = seguranca.FiltroSegredos()
    for nome in ('', 'gateway'): logging.getLogger(nome).addFilter(filtro)
    for handler in logging.getLogger().handlers: handler.addFilter(filtro)
    db = await asyncpg.create_pool(infra.database_url, min_size=1, max_size=5)
    redis = aioredis.from_url(infra.redis_url, decode_responses=True)
    http = httpx.AsyncClient(follow_redirects=False)
    try:
        # Migrações nunca são automáticas; schema diferente → não sobe.
        if await db.fetchval('SELECT max(versao) FROM schema_migrations') != len(migrar.arquivos()):
            raise SystemExit('Schema incompatível: aplique as migrações explicitamente')
        cofre = Cofre(infra.encryption_key)
        configuracoes = Configuracoes(Repositorio(db))
        pipeline = Pipeline(db, redis, configuracoes, cofre, http, skyone_rpm=infra.skyone_rpm,
                            fila_duravel=infra.fila_duravel)
        fila = FilaDuravel(pipeline, infra.fila_duravel, infra.concorrencia_por_worker)
        parar = asyncio.Event()
        laco = asyncio.get_running_loop()
        for sinal in (signal.SIGTERM, signal.SIGINT): laco.add_signal_handler(sinal, parar.set)
        log.info('%s iniciado; fila durável %s', papel, 'ligada' if infra.fila_duravel else 'desligada')
        # Configuração publicada no gateway vale aqui na hora (sem esperar os 30 s do cache).
        from nucleo.configuracao import escutar_invalidacoes
        escuta = asyncio.create_task(escutar_invalidacoes(redis, configuracoes, pipeline.prefixo, log))
        try:
            if papel == 'worker': await fila.worker(parar)
            else: await fila.agendador(parar=parar)
        finally:
            escuta.cancel(); await asyncio.gather(escuta, return_exceptions=True)
        await pipeline.fechar()
    finally:
        await http.aclose(); await redis.aclose(); await db.close()


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('worker', 'agendador'):
        raise SystemExit('Uso: python processos.py worker|agendador')
    asyncio.run(principal(sys.argv[1]))
