import json,secrets
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import Field
from .modelos import Estrito
from .capacidades import empresa
from .servico import validar,auditar
from nucleo.autenticacao import painel
from nucleo.horarios import validar as validar_horarios
router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
class Unidade(Estrito):
    nome:str|None=Field(None,min_length=1,max_length=200)
    telefone_whatsapp:str|None=Field(None,pattern=r'^\d{8,16}$')
    horarios:dict|None=None
    ativo:bool|None=None
    # Fase 4. codigo liga a unidade aos itens do catálogo. Os demais: vazio (nulo) = o agente não
    # afirma nada e diz que a unidade confirma no retorno do pré-pedido.
    codigo:str|None=Field(None,pattern=r'^[a-z0-9][a-z0-9_]{0,39}$')
    endereco:str|None=Field(None,max_length=500)
    telefone:str|None=Field(None,max_length=40)
    whatsapp_pedidos:str|None=Field(None,pattern=r'^\d{8,16}$')
    formas_pagamento:str|None=Field(None,max_length=500)
    taxa_entrega:float|None=Field(None,ge=0,le=99999)
    pedido_minimo:float|None=Field(None,ge=0,le=99999)
    area_entrega:str|None=Field(None,max_length=1000)
    aceita_retirada:bool|None=None
    regras:str|None=Field(None,max_length=2000)

EXTRAS=('codigo','endereco','telefone','whatsapp_pedidos','formas_pagamento','taxa_entrega','pedido_minimo','area_entrega','aceita_retirada','regras')

def formato(row):
    d=dict(row);d['horarios']=json.loads(d['horarios']) if isinstance(d['horarios'],str) else d['horarios']
    for campo in ('taxa_entrega','pedido_minimo'):
        if d.get(campo) is not None: d[campo]=float(d[campo])
    return d

async def obter(c,t,uid):
    row=await c.fetchrow('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND id=$2',t,uid)
    if not row: raise HTTPException(404,'NOT_FOUND')
    return formato(row)

@router.get('/tenants/{tenant_id}/unidades')
async def lista(request:Request,tenant_id:str):
    await empresa(request.app.state.db,tenant_id)
    return {'itens':[formato(r) for r in await request.app.state.db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 ORDER BY id',tenant_id)]}

@router.get('/tenants/{tenant_id}/unidades/{uid}')
async def detalhe(request:Request,tenant_id:str,uid:str): return await obter(request.app.state.db,tenant_id,uid)

async def salvar(request,t,dados,actor,uid=None):
    d=validar(Unidade,dados);novo=uid is None;uid=uid or 'uni_'+secrets.token_hex(12)
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,t)
        old={'nome':None,'telefone_whatsapp':None,'horarios':{},'ativo':True,**{k:None for k in EXTRAS}} if novo else await obter(c,t,uid)
        final={**old,**d}
        if not final['nome'] or not isinstance(final['ativo'],bool): raise HTTPException(400,'VALIDATION_ERROR')
        validar_horarios(final['horarios'])
        # Texto só com espaços vale como vazio.
        for k in EXTRAS:
            if isinstance(final[k],str) and not final[k].strip(): final[k]=None
        if final['codigo'] and await c.fetchval('SELECT EXISTS(SELECT 1 FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2 AND id<>$3)',t,final['codigo'],uid):
            raise HTTPException(409,{'campo':'codigo'})
        if novo:
            await c.execute('INSERT INTO tenant_unidades(id,tenant_id,nome,telefone_whatsapp,horarios,ativo) VALUES ($1,$2,$3,$4,$5::jsonb,$6)',uid,t,final['nome'],final['telefone_whatsapp'],json.dumps(final['horarios']),final['ativo'])
        else:
            await c.execute('UPDATE tenant_unidades SET nome=$3,telefone_whatsapp=$4,horarios=$5::jsonb,ativo=$6 WHERE tenant_id=$1 AND id=$2',t,uid,final['nome'],final['telefone_whatsapp'],json.dumps(final['horarios']),final['ativo'])
        await c.execute('''UPDATE tenant_unidades SET codigo=$3,endereco=$4,telefone=$5,whatsapp_pedidos=$6,formas_pagamento=$7,
            taxa_entrega=$8,pedido_minimo=$9,area_entrega=$10,aceita_retirada=$11,regras=$12 WHERE tenant_id=$1 AND id=$2''',t,uid,*(final[k] for k in EXTRAS))
        await auditar(c,t,actor,'unidade.salva',uid)
        return await obter(c,t,uid)

@router.post('/tenants/{tenant_id}/unidades',status_code=201)
async def criar(request:Request,tenant_id:str,dados:dict,actor=Depends(painel)): return await salvar(request,tenant_id,dados,actor)
@router.patch('/tenants/{tenant_id}/unidades/{uid}')
async def alterar(request:Request,tenant_id:str,uid:str,dados:dict,actor=Depends(painel)): return await salvar(request,tenant_id,dados,actor,uid)
@router.delete('/tenants/{tenant_id}/unidades/{uid}')
async def remover(request:Request,tenant_id:str,uid:str,actor=Depends(painel)): return await salvar(request,tenant_id,{'ativo':False},actor,uid)
