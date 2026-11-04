import json
from fastapi import APIRouter, Depends, HTTPException, Request
from nucleo.autenticacao import painel, contexto_da_chave
from nucleo.banco import Repositorio, registro
from .servico import ENTIDADES, limpar, auditar
from starlette.responses import JSONResponse

router = APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
api = APIRouter(prefix='/api/v1')


async def barrar_so_plataforma(request,tenant_id,dados,campos,actor,alvo):
    """Campos só da plataforma num corpo vindo de admin ou membro: o pedido inteiro é recusado (403, nada é aplicado) e a tentativa
    fica na auditoria, com os campos e o papel. Devolve a resposta de recusa, ou None (segue)."""
    from nucleo.autenticacao import papel
    quem=papel(request)
    if quem=='plataforma' or not isinstance(dados,dict): return None
    achados=sorted(c for c in dados if c in campos)
    if not achados: return None
    if await Repositorio(request.app.state.db).tenant(tenant_id):
        async with request.app.state.db.acquire() as conn,conn.transaction():
            await auditar(conn,tenant_id,actor or 'painel','acesso.campo_so_plataforma',alvo,{'campos':achados,'papel':quem})
    return JSONResponse({'erro':{'codigo':'FORBIDDEN','mensagem':'Estes campos só podem ser alterados pela plataforma.',
                                 'detalhes':{'motivo':'so_plataforma','campos':achados}}},status_code=403,
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def pagina(itens,limite):
    return {'itens':itens[:limite],'proximo_cursor':str(itens[limite-1]['id']) if len(itens)>limite else None}


@router.get('/tenants')
async def tenants(request:Request,limite:int=50,cursor:str|None=None):
    if not 1<=limite<=100: raise HTTPException(400,'Limite inválido')
    # Catálogo global restrito à chave de serviço do painel.
    # MULTI1-a: quem não é plataforma e manda a empresa da sessão vê só a própria.
    from nucleo.autenticacao import papel
    from nucleo.escopo_empresa import do_pedido
    so=do_pedido(request) if papel(request)!='plataforma' else None
    rows=await request.app.state.db.fetch('SELECT * FROM tenants WHERE ($1::text IS NULL OR id>$1) AND ($3::text IS NULL OR id=$3) ORDER BY id LIMIT $2',cursor,limite+1,so)
    return pagina([dict(r) for r in rows],limite)


@router.post('/tenants',status_code=201)
async def criar_tenant(request:Request,dados:dict,actor=Depends(painel)):
    return await request.app.state.admin.salvar_tenant(dados,actor)


@router.get('/tenants/{tenant_id}')
async def tenant(request:Request,tenant_id:str):
    row=await Repositorio(request.app.state.db).tenant(tenant_id)
    if not row: raise HTTPException(404,'TENANT_NOT_FOUND')
    return row


# PAPEL1-a: o gestor muda nome e alerta da equipe; o resto do cadastro da empresa é da plataforma.
TECNICOS_DA_EMPRESA=('status','plano','limite_turnos_mes','retencao_dias','tenant_teste','timezone','id')


@router.patch('/tenants/{tenant_id}')
async def alterar_tenant(request:Request,tenant_id:str,dados:dict,actor=Depends(painel)):
    recusa=await barrar_so_plataforma(request,tenant_id,dados,TECNICOS_DA_EMPRESA,actor,'empresa')
    if recusa: return recusa
    return await request.app.state.admin.salvar_tenant(dados,actor,tenant_id)


@router.get('/tenants/{tenant_id}/config')
async def config(request:Request,tenant_id:str):
    await tenant(request,tenant_id)
    return await Repositorio(request.app.state.db).obter(tenant_id,'tenant_config',tenant_id)


@router.put('/tenants/{tenant_id}/config')
async def salvar_config(request:Request,tenant_id:str,dados:dict,actor=Depends(painel)):
    from nucleo.atendimento import SO_PLATAFORMA
    recusa=await barrar_so_plataforma(request,tenant_id,dados,SO_PLATAFORMA,actor,'config')
    if recusa: return recusa
    return await request.app.state.admin.salvar_config(tenant_id,dados,actor)


@router.get('/tenants/{tenant_id}/auditoria')
async def auditoria(request:Request,tenant_id:str,limite:int=50,cursor:str|None=None):
    await tenant(request,tenant_id)
    return {'itens':await Repositorio(request.app.state.db).listar(tenant_id,'audit_log',limite,cursor)}


@router.get('/tenants/{tenant_id}/{entidade}')
async def listar(request:Request,tenant_id:str,entidade:str,limite:int=50,cursor:str|None=None):
    if entidade not in ENTIDADES: raise HTTPException(404,'NOT_FOUND')
    await tenant(request,tenant_id)
    # Repo limita a 100; buscar limite+1 com teto 100.
    if not 1<=limite<=99: raise HTTPException(400,'Limite deve ser 1–99')
    if entidade=='agentes':
        rows=await request.app.state.db.fetch("""SELECT a.*,
            CASE WHEN c.aberto_ate>now() THEN 'aberto' WHEN c.aberto_ate IS NOT NULL THEN 'meio_aberto' ELSE 'fechado' END AS circuito_estado,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('id',ch.id,'tipo',ch.tipo,'ativo',ch.ativo) ORDER BY ch.id)
                FROM tenant_canais ch WHERE ch.tenant_id=a.tenant_id AND ch.agente_id=a.id AND ch.excluido_em IS NULL),'[]'::jsonb) AS canais
            FROM agentes a LEFT JOIN agente_circuitos c ON c.tenant_id=a.tenant_id AND c.agente_id=a.id
            WHERE a.tenant_id=$1 AND ($2::text IS NULL OR a.id>$2) ORDER BY a.id LIMIT $3""",tenant_id,cursor,limite+1)
        rows=[{**registro(r),'canais':json.loads(r['canais']) if isinstance(r['canais'],str) else r['canais']} for r in rows]
        # MULTI1-a: módulos que cada agente usa de fato (para o painel).
        for r in rows: r['modulos']=(await request.app.state.configuracoes.efetiva(tenant_id,r['id']))['modulos']
    elif entidade=='canais':
        # CANAL-EXC: canal excluído (marcado porque sobrou pedido) não aparece mais.
        rows=[registro(r) for r in await request.app.state.db.fetch("""SELECT * FROM tenant_canais WHERE tenant_id=$1 AND excluido_em IS NULL
            AND ($2::text IS NULL OR id::text>$2) ORDER BY id::text LIMIT $3""",tenant_id,cursor,limite+1)]
    else:
        rows=await Repositorio(request.app.state.db).listar(tenant_id,ENTIDADES[entidade][0],limite+1,cursor)
    return pagina(limpar(rows),limite)


@router.post('/tenants/{tenant_id}/{entidade}',status_code=201)
async def criar(request:Request,tenant_id:str,entidade:str,dados:dict,actor=Depends(painel)):
    if entidade not in ENTIDADES: raise HTTPException(404,'NOT_FOUND')
    if entidade=='agentes':
        # O agente novo nasce com o modelo escolhido; o resto do que é só da plataforma, não.
        from nucleo.atendimento import SO_PLATAFORMA_AGENTE
        campos=tuple(c for c in SO_PLATAFORMA_AGENTE if c not in ('modelo_id','modelo_versao'))
        recusa=await barrar_so_plataforma(request,tenant_id,dados,campos,actor,'agente:novo')
        if recusa: return recusa
    result=await request.app.state.admin.salvar(tenant_id,entidade,dados,actor)
    # MULTI1-a: canal uazapi criado com credenciais → o gateway cadastra o webhook na instância.
    if entidade=='canais' and result.get('tipo')=='uazapi' and isinstance(dados,dict) and dados.get('credenciais') and request.app.state.infra.registrar_webhook_uazapi:
        from canais.webhook_instancia import registrar
        from urllib.parse import parse_qs,urlsplit
        segredo=parse_qs(urlsplit(result['webhook_url']).query).get('s',[None])[0] if result.get('webhook_url') else None
        result['webhook_uazapi']=await registrar(request.app.state,tenant_id,result['id'],actor,segredo)
    return result


@router.get('/tenants/{tenant_id}/{entidade}/{identidade}')
async def obter(request:Request,tenant_id:str,entidade:str,identidade:str):
    if entidade not in ENTIDADES: raise HTTPException(404,'NOT_FOUND')
    row=await Repositorio(request.app.state.db).obter(tenant_id,ENTIDADES[entidade][0],identidade)
    if not row or row.get('excluido_em'): raise HTTPException(404,'NOT_FOUND')
    result=limpar(row)
    if entidade=='agentes':
        result['configuracao_efetiva']=await request.app.state.configuracoes.efetiva(tenant_id,identidade)
        result['modulos']=result['configuracao_efetiva']['modulos']
    return result


@router.patch('/tenants/{tenant_id}/{entidade}/{identidade}')
async def alterar(request:Request,tenant_id:str,entidade:str,identidade:str,dados:dict,actor=Depends(painel)):
    if entidade not in ENTIDADES: raise HTTPException(404,'NOT_FOUND')
    if entidade=='agentes' and isinstance(dados,dict):
        from nucleo.autenticacao import pode
        from nucleo.atendimento import SO_PLATAFORMA_AGENTE
        # Envio, piloto, alerta, fluxo e credencial do Skyone, provedor, instruções, limites e modelo: só a plataforma (pedido todo recusado).
        recusa=await barrar_so_plataforma(request,tenant_id,dados,SO_PLATAFORMA_AGENTE,actor,'agente:'+identidade)
        if recusa: return recusa
        # MULTI1-a: ligar e desligar módulos no agente, de admin ou plataforma.
        if 'modulos_ligados' in dados and not pode(request,'gestor'): raise HTTPException(403,'FORBIDDEN')
    if entidade=='canais' and await request.app.state.db.fetchval('SELECT excluido_em IS NOT NULL FROM tenant_canais WHERE tenant_id=$1 AND id=$2',tenant_id,identidade):
        raise HTTPException(404,'NOT_FOUND')
    result=await request.app.state.admin.salvar(tenant_id,entidade,dados,actor,identidade)
    # MULTI1-a: credenciais novas num canal uazapi → cadastra de novo o webhook (pode ser outra instância).
    if entidade=='canais' and result.get('tipo')=='uazapi' and isinstance(dados,dict) and dados.get('credenciais') and request.app.state.infra.registrar_webhook_uazapi:
        from canais.webhook_instancia import registrar
        result['webhook_uazapi']=await registrar(request.app.state,tenant_id,identidade,actor)
    return result


@router.delete('/tenants/{tenant_id}/api-keys/{identidade}')
async def revogar(request:Request,tenant_id:str,identidade:str,actor=Depends(painel)):
    async with request.app.state.db.acquire() as conn, conn.transaction():
        result=await conn.fetchrow('UPDATE tenant_api_keys SET revogada_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING id',tenant_id,identidade)
        if not result: raise HTTPException(404,'NOT_FOUND')
        await auditar(conn,tenant_id,actor,'api-keys.revogada',identidade)
    return {'ok':True}


@api.get('/conversas/{conversation_id}/mensagens')
async def mensagens(request:Request,conversation_id:str,antes:int|None=None,limite:int=100,ctx=Depends(contexto_da_chave('eventos:ler'))):
    repo=Repositorio(request.app.state.db)
    conv=await repo.obter(ctx['tenant_id'],'conversas',conversation_id)
    if not conv or (ctx['agente_id'] and conv['agente_id']!=ctx['agente_id']): raise HTTPException(404,'NOT_FOUND')
    if not 1<=limite<=100: raise HTTPException(400,{'campo':'limite'})
    if antes is not None and not 1<=antes<=9223372036854775807: raise HTTPException(400,{'campo':'antes'})
    rows=await request.app.state.db.fetch('SELECT id,direcao,origem,conteudo,criado_em FROM mensagens '
        'WHERE tenant_id=$1 AND conversation_id=$2 AND ($3::bigint IS NULL OR id<$3) ORDER BY id DESC LIMIT $4',ctx['tenant_id'],conversation_id,antes,limite+1)
    return {'itens':[dict(r) for r in reversed(rows[:limite])],'tem_mais':len(rows)>limite}
