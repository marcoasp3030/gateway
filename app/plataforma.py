"""Aplicação multiempresa; ativação no serviço somente após a troca de banco."""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import os
import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import limites
import seguranca
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from nucleo.credenciais import Cofre, CredencialInvalida
from admin.servico import Administracao
from admin.rotas import router, api


@dataclass(repr=False)
class Infra:
    panel_key: str
    panel_next: str
    pepper: str
    encryption_key: str
    database_url: str = ''
    redis_url: str = ''
    public_url: str = 'https://painel.luxbrain.com.br/gateway'
    max_body_bytes: int = 2097152

    def validar(self):
        segredos={'PANEL_SERVICE_KEY':self.panel_key,'API_KEY_PEPPER':self.pepper}
        if self.panel_next: segredos['PANEL_SERVICE_KEY_NEXT']=self.panel_next
        if seguranca.validar_segredos(segredos): raise ValueError('Chaves de infraestrutura inválidas')
        Cofre(self.encryption_key)
        seguranca.validar_formato_url(self.public_url)

    @classmethod
    def ambiente(cls):
        return cls(os.getenv('PANEL_SERVICE_KEY',''),os.getenv('PANEL_SERVICE_KEY_NEXT',''),
            os.getenv('API_KEY_PEPPER',''),os.getenv('ENCRYPTION_KEY',''),os.getenv('DATABASE_URL',''),
            os.getenv('REDIS_URL',''),os.getenv('GATEWAY_PUBLIC_URL','https://painel.luxbrain.com.br/gateway'))


def criar_app(infra,db=None,redis=None):
    infra.validar()
    filtro=seguranca.FiltroSegredos()
    for nome in ('','uvicorn','uvicorn.access','uvicorn.error','gateway'):
        logging.getLogger(nome).addFilter(filtro)
    for handler in logging.getLogger().handlers:
        handler.addFilter(filtro)
    @asynccontextmanager
    async def lifespan(app):
        proprio = db is None
        if proprio:
            app.state.db=await asyncpg.create_pool(infra.database_url,min_size=2,max_size=10)
            app.state.redis=aioredis.from_url(infra.redis_url,decode_responses=True)
            preparar(app)
        try: yield
        finally:
            if proprio:
                await app.state.db.close()
                await app.state.redis.aclose()

    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.infra=infra
    app.state.db=db
    app.state.redis=redis
    def preparar(app):
        app.state.cofre=Cofre(infra.encryption_key)
        app.state.configuracoes=Configuracoes(Repositorio(app.state.db))
        app.state.admin=Administracao(app.state.db,app.state.cofre,app.state.configuracoes,infra)
    preparar(app)
    app.add_middleware(limites.LimiteCorpo,max_bytes=infra.max_body_bytes)

    @app.middleware('http')
    async def protecoes(request,call_next):
        if seguranca.rota_interna(request.url.path) and not seguranca.chamada_interna(
                request.client.host if request.client else None,request.headers):
            return JSONResponse({'detail':'Not Found'},status_code=404)
        grupo=request.url.path.split('/',2)[1]
        if grupo in ('api','webhooks'):
            try:
                permitido=await limites.permitir(app.state.redis,grupo,limites.ip_cliente(
                    request.client.host if request.client else None,request.headers))
            except aioredis.RedisError:
                return JSONResponse({'detail':'Serviço indisponível'},status_code=503)
            if not permitido: return JSONResponse({'detail':'Limite excedido'},status_code=429,headers={'Retry-After':'1'})
        r=await call_next(request)
        r.headers['X-Content-Type-Options']='nosniff';r.headers['Cache-Control']='no-store'
        return r

    def erro(status,codigo):
        return JSONResponse({'erro':{'codigo':codigo,'mensagem':codigo,'detalhes':{}}},status_code=status)
    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        return erro(exc.status_code,exc.detail if isinstance(exc.detail,str) else 'REQUEST_ERROR')
    @app.exception_handler(RequestValidationError)
    async def validacao(request,exc): return erro(400,'VALIDATION_ERROR')
    @app.exception_handler(ValueError)
    async def invalido(request,exc): return erro(400,'VALIDATION_ERROR')
    @app.exception_handler(asyncpg.PostgresError)
    async def banco_error(request,exc):
        return erro(409,'CONFLICT') if isinstance(exc,asyncpg.IntegrityConstraintViolationError) else erro(503,'DATABASE_ERROR')
    @app.exception_handler(CredencialInvalida)
    async def segredo_error(request,exc): return erro(503,'CREDENTIAL_ERROR')

    @app.get('/health')
    async def health():
        await app.state.db.fetchval('SELECT 1'); await app.state.redis.ping()
        return {'ok':True}

    app.include_router(router);app.include_router(api)
    return app
