import asyncio
import importlib.util
import os
from pathlib import Path
import uuid

import pytest

asyncpg = pytest.importorskip('asyncpg')
spec = importlib.util.spec_from_file_location('migrar', Path(__file__).resolve().parents[1] / 'app/migrar.py')
migrar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrar)
DSN = os.getenv('MIGRATION_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DSN, reason='Defina MIGRATION_TEST_DATABASE_URL para Postgres isolado de teste')


def banco(cenario):
    async def run():
        conn = await asyncpg.connect(DSN)
        tx = conn.transaction()
        await tx.start()
        try:
            schema = 'teste_' + uuid.uuid4().hex
            await conn.execute(f'CREATE SCHEMA {schema}')
            await conn.execute(f'SET LOCAL search_path TO {schema}')
            await cenario(conn)
        finally:
            await tx.rollback()
            await conn.close()
    asyncio.run(run())


def test_migracoes_seed_e_reaplicacao():
    async def cenario(conn):
        assert await migrar.aplicar(conn) == [a.name for a in sorted(migrar.PASTA.glob('*.sql'))]
        assert await migrar.aplicar(conn) == []
        assert await conn.fetchval('SELECT count(*) FROM schema_migrations') == len(migrar.arquivos())
        agente = await conn.fetchrow("SELECT * FROM agentes WHERE id='bykoji_atendimento'")
        assert (agente['timeout_s'],agente['provedor'],agente['send_mode'],agente['status']) == (90,'mock','dry','rascunho')
        assert await conn.fetchval('SELECT count(*) FROM modelos_agente') == 2
        assert await conn.fetchval('SELECT count(*) FROM tenant_canais') == 0
    banco(cenario)


def test_migracao_modificada_recusada(tmp_path):
    for arquivo in migrar.PASTA.glob('*.sql'):
        (tmp_path / arquivo.name).write_bytes(arquivo.read_bytes())
    async def cenario(conn):
        await migrar.aplicar(conn, tmp_path)
        with (tmp_path / '001_base.sql').open('a') as f:
            f.write('\n-- alteração indevida\n')
        with pytest.raises(ValueError, match='alterada'):
            await migrar.aplicar(conn, tmp_path)
    banco(cenario)


def test_migracao_falha_reverte_schema_e_registro(tmp_path):
    (tmp_path / '001_base.sql').write_text('CREATE TABLE primeira(id integer);')
    (tmp_path / '002_falha.sql').write_text('CREATE TABLE parcial(id integer); SELECT 1/0;')
    async def cenario(conn):
        with pytest.raises(asyncpg.DivisionByZeroError):
            await migrar.aplicar(conn, tmp_path)
        assert await conn.fetchval("SELECT to_regclass('parcial')") is None
        assert await conn.fetchval("SELECT to_regclass('primeira')") is not None
        assert await conn.fetchval('SELECT count(*) FROM schema_migrations') == 1
    banco(cenario)


def test_legado_com_dados_e_preservado():
    async def cenario(conn):
        await conn.execute((migrar.PASTA / '001_base.sql').read_text())
        await conn.execute("INSERT INTO conversas(conversation_id,canal) VALUES ('test:preservar','test')")
        with pytest.raises(asyncpg.RaiseError, match='legado'):
            await migrar.aplicar(conn)
        assert await conn.fetchval('SELECT conversation_id FROM conversas') == 'test:preservar'
        assert await conn.fetchval("SELECT to_regclass('tenants')") is None
        assert await conn.fetchval('SELECT count(*) FROM schema_migrations') == 1
    banco(cenario)


def test_INV08_INV17_referencias_de_outro_tenant_recusadas():
    async def cenario(conn):
        await migrar.aplicar(conn)
        await conn.execute("INSERT INTO tenants(id,nome) VALUES ('outro','Outro')")
        await conn.execute("INSERT INTO agentes(id,tenant_id,modelo_id,nome) VALUES ('outro_agente','outro','assistente_generico','Outro')")
        await conn.execute("INSERT INTO tenant_canais(id,tenant_id,tipo,nome,agente_id) VALUES ('can_a','bykoji','api','A','bykoji_atendimento')")
        await conn.execute("INSERT INTO conversas(conversation_id,canal,tenant_id,canal_id,agente_id) VALUES ('bykoji:can_a:teste','api','bykoji','can_a','bykoji_atendimento')")
        await conn.execute("INSERT INTO tenant_canais(id,tenant_id,tipo,nome,agente_id) VALUES ('can_b','outro','api','B','outro_agente')")
        await conn.execute("INSERT INTO conversas(conversation_id,canal,tenant_id,canal_id,agente_id) VALUES ('outro:can_b:teste','api','outro','can_b','outro_agente')")
        for tenant, canal in (('bykoji','can_a'),('outro','can_b')):
            await conn.execute("INSERT INTO mensagens(conversation_id,tenant_id,direcao,origem,conteudo,message_id) VALUES ($1,$2,'entrada','cliente','x','id-provedor-igual')", f'{tenant}:{canal}:teste', tenant)
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute("INSERT INTO mensagens(conversation_id,tenant_id,direcao,origem,conteudo,message_id) VALUES ('outro:can_b:teste','outro','entrada','cliente','x','id-provedor-igual')")
        recusadas = [
            "INSERT INTO tenant_canais(id,tenant_id,tipo,nome,agente_id) VALUES ('errado','bykoji','api','X','outro_agente')",
            "INSERT INTO mensagens(conversation_id,tenant_id,direcao,origem,conteudo) VALUES ('bykoji:can_a:teste','outro','entrada','cliente','x')",
            "INSERT INTO conversas(conversation_id,canal,tenant_id,canal_id,agente_id) VALUES ('outro:can_a:x','api','outro','can_a','outro_agente')",
            "INSERT INTO turnos(conversation_id,tenant_id,agente_id,canal_id,request_id,n_mensagens,status) VALUES ('bykoji:can_a:teste','outro','outro_agente','can_a','00000000-0000-0000-0000-000000000001',1,'ok')",
            "INSERT INTO tenant_api_keys(id,tenant_id,nome,hash,agente_id) VALUES ('key','bykoji','X',repeat('a',64),'outro_agente')",
        ]
        for sql in recusadas:
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                async with conn.transaction():
                    await conn.execute(sql)
        await conn.execute("INSERT INTO modelos_agente(id,tenant_id,nome,tipo) VALUES ('privado','outro','Privado','interno')")
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute("UPDATE agentes SET modelo_id='privado' WHERE id='bykoji_atendimento'")
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute("UPDATE modelos_agente SET tenant_id='bykoji' WHERE id='privado'")
    banco(cenario)


def test_SEG13_schema_auth_generica_e_timeout():
    async def cenario(conn):
        await migrar.aplicar(conn)
        for patch in ("timeout_s=0", "skyone_auth_tipo='bearer'", "skyone_auth_tipo='cabecalho',skyone_auth_enc='cifrado'"):
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute('UPDATE agentes SET '+patch)
        await conn.execute("UPDATE agentes SET skyone_auth_tipo='cabecalho',skyone_auth_cabecalho='X-Api-Key',skyone_auth_enc='fixture-cifrada'")
    banco(cenario)
