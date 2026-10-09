import json
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import Field
from .modelos import Estrito
from .servico import auditar,validar
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from modulos import REGISTRO
router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])

class ConfigModulo(Estrito):
    ativo:bool
    config:dict=Field(default_factory=dict)

async def empresa(db,t):
    if not await Repositorio(db).tenant(t): raise HTTPException(404,'TENANT_NOT_FOUND')

@router.get('/modulos')
async def modulos():
    return {'itens':[{'nome':m.nome,'migracoes':m.migracoes,'rotas':m.rotas,'escopos':m.escopos} for m in REGISTRO.values()]}

@router.get('/tenants/{tenant_id}/modulos')
async def modulos_tenant(request:Request,tenant_id:str):
    await empresa(request.app.state.db,tenant_id)
    return {'itens':await Repositorio(request.app.state.db).listar(tenant_id,'modulos_tenant',100)}

@router.put('/tenants/{tenant_id}/modulos/{modulo}')
async def salvar_modulo(request:Request,tenant_id:str,modulo:str,dados:dict,actor=Depends(painel)):
    if modulo not in REGISTRO: raise HTTPException(404,'NOT_FOUND')
    d=validar(ConfigModulo,dados)
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,tenant_id)
        await c.execute('INSERT INTO modulos_tenant(tenant_id,modulo,ativo,config) VALUES ($1,$2,$3,$4::jsonb) ON CONFLICT(tenant_id,modulo) DO UPDATE SET ativo=EXCLUDED.ativo,config=EXCLUDED.config',tenant_id,modulo,d['ativo'],json.dumps(d.get('config',{})))
        await auditar(c,tenant_id,actor,'modulo.alterado',modulo)
    request.app.state.configuracoes.invalidar(tenant_id)
    return {'ok':True}

from typing import Literal
from .modelos import Comportamento
from nucleo.banco import registro

class Modelo(Estrito):
    id:str|None=Field(None,pattern=r'^[a-z0-9_]{3,80}$')
    tenant_id:str|None=None
    nome:str|None=Field(None,min_length=1,max_length=200)
    tipo:Literal['atendimento','interno']|None=None
    modulos:list[str]|None=None
    comportamento_padrao:dict|None=None
    desfechos:list[str]|None=None
    followups_padrao:list[dict]|None=None
    skyone_fluxo_modelo:str|None=None

@router.get('/modelos-agente')
async def modelos(request:Request,tenant:str|None=None):
    if tenant: await empresa(request.app.state.db,tenant)
    rows=await request.app.state.db.fetch('SELECT * FROM modelos_agente WHERE tenant_id IS NULL OR tenant_id=$1 ORDER BY id,versao',tenant)
    return {'itens':[registro(r) for r in rows]}

@router.get('/modelos-agente/{modelo_id}')
async def modelo(request:Request,modelo_id:str,tenant:str|None=None,versao:int|None=None):
    if tenant: await empresa(request.app.state.db,tenant)
    row=await request.app.state.db.fetchrow('SELECT * FROM modelos_agente WHERE id=$1 AND (tenant_id IS NULL OR tenant_id=$2) AND ($3::int IS NULL OR versao=$3) ORDER BY versao DESC LIMIT 1',modelo_id,tenant,versao)
    if not row: raise HTTPException(404,'NOT_FOUND')
    return registro(row)

async def gravar_modelo(request,dados,actor,identidade=None,tenant=None):
    d=validar(Modelo,dados)
    if identidade and any(k in d for k in ('id','tenant_id')): raise HTTPException(400,'Escopo/ID imutáveis')
    mid=identidade or d.pop('id',None)
    if not mid: raise HTTPException(400,'ID obrigatório')
    async with request.app.state.db.acquire() as c,c.transaction():
        await c.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','modelo:'+mid)
        atual=await c.fetchrow('SELECT * FROM modelos_agente WHERE id=$1 ORDER BY versao DESC LIMIT 1',mid)
        if identidade:
            if not atual or (atual['tenant_id'] is not None and atual['tenant_id']!=tenant): raise HTTPException(404,'NOT_FOUND')
            final=registro(atual);final.update(d);final['versao']+=1
        else:
            if atual: raise HTTPException(409,'CONFLICT')
            final={'tenant_id':None,'tipo':'atendimento','modulos':[],'comportamento_padrao':{},'desfechos':[],'followups_padrao':[],'skyone_fluxo_modelo':None,**d,'versao':1}
        if not final.get('nome'): raise HTTPException(400,'Nome obrigatório')
        if final['tenant_id']: await empresa(c,final['tenant_id'])
        if final['tipo'] not in ('atendimento','interno') or not isinstance(final['modulos'],list) or any(m not in REGISTRO for m in final['modulos']): raise HTTPException(400,'Modelo inválido')
        if not isinstance(final['comportamento_padrao'],dict): raise HTTPException(400,'Comportamento inválido')
        validar(Comportamento,final['comportamento_padrao'])
        row=await c.fetchrow('INSERT INTO modelos_agente(id,versao,tenant_id,nome,tipo,modulos,comportamento_padrao,desfechos,followups_padrao,skyone_fluxo_modelo) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10) RETURNING *',mid,final['versao'],final['tenant_id'],final['nome'],final['tipo'],final['modulos'],json.dumps(final['comportamento_padrao']),final['desfechos'],json.dumps(final['followups_padrao']),final['skyone_fluxo_modelo'])
        await auditar(c,final['tenant_id'],actor,'modelo.versionado',mid+':'+str(final['versao']))
    return registro(row)

@router.post('/modelos-agente',status_code=201)
async def criar_modelo(request:Request,dados:dict,actor=Depends(painel)):
    return await gravar_modelo(request,dados,actor)

@router.patch('/modelos-agente/{modelo_id}')
async def alterar_modelo(request:Request,modelo_id:str,dados:dict,tenant:str|None=None,actor=Depends(painel)):
    return await gravar_modelo(request,dados,actor,modelo_id,tenant)

@router.get('/auditoria/plataforma')
async def auditoria_global(request:Request):
    rows=await request.app.state.db.fetch('SELECT * FROM audit_log WHERE tenant_id IS NULL ORDER BY id DESC LIMIT 100')
    return {'itens':[registro(r) for r in rows]}

@router.get('/tenants/{tenant_id}/consumo')
async def consumo(request:Request,tenant_id:str,mes:str|None=None):
    from nucleo.consumo import consulta
    await empresa(request.app.state.db,tenant_id)
    return await consulta(request.app.state.db,tenant_id,mes)
