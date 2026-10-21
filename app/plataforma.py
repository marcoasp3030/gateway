"""Aplicação multiempresa; ativação no serviço somente após a troca de banco."""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import os
import asyncio
import httpx
import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from erros_api import resposta as resposta_erro, resposta_integridade, campo_validacao, CAMPOS_LEGADOS
from fastapi.responses import JSONResponse
import migrar
import limites
import seguranca
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from nucleo.credenciais import Cofre, CredencialInvalida
from admin.servico import Administracao
from admin.rotas import router, api
from canais.rotas import router as canais_router
from nucleo.pipeline import Pipeline
from nucleo.fila import FilaDuravel, ligada_no_ambiente


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
    skyone_rpm: int = 240
    fila_duravel: bool = False
    concorrencia_por_worker: int = 20
    # MULTI1-a: cadastrar sozinho o webhook na instância uazapi ao criar o canal, trocar credenciais e rotacionar o segredo.
    registrar_webhook_uazapi: bool = False

    def validar(self):
        segredos={'PANEL_SERVICE_KEY':self.panel_key,'API_KEY_PEPPER':self.pepper}
        if self.panel_next: segredos['PANEL_SERVICE_KEY_NEXT']=self.panel_next
        if seguranca.validar_segredos(segredos): raise ValueError('Chaves de infraestrutura inválidas')
        if not 1<=self.skyone_rpm<=100000: raise ValueError("SKYONE_REQUESTS_PER_MINUTE inválido")
        if not 1<=self.concorrencia_por_worker<=200: raise ValueError("CONCORRENCIA_POR_WORKER inválido")
        Cofre(self.encryption_key)
        seguranca.validar_formato_url(self.public_url)

    @classmethod
    def ambiente(cls):
        return cls(os.getenv('PANEL_SERVICE_KEY',''),os.getenv('PANEL_SERVICE_KEY_NEXT',''),
            os.getenv('API_KEY_PEPPER',''),os.getenv('ENCRYPTION_KEY',''),os.getenv('DATABASE_URL',''),
            os.getenv('REDIS_URL',''),os.getenv('GATEWAY_PUBLIC_URL','https://painel.luxbrain.com.br/gateway'),
            skyone_rpm=int(os.getenv('SKYONE_REQUESTS_PER_MINUTE','240')),
            fila_duravel=ligada_no_ambiente(),
            concorrencia_por_worker=int(os.getenv('CONCORRENCIA_POR_WORKER','20')),
            registrar_webhook_uazapi=os.getenv('REGISTRAR_WEBHOOK_UAZAPI','on').strip().lower() in ('on','1','sim','true'))


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
        agendador = None
        try:
            if proprio:
                app.state.db=await asyncpg.create_pool(infra.database_url,min_size=2,max_size=10)
                app.state.redis=aioredis.from_url(infra.redis_url,decode_responses=True)
                preparar(app)
            # Migrações nunca são automáticas.
            versao=await app.state.db.fetchval('SELECT max(versao) FROM schema_migrations')
            if versao != len(migrar.arquivos()):
                raise RuntimeError('Schema incompatível: aplique as migrações explicitamente')
            await app.state.pipeline.recuperar()
            # Exclusividade: com a fila durável ligada, quem processa são os workers.
            if not infra.fila_duravel:
                agendador=asyncio.create_task(app.state.pipeline.agendador())
            yield
        finally:
            if agendador:
                agendador.cancel()
                await asyncio.gather(agendador,return_exceptions=True)
            if hasattr(app.state,'pipeline'): await app.state.pipeline.fechar()
            if hasattr(app.state,'http'): await app.state.http.aclose()
            if proprio:
                if app.state.db is not None: await app.state.db.close()
                if app.state.redis is not None: await app.state.redis.aclose()

    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.infra=infra
    app.state.db=db
    app.state.redis=redis
    def preparar(app):
        app.state.cofre=Cofre(infra.encryption_key)
        app.state.configuracoes=Configuracoes(Repositorio(app.state.db))
        app.state.admin=Administracao(app.state.db,app.state.cofre,app.state.configuracoes,infra)
        app.state.http=httpx.AsyncClient(follow_redirects=False)
        app.state.pipeline=Pipeline(app.state.db,app.state.redis,app.state.configuracoes,app.state.cofre,app.state.http,
                                    skyone_rpm=infra.skyone_rpm,fila_duravel=infra.fila_duravel)
        app.state.fila=FilaDuravel(app.state.pipeline,infra.fila_duravel,infra.concorrencia_por_worker)
    if db is not None: preparar(app)
    # PED2: por dentro do limite de corpo (só vê o que já passou por ele); registra as chamadas de Skill.
    from nucleo.skill_registro import RegistroDeSkills
    app.add_middleware(RegistroDeSkills)
    app.add_middleware(limites.LimiteCorpo,max_bytes=infra.max_body_bytes)

    @app.middleware('http')
    async def protecoes(request,call_next):
        if seguranca.rota_interna(request.url.path) and not seguranca.chamada_interna(
                request.client.host if request.client else None,request.headers):
            return resposta_erro(404)
        # MULTI1-a: empresa da sessão do painel (X-Tenant-Scope).
        if request.url.path.startswith('/admin/v1/'):
            from nucleo import escopo_empresa
            from nucleo.autenticacao import papel as papel_do_pedido
            quem=papel_do_pedido(request);veredito=escopo_empresa.conferir(request,quem)
            if veredito=='sem_escopo':
                if escopo_empresa.registrar_sem_escopo(request,quem):
                    try:
                        from nucleo.alertas import Alertas
                        await Alertas(app.state.redis,app.state.http,app.state.db,app.state.pipeline.prefixo).disparar('painel_sem_escopo','aviso',
                            'painel sem a empresa da sessão',f'Chamada de papel "{quem}" sem X-Tenant-Scope: {request.method} {escopo_empresa.rota_sem_ids(request.url.path)}')
                    except Exception: pass
            elif veredito is not None: return resposta_erro(veredito[0])
        grupo=request.url.path.split('/',2)[1]
        if grupo in ('api','webhooks'):
            try:
                permitido=await limites.permitir(app.state.redis,grupo,limites.ip_cliente(
                    request.client.host if request.client else None,request.headers))
            except aioredis.RedisError:
                return resposta_erro(503)
            if not permitido: return resposta_erro(429,headers={'Retry-After':'1'})
        r=await call_next(request)
        r.headers['X-Content-Type-Options']='nosniff';r.headers['Cache-Control']='no-store'
        return r

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request,exc):
        codigo='CREDENTIAL_REJECTED' if exc.status_code==502 and exc.detail=='credencial do Skyone recusada' else None
        campo=exc.detail.get('campo') if isinstance(exc.detail,dict) else None
        if isinstance(exc.detail,str): campo=CAMPOS_LEGADOS.get(exc.detail,campo)
        return resposta_erro(exc.status_code,codigo,campo,exc.headers,exc.detail.get('motivo') if isinstance(exc.detail,dict) else None)
    @app.exception_handler(RequestValidationError)
    async def validacao(request,exc): return resposta_erro(400,campo=campo_validacao(exc.errors()))
    @app.exception_handler(ValueError)
    async def invalido(request,exc): return resposta_erro(400)
    @app.exception_handler(asyncpg.PostgresError)
    async def banco_error(request,exc):
        if isinstance(exc,asyncpg.IntegrityConstraintViolationError): return resposta_integridade(exc)
        return resposta_erro(503)
    @app.exception_handler(CredencialInvalida)
    async def segredo_error(request,exc): return resposta_erro(503)
    @app.exception_handler(aioredis.RedisError)
    async def redis_error(request,exc): return resposta_erro(503)

    @app.get('/health')
    async def health(request:Request):
        await app.state.db.fetchval('SELECT 1'); await app.state.redis.ping()
        # Pelo proxy público só {"ok":true} (SEG08); chamada interna recebe o resumo da fila.
        if not seguranca.chamada_interna(request.client.host if request.client else None,request.headers):
            return {'ok':True}
        try: fila=app.state.fila.resumo(await app.state.fila.indicadores())
        except Exception: fila={'ligada':infra.fila_duravel,'indisponivel':True}
        return {'ok':True,'fila':fila}

    @app.get('/health/ops')
    async def health_ops(request:Request):
        """Operação em ordem? (OPS1) Público: só ok, com 200 ou 503, sem dizer por quê.
        Fica 503 se não há backup recente, se o backup não foi para o destino externo, se o último teste de
        restauração falhou ou se o agendador está sem sinal. Chamada interna recebe os motivos."""
        from nucleo.alertas import situacao_ops
        try: motivos=await situacao_ops(app.state.db,app.state.redis,app.state.pipeline.prefixo)
        except Exception: motivos=['indisponivel']
        corpo={'ok':not motivos}
        if seguranca.chamada_interna(request.client.host if request.client else None,request.headers): corpo['motivos']=motivos
        return JSONResponse(corpo,status_code=200 if not motivos else 503,headers={'Cache-Control':'no-store'})

    from admin.capacidades import router as capacidades
    from admin.saidas import router as saidas
    from admin.notificacoes import router as notificacoes
    from admin.unidades import router as unidades
    from admin.contatos import router as contatos
    from admin.gestao import router as gestao
    from agentes.controle import router as circuito
    from admin.fila import router as fila
    app.include_router(circuito)
    app.include_router(fila)
    app.include_router(gestao)
    from admin.atendimentos import router as atendimentos
    app.include_router(atendimentos)
    # ARQ1-a1: envio de documento (API pública com chave) e o painel dos documentos.
    from canais.documentos import router as documentos_api
    from admin.documentos import router as documentos_admin
    app.include_router(documentos_api);app.include_router(documentos_admin)
    from admin.atendimento import router as atendimento
    app.include_router(atendimento)
    from admin.solicitacoes import router as solicitacoes
    app.include_router(solicitacoes)
    app.include_router(contatos)
    app.include_router(unidades)
    from admin.entrega import router as entrega
    app.include_router(entrega)
    from admin.alteracoes import router as alteracoes
    app.include_router(alteracoes)
    from modulos.unidades import router as info_unidades
    app.include_router(info_unidades)
    app.include_router(notificacoes)
    app.include_router(saidas)
    app.include_router(capacidades)
    # MULTI1-b: rotas de cada módulo do registro (Skills em `router`, painel em `admin`), na ordem do registro.
    import importlib
    from modulos import REGISTRO
    for nome_do_modulo in REGISTRO:
        rotas_do_modulo=importlib.import_module(f'modulos.{nome_do_modulo}.rotas')
        app.include_router(rotas_do_modulo.router);app.include_router(rotas_do_modulo.admin)
    app.include_router(canais_router);app.include_router(router);app.include_router(api)
    return app
