import httpx,pytest
from fastapi import HTTPException
import redis.exceptions
from erros_api import MENSAGENS
from test_admin import preparar,criar,Pool
from test_pipeline_multi import RedisFila,ativo
from test_migrations import banco,migrar,DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


def test_codigos_fixos_envelope_validacao_e_middlewares(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        @app.get('/diagnostico/{status}')
        async def erro(status:int,credencial:bool=False):
            raise HTTPException(status,'credencial do Skyone recusada' if credencial else 'segredo-nao-ecoar')
        async with c:
            casos=[(400,'VALIDATION_ERROR'),(401,'UNAUTHORIZED'),(403,'FORBIDDEN'),(404,'NOT_FOUND'),
                   (409,'CONFLICT'),(429,'RATE_LIMITED'),(502,'UPSTREAM_ERROR'),(501,'NOT_IMPLEMENTED'),(503,'UNAVAILABLE')]
            for status,code in casos:
                r=await c.get('/diagnostico/'+str(status))
                assert r.status_code==status
                assert r.json()['erro']['codigo']==code and r.json()['erro']['mensagem']==MENSAGENS[code]
                assert 'segredo-nao-ecoar' not in r.text
                if status==429: assert r.headers['Retry-After']=='1'
            r=await c.get('/diagnostico/502?credencial=true')
            assert r.json()['erro']['codigo']=='CREDENTIAL_REJECTED'
            r=await c.get('/rota-inexistente')
            assert r.status_code==404 and r.json()['erro']['codigo']=='NOT_FOUND'
            r=await c.get('/admin/v1/tenants',headers={'X-Forwarded-For':'127.0.0.1'})
            assert r.status_code==404 and r.json()['erro']['codigo']=='NOT_FOUND'
            r=await c.post('/admin/v1/tenants',json={'id':'alpha','nome':123})
            assert r.status_code==400 and r.json()['erro']['detalhes']['campo']=='nome'
            r=await c.get('/admin/v1/tenants?limite=invalido')
            assert r.json()['erro']['detalhes']['campo']=='limite'
            r=await c.post('/admin/v1/tenants',content=b'x'*(app.state.infra.max_body_bytes+1))
            assert r.status_code==413 and r.json()['erro']['codigo']=='VALIDATION_ERROR'
            assert r.json()['erro']['detalhes']['campo']=='corpo'
            async def chunks():
                yield b'x'*(app.state.infra.max_body_bytes//2+1)
                yield b'x'*(app.state.infra.max_body_bytes//2+1)
            r=await c.post('/admin/v1/tenants',content=chunks())
            assert r.status_code==413 and r.json()['erro']['codigo']=='VALIDATION_ERROR'
            async def recusar(*args):return False
            monkeypatch.setattr('limites.permitir',recusar)
            r=await c.get('/api/v1/qualquer')
            assert r.status_code==429 and r.headers['Retry-After']=='1'
            assert r.json()['erro']['codigo']=='RATE_LIMITED'
            async def indisponivel(*args):raise redis.exceptions.ConnectionError('senha-nao-expor')
            monkeypatch.setattr('limites.permitir',indisponivel)
            r=await c.get('/api/v1/qualquer')
            assert r.status_code==503 and r.json()['erro']['codigo']=='UNAVAILABLE'
            assert 'senha-nao-expor' not in r.text
    banco(cenario)
