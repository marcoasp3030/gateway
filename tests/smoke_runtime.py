"""Servidor temporário: schema e fila exclusivos, somente banco *_test.

Executar com porta ligada ao loopback e mount /workspace; nunca como produção.
Não compartilha agendamentos com o serviço publicado. Schema de diagnóstico é
preservado para inspeção; não apaga tabelas ou chaves do serviço.
"""
import asyncio,os,sys,uuid
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
import asyncpg,redis.asyncio as aioredis,uvicorn
sys.path.insert(0,'/workspace/app')
import migrar
from plataforma import Infra,criar_app

async def main():
    url=urlsplit(os.environ['DATABASE_URL'])._replace(path='/gateway_migrations_test').geturl()
    assert urlsplit(url).path.endswith('_test')
    schema='smoke_'+uuid.uuid4().hex
    conn=await asyncpg.connect(url)
    try:
        await conn.execute(f'CREATE SCHEMA {schema}')
        await conn.execute(f'SET search_path TO {schema}')
        await migrar.aplicar(conn)
    finally: await conn.close()
    pool=await asyncpg.create_pool(url,min_size=2,max_size=10,server_settings={'search_path':schema})
    redis=aioredis.from_url(os.environ['REDIS_URL'],decode_responses=True)
    infra=Infra('a'*32,'b'*32,'c'*32,Fernet.generate_key().decode(),url,os.environ['REDIS_URL'])
    app=criar_app(infra,pool,redis)
    app.state.pipeline.prefixo=schema
    print('Schema/fila exclusivos:',schema,flush=True)
    try:
        await uvicorn.Server(uvicorn.Config(app,host='0.0.0.0',port=8000,access_log=False)).serve()
    finally:
        await pool.close();await redis.aclose()

if __name__=='__main__': asyncio.run(main())
