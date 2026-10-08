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
