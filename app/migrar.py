"""Migrações explícitas, transacionais e serializadas; nunca executadas no startup."""
import argparse
import asyncio
import hashlib
import os
from pathlib import Path
import re

import asyncpg

PASTA = Path(__file__).parent / 'migrations'
TRAVA = 741035002


def arquivos(pasta=PASTA):
    encontrados = []
    for path in sorted(pasta.glob('*.sql')):
        if not re.fullmatch(r'[0-9]{3}_[a-z0-9_]+\.sql', path.name):
            raise ValueError('Nome de migração inválido')
        encontrados.append((int(path.name[:3]), path.name, path.read_bytes()))
    if [v for v, _, _ in encontrados] != list(range(1, len(encontrados) + 1)):
        raise ValueError('Migrações devem ser contíguas e começar em 001')
    if not encontrados:
        raise ValueError('Nenhuma migração encontrada')
    return encontrados


async def aplicar(conn, pasta=PASTA):
    scripts = arquivos(pasta)
    await conn.execute('SELECT pg_advisory_lock($1)', TRAVA)
    try:
        await conn.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
            versao INTEGER PRIMARY KEY, nome TEXT NOT NULL,
            sha256 TEXT NOT NULL, aplicada_em TIMESTAMPTZ NOT NULL DEFAULT now())''')
        atuais = await conn.fetch('SELECT versao, nome, sha256 FROM schema_migrations ORDER BY versao')
        if [r['versao'] for r in atuais] != list(range(1, len(atuais) + 1)) or len(atuais) > len(scripts):
            raise ValueError('Histórico de migrações incompatível')
        for registro, (_, nome, sql) in zip(atuais, scripts):
            if registro['nome'] != nome or registro['sha256'] != hashlib.sha256(sql).hexdigest():
                raise ValueError('Migração aplicada foi alterada; crie uma nova migração')
        aplicadas = []
        for versao, nome, sql in scripts[len(atuais):]:
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout = '10s'")
                await conn.execute("SET LOCAL statement_timeout = '120s'")
                await conn.execute(sql.decode('utf-8'))
                await conn.execute('INSERT INTO schema_migrations (versao,nome,sha256) VALUES ($1,$2,$3)',
                                   versao, nome, hashlib.sha256(sql).hexdigest())
            aplicadas.append(nome)
        return aplicadas
    finally:
        await conn.execute('SELECT pg_advisory_unlock($1)', TRAVA)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='aplicar no DATABASE_URL configurado')
    args = parser.parse_args()
    if not args.apply:
        parser.error('Use --apply para aplicar explicitamente no DATABASE_URL configurado')
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        nomes = await aplicar(conn)
        print('Aplicadas: ' + (', '.join(nomes) if nomes else 'nenhuma; banco atualizado'))
    finally:
        await conn.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (asyncpg.PostgresError, ValueError, KeyError, OSError):
        # Erros do driver podem conter DSN, credenciais ou conteúdo de registros.
        raise SystemExit('Migração recusada; verifique schema, versões e configuração. Nenhum segredo exibido.') from None
