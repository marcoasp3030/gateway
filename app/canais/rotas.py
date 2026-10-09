import json
from datetime import datetime,timezone
from fastapi import APIRouter,Request,HTTPException,Depends
from pydantic import BaseModel,Field,AwareDatetime,ConfigDict
from seguranca import segredo_confere
from nucleo.autenticacao import contexto_da_chave,painel
from nucleo.banco import Repositorio
from nucleo.pipeline import sanitizar
from admin.servico import auditar
from canais import uazapi

router=APIRouter()


class Entrada(BaseModel):
    model_config=ConfigDict(extra='forbid')
    identificador:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_@+.-]+$')
    texto:str=Field(min_length=1,max_length=20000)
    message_id:str|None=Field(None,max_length=255)
    enviada_em:AwareDatetime|None=None


class EntradaAPI(Entrada):
    canal_id:str


@router.post('/webhooks/{tipo}/{canal_id}')
async def webhook(request:Request,tipo:str,canal_id:str,s:str=''):
    # Só resolve identidade antes da autenticação. Todo acesso posterior usa tenant explícito.
    canal=await request.app.state.db.fetchrow('SELECT tenant_id,webhook_secret,tipo FROM tenant_canais WHERE id=$1',canal_id)
    if not canal or canal['tipo']!=tipo: raise HTTPException(404,'NOT_FOUND')
    esperado=request.app.state.cofre.abrir(canal['tenant_id'],canal['webhook_secret']) if canal['webhook_secret'] else None
    if not segredo_confere(s,esperado): raise HTTPException(401,'UNAUTHORIZED')
    if tipo not in ('api','uazapi'): raise HTTPException(501,'Provedor ainda não implementado')
    tenant=canal['tenant_id']
    canal_reg,empresa,cfg=await request.app.state.pipeline.contexto(tenant,canal_id)
    if not canal_reg['ativo'] or empresa['status']!='ativo' or cfg['status']!='ativo': return {'ok':True,'ignorado':True}
    try: bruto=await request.json()
    except ValueError: raise HTTPException(400,'JSON inválido') from None
    if not isinstance(bruto,dict): raise HTTPException(400,'Objeto JSON obrigatório')
    raw_id=await request.app.state.db.fetchval('INSERT INTO webhook_raw(tenant_id,fonte,payload) VALUES ($1,$2,$3::jsonb) RETURNING id',
        tenant,tipo,json.dumps(sanitizar(bruto)))
    if tipo=='api':
        m=Entrada.model_validate(bruto).model_dump()
    else:
        try: norm=uazapi.normalizar(bruto)
        except (TypeError,AttributeError,ValueError): raise HTTPException(400,'Payload inválido') from None
        if norm is None: return {'ok':True,'ignorado':True}
        m=Entrada.model_validate({'identificador':norm['telefone'],
            'texto':norm['texto'] or '[mídia ainda não interpretada]', 'message_id':norm['message_id']}).model_dump()
        m.update(humano=norm['from_me'],via_api=norm['via_api'],nome=norm['nome'])
    result=await request.app.state.pipeline.receber(tenant,canal_id,m)
    await request.app.state.db.execute('UPDATE webhook_raw SET contato_id=(SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$3) WHERE tenant_id=$1 AND id=$2',tenant,raw_id,f'{tenant}:{canal_id}:{m["identificador"]}')
    return result


@router.post('/api/v1/conversas/mensagens')
async def mensagem_api(request:Request,dados:EntradaAPI,ctx=Depends(contexto_da_chave('conversar'))):
    canal=await Repositorio(request.app.state.db).obter(ctx['tenant_id'],'tenant_canais',dados.canal_id)
    if not canal or canal['tipo']!='api' or (ctx['agente_id'] and canal['agente_id']!=ctx['agente_id']):
        raise HTTPException(404,'NOT_FOUND')
    return await request.app.state.pipeline.receber(ctx['tenant_id'],dados.canal_id,dados.model_dump(exclude={'canal_id'}))


@router.post('/admin/v1/tenants/{tenant_id}/agentes/{agente_id}/simular')
async def simular(request:Request,tenant_id:str,agente_id:str,dados:EntradaAPI,actor=Depends(painel)):
    repo=Repositorio(request.app.state.db)
    canal=await repo.obter(tenant_id,'tenant_canais',dados.canal_id)
    if not canal or canal['agente_id']!=agente_id or canal['tipo']!='api': raise HTTPException(404,'NOT_FOUND')
    cfg=await request.app.state.configuracoes.efetiva(tenant_id,agente_id)
    # Simulação aceita IA real (agente live) porque nada sai para fora com envio em teste (dry).
    if cfg['send_mode']!='dry': raise HTTPException(400,{'campo':'send_mode'})
    result=await request.app.state.pipeline.receber(tenant_id,dados.canal_id,dados.model_dump(exclude={'canal_id'}))
    async with request.app.state.db.acquire() as conn,conn.transaction():
        await auditar(conn,tenant_id,actor,'agente.simulado',agente_id)
    return result


@router.get('/admin/v1/tenants/{tenant_id}/conversas/{conversa_id}/mensagens',dependencies=[Depends(painel)])
async def mensagens_admin(request:Request,tenant_id:str,conversa_id:str,antes:int|None=None,limite:int=100):
    if not 1<=limite<=100: raise HTTPException(400,{"campo":"limite"})
    if antes is not None and not 1<=antes<=9223372036854775807: raise HTTPException(400,{"campo":"antes"})
    if not await Repositorio(request.app.state.db).obter(tenant_id,'conversas',conversa_id): raise HTTPException(404,'NOT_FOUND')
    rows=await request.app.state.db.fetch('SELECT id,conteudo,direcao,origem,enviada_em FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND ($3::bigint IS NULL OR id<$3) ORDER BY id DESC LIMIT $4',tenant_id,conversa_id,antes,limite+1)
    return {'itens':[dict(r) for r in reversed(rows[:limite])],'tem_mais':len(rows)>limite}


@router.get('/admin/v1/tenants/{tenant_id}/metricas',dependencies=[Depends(painel)])
async def metricas(request:Request,tenant_id:str,agente:str|None=None):
    repo=Repositorio(request.app.state.db)
    if not await repo.tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    if agente and not await repo.obter(tenant_id,'agentes',agente): raise HTTPException(404,'NOT_FOUND')
    from admin.fila import fila_da_empresa
    linha=await request.app.state.db.fetchrow('''SELECT count(*) AS turnos,
        count(*) FILTER (WHERE status='erro') AS erros,
        percentile_cont(0.5) WITHIN GROUP(ORDER BY total_ms) AS total_p50_ms,
        percentile_cont(0.95) WITHIN GROUP(ORDER BY total_ms) AS total_p95_ms
        FROM turnos WHERE tenant_id=$1 AND etapa='concluido' AND ($2::text IS NULL OR agente_id=$2)''',tenant_id,agente)
    # Indicadores da fila durável da empresa (zerados enquanto ela estiver desligada).
    try: fila=await fila_da_empresa(request,tenant_id)
    except HTTPException: raise
    except Exception: fila={'ligada':request.app.state.infra.fila_duravel,'indisponivel':True}
    return {**dict(linha),'fila':fila}
