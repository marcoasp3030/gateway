import json,secrets
from fastapi import APIRouter,Request,Depends,HTTPException
from pydantic import Field
from .modelos import Estrito
from .servico import auditar,validar
from .capacidades import empresa
from nucleo.autenticacao import painel
from seguranca import validar_formato_url
router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])

# Segredo nunca sai daqui; só quando foi rotacionado e até quando o anterior ainda assina.
CAMPOS="id,tenant_id,url,eventos,ativo,segredo_rotacionado_em,CASE WHEN segredo_anterior_ate>now() THEN segredo_anterior_ate END AS segredo_anterior_ate"
# Durante este tempo depois da rotação, as entregas saem assinadas com o segredo novo e com o anterior.
JANELA_ROTACAO_H=24

class Saida(Estrito):
    url:str|None=None
    eventos:list[str]|None=None
    ativo:bool|None=None

async def obter(db,t,w):
    row=await db.fetchrow(f'SELECT {CAMPOS} FROM tenant_webhooks_saida WHERE tenant_id=$1 AND id=$2',t,w)
    if not row: raise HTTPException(404,'NOT_FOUND')
    return dict(row)

@router.get('/tenants/{tenant_id}/webhooks-saida')
async def lista(request:Request,tenant_id:str):
    await empresa(request.app.state.db,tenant_id)
    return {'itens':[dict(r) for r in await request.app.state.db.fetch(f'SELECT {CAMPOS} FROM tenant_webhooks_saida WHERE tenant_id=$1 ORDER BY id',tenant_id)]}

@router.get('/tenants/{tenant_id}/webhooks-saida/{wid}')
async def detalhe(request:Request,tenant_id:str,wid:str): return await obter(request.app.state.db,tenant_id,wid)

async def salvar(request,t,dados,actor,wid=None):
    d=validar(Saida,dados);novo=wid is None;wid=wid or 'wh_'+secrets.token_hex(12)
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,t)
        old={'url':None,'eventos':[],'ativo':False} if novo else await obter(c,t,wid)
        final={**old,**d}
        validar_formato_url(final['url'])
        if not isinstance(final['eventos'],list) or not all(isinstance(e,str) and 0<len(e)<=100 for e in final['eventos']) or not isinstance(final['ativo'],bool): raise HTTPException(400,'VALIDATION_ERROR')
        if novo:
            segredo=secrets.token_urlsafe(32)
            await c.execute('INSERT INTO tenant_webhooks_saida(id,tenant_id,url,eventos,ativo,segredo_enc) VALUES ($1,$2,$3,$4,$5,$6)',wid,t,final['url'],final['eventos'],final['ativo'],request.app.state.cofre.cifrar(t,segredo))
        else:
            await c.execute('UPDATE tenant_webhooks_saida SET url=$3,eventos=$4,ativo=$5 WHERE tenant_id=$1 AND id=$2',t,wid,final['url'],final['eventos'],final['ativo'])
        await auditar(c,t,actor,'webhook.salvo',wid)
        result=await obter(c,t,wid)
    if novo: result['segredo']=segredo
    return result

@router.post('/tenants/{tenant_id}/webhooks-saida',status_code=201)
async def criar(request:Request,tenant_id:str,dados:dict,actor=Depends(painel)): return await salvar(request,tenant_id,dados,actor)

@router.patch('/tenants/{tenant_id}/webhooks-saida/{wid}')
async def editar(request:Request,tenant_id:str,wid:str,dados:dict,actor=Depends(painel)): return await salvar(request,tenant_id,dados,actor,wid)

@router.delete('/tenants/{tenant_id}/webhooks-saida/{wid}')
async def remover(request:Request,tenant_id:str,wid:str,actor=Depends(painel)):
    return await salvar(request,tenant_id,{'ativo':False},actor,wid)

@router.post('/tenants/{tenant_id}/webhooks-saida/{wid}/testar',status_code=202)
async def testar(request:Request,tenant_id:str,wid:str,actor=Depends(painel)):
    async with request.app.state.db.acquire() as c,c.transaction():
        w=await obter(c,tenant_id,wid)
        if not w['ativo']: raise HTTPException(409,'Webhook inativo')
        eid=await c.fetchval("INSERT INTO webhook_entregas(tenant_id,webhook_id,evento,corpo) VALUES ($1,$2,'teste',$3) RETURNING id",tenant_id,wid,json.dumps({'evento':'teste','tenant_id':tenant_id,'dados':{}}))
        await auditar(c,tenant_id,actor,'webhook.testado',wid)
    return {'entrega_id':eid,'status':'pendente'}

@router.get('/tenants/{tenant_id}/webhooks-saida/{wid}/entregas')
async def entregas(request:Request,tenant_id:str,wid:str):
    await obter(request.app.state.db,tenant_id,wid)
    rows=await request.app.state.db.fetch('SELECT id,evento,tentativas,status,http_status,criado_em FROM webhook_entregas WHERE tenant_id=$1 AND webhook_id=$2 ORDER BY id DESC LIMIT 100',tenant_id,wid)
    return {'itens':[dict(r) for r in rows]}


@router.post('/tenants/{tenant_id}/webhooks-saida/{wid}/rotacionar-segredo')
async def rotacionar(request:Request,tenant_id:str,wid:str,actor=Depends(painel)):
    """Gera um segredo novo e devolve UMA vez. O anterior continua assinando por 24 h (X-Gateway-Signature com as duas assinaturas,
    a do novo primeiro), para o destino trocar sem perder aviso. Rotacionar de novo dentro das 24 h: o "anterior" passa a ser o que
    estava em uso, e o mais antigo deixa de valer."""
    cofre=request.app.state.cofre
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,tenant_id)
        w=await c.fetchrow('SELECT segredo_enc FROM tenant_webhooks_saida WHERE tenant_id=$1 AND id=$2 FOR UPDATE',tenant_id,wid)
        if not w: raise HTTPException(404,'NOT_FOUND')
        segredo=secrets.token_urlsafe(32)
        ate=await c.fetchval("""UPDATE tenant_webhooks_saida SET segredo_anterior_enc=segredo_enc,segredo_enc=$3,
            segredo_anterior_ate=now()+make_interval(hours=>$4),segredo_rotacionado_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING segredo_anterior_ate""",
            tenant_id,wid,cofre.cifrar(tenant_id,segredo),JANELA_ROTACAO_H)
        await auditar(c,tenant_id,actor,'webhook.segredo_rotacionado',wid,{'anterior_valido_ate':ate.isoformat(timespec='seconds')})
        result=await obter(c,tenant_id,wid)
    return {**result,'segredo':segredo}
