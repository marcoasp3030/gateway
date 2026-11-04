"""Aplicação multiempresa; ativação no serviço somente após a troca de banco."""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import os
import re
import asyncio
import httpx
import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from erros_api import resposta as resposta_erro, resposta_integridade, campo_validacao, mensagem_validacao, CAMPOS_LEGADOS
from fastapi.responses import JSONResponse
import migrar
import limites
import seguranca
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from nucleo.credenciais import Cofre, CredencialInvalida
from admin.servico import Administracao, auditar
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
    # CHAVES1: chave da sessão de plataforma do painel (e a próxima, na rotação). Vazia = só a chave de empresa existe (fase A).
    panel_platform_key: str = ''
    panel_platform_next: str = ''

    def validar(self):
        segredos={'PANEL_SERVICE_KEY':self.panel_key,'API_KEY_PEPPER':self.pepper}
        if self.panel_next: segredos['PANEL_SERVICE_KEY_NEXT']=self.panel_next
        if self.panel_platform_key: segredos['PANEL_PLATFORM_KEY']=self.panel_platform_key
        if self.panel_platform_next: segredos['PANEL_PLATFORM_KEY_NEXT']=self.panel_platform_next
        if seguranca.validar_segredos(segredos): raise ValueError('Chaves de infraestrutura inválidas')
        # A chave de plataforma não pode ser igual a nenhuma chave de empresa (senão o papel volta a ser só um cabeçalho).
        if {k for k in (self.panel_platform_key,self.panel_platform_next) if k} & {k for k in (self.panel_key,self.panel_next) if k}:
            raise ValueError('PANEL_PLATFORM_KEY igual a uma chave de empresa')
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
            concorrencia_por_worker=int(os.getenv('CONCORRENCIA_POR_WORKER','100')),
            registrar_webhook_uazapi=os.getenv('REGISTRAR_WEBHOOK_UAZAPI','on').strip().lower() in ('on','1','sim','true'),
            panel_platform_key=os.getenv('PANEL_PLATFORM_KEY',''),panel_platform_next=os.getenv('PANEL_PLATFORM_KEY_NEXT',''))


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
        # Publicar ou mudar configuração aqui limpa também o cache dos workers e do agendador (Redis), não só o deste processo.
        from nucleo.configuracao import avisar_pelo_redis
        app.state.configuracoes.aviso=avisar_pelo_redis(lambda: app.state.redis,lambda: app.state.pipeline.prefixo)
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

    _negados={}

    def _recusa(motivo,mensagem,**detalhes):
        return JSONResponse({'erro':{'codigo':'FORBIDDEN','mensagem':mensagem,'detalhes':{'motivo':motivo,**detalhes}}},status_code=403,
                            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    async def _auditar_negado(request,quem,rota,motivo):
        """acesso.negado na auditoria: no máximo um por minuto por sessão, rota e motivo. Sem ids nem valores."""
        import time
        actor=(request.headers.get('x-actor') or 'painel')[:200];chave=(actor,request.method,rota,motivo);agora=time.monotonic()
        if agora-_negados.get(chave,0)<60: return
        _negados[chave]=agora
        try:
            m=re.match(r'^/admin/v1/tenants/([^/]+)',request.url.path);tenant=m.group(1) if m else None
            async with app.state.db.acquire() as conn,conn.transaction():
                if tenant and not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM tenants WHERE id=$1)',tenant): tenant=None
                await auditar(conn,tenant,actor,'acesso.negado',rota[:200],{'rota':f'{request.method} {rota}','papel':quem or request.headers.get('x-role','')[:20],'motivo':motivo})
        except Exception: logging.getLogger('gateway.auth').warning('Recusa de acesso não registrada na auditoria')

    async def _papel_e_rota(request,quem,tipo):
        """PAPEL1-a. Chave que não confere segue para a rota (que devolve 401). Devolve a resposta de recusa, ou None."""
        if tipo is None: return None
        from starlette.routing import Match
        from nucleo import permissoes
        rota=None;parametros={}
        for r in app.router.routes:
            casou,filho=r.matches(request.scope)
            if casou==Match.FULL: rota=getattr(r,'path',None);parametros=filho.get('path_params',{});break
        rota_txt=(rota or request.url.path)[len('/admin/v1'):] if (rota or request.url.path).startswith('/admin/v1') else (rota or request.url.path)
        if '{entidade}' in rota_txt and parametros.get('entidade') in permissoes.ENTIDADES: rota_txt=rota_txt.replace('{entidade}',parametros['entidade'])
        if quem is None:
            await _auditar_negado(request,None,rota_txt,'papel_desconhecido')
            return _recusa('papel_desconhecido','Papel desconhecido.')
        if quem=='plataforma' and tipo!='plataforma':
            # CHAVES1: na fase A a chave de empresa com papel de plataforma ainda vale, com aviso; com a exigência ligada, 403.
            if os.getenv('CHAVE_PLATAFORMA_OBRIGATORIA','off').strip().lower() in ('on','1','sim','true'):
                await _auditar_negado(request,quem,rota_txt,'papel_sem_credencial')
                try:
                    from nucleo.alertas import Alertas
                    await Alertas(app.state.redis,app.state.http,app.state.db,app.state.pipeline.prefixo).disparar('painel_papel_plataforma','aviso',
                        'papel de plataforma com a chave de empresa',f'{request.method} {rota_txt}')
                except Exception: pass
                return _recusa('papel_sem_credencial','O papel de plataforma exige a chave de plataforma.')
            logging.getLogger('gateway.auth').warning('Papel plataforma com a chave de empresa (fase A): %s %s',request.method,rota_txt)
        minimo=permissoes.minimo(request.method,rota or request.url.path,parametros)
        if not permissoes.atende(quem,minimo):
            await _auditar_negado(request,quem,rota_txt,'papel_insuficiente')
            return _recusa('papel_insuficiente','Seu papel não permite esta operação.',minimo=minimo)
        return None

    @app.middleware('http')
    async def protecoes(request,call_next):
        if seguranca.rota_interna(request.url.path) and not seguranca.chamada_interna(
                request.client.host if request.client else None,request.headers):
            return resposta_erro(404)
        # MULTI1-a: empresa da sessão do painel (X-Tenant-Scope). PAPEL1-a: papel exato, credencial e nível mínimo da rota.
        if request.url.path.startswith('/admin/v1/'):
            from nucleo import escopo_empresa
            from nucleo.autenticacao import papel as papel_do_pedido,credencial
            quem=papel_do_pedido(request)
            # Primeiro a empresa (outra empresa é 404 para qualquer papel), depois o papel e o nível da rota.
            veredito=escopo_empresa.conferir(request,quem) if credencial(request) else None
            if veredito=='sem_escopo':
                # No log e no alerta, o papel como o painel mandou (admin/membro na transição).
                rotulo=request.headers.get('x-role','')[:20]
                if escopo_empresa.registrar_sem_escopo(request,rotulo):
                    try:
                        from nucleo.alertas import Alertas
                        await Alertas(app.state.redis,app.state.http,app.state.db,app.state.pipeline.prefixo).disparar('painel_sem_escopo','aviso',
                            'painel sem a empresa da sessão',f'Chamada de papel "{rotulo}" sem X-Tenant-Scope: {request.method} {escopo_empresa.rota_sem_ids(request.url.path)}')
                    except Exception: pass
            elif veredito is not None: return resposta_erro(veredito[0])
            recusa=await _papel_e_rota(request,quem,credencial(request))
            if recusa is not None: return recusa
        grupo=request.url.path.split('/',2)[1]
        if grupo in ('api','webhooks'):
            try:
                permitido=await limites.permitir(app.state.redis,grupo,limites.ip_cliente(
                    request.client.host if request.client else None,request.headers),request.url.path,request.headers)
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
        detalhe=exc.detail if isinstance(exc.detail,dict) else {}
        return resposta_erro(exc.status_code,codigo,campo,exc.headers,detalhe.get('motivo'),detalhe.get('mensagem'))
    @app.exception_handler(RequestValidationError)
    async def validacao(request,exc): return resposta_erro(400,campo=campo_validacao(exc.errors()),mensagem=mensagem_validacao(exc.errors()))
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
    from admin.respostas_rapidas import router as respostas_rapidas
    app.include_router(respostas_rapidas)
    from admin.notas import router as notas
    app.include_router(notas)
    from admin.etiquetas import router as etiquetas
    app.include_router(etiquetas)
    from admin.operacao import router as operacao
    app.include_router(operacao)
    from admin.follow_up import router as follow_ups
    app.include_router(follow_ups)
    # ARQ1-a1: envio de documento (API pública com chave) e o painel dos documentos.
    from canais.documentos import router as documentos_api
    from admin.documentos import router as documentos_admin
    app.include_router(documentos_api);app.include_router(documentos_admin)
    from admin.atendimento import router as atendimento
    app.include_router(atendimento)
    from admin.solicitacoes import router as solicitacoes
    app.include_router(solicitacoes)
    app.include_router(contatos)
    from admin.canal_excluir import router as canal_excluir
    app.include_router(canal_excluir)
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
    # Cardápio interativo: rotas públicas do link (sem chave) e as do painel que geram o link.
    from modulos.pedidos.cardapio import router as cardapio_interativo
    app.include_router(cardapio_interativo)
    from modulos.pedidos.aparencia import router as aparencia_do_cardapio
    app.include_router(aparencia_do_cardapio)
    from modulos.pedidos.redes import router as redes_do_cardapio
    app.include_router(redes_do_cardapio)
    app.include_router(canais_router);app.include_router(router);app.include_router(api)
    return app
