"""IFOOD1-a (docs/ESPEC_IFOOD1.md): lojas do iFood ligadas às empresas.

Só a plataforma liga e desliga (decisão 1): a lista de lojas que autorizaram o aplicativo traz as de todas as empresas, e um
gestor que escolhesse a loja poderia ver o financeiro de outra. O gestor vê as lojas da empresa e confere a conexão."""
import re
from fastapi import APIRouter,Depends,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from admin.servico import auditar
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from .cliente import Ifood,ErroIfood

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
MERCHANT=re.compile(r'[A-Za-z0-9-]{1,64}')
MENSAGENS={'nao_configurado':'A integração com o iFood ainda não foi configurada no gateway.',
           'autenticacao':'O iFood recusou as credenciais do aplicativo.',
           'sem_permissao':'O iFood não deu acesso a esta loja. A loja precisa autorizar o aplicativo no Portal do Parceiro.',
           'nao_encontrado':'O iFood não encontrou esta loja.',
           'limite':'O iFood pediu para esperar. Tente de novo em instantes.',
           'indisponivel':'O iFood não respondeu. Tente de novo em instantes.',
           'recusado':'O iFood recusou a consulta.',
           'formato_inesperado':'O iFood respondeu num formato inesperado.'}


class Estrito(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Ligacao(Estrito):
    unidade:str|None=Field(None,max_length=80)
    ativo:bool=True


class Confirmacao(Estrito):
    confirmacao:str=Field(min_length=1,max_length=255)


def _falha(status,motivo,campo=None):
    codigo={400:'VALIDATION_ERROR',404:'NOT_FOUND',409:'CONFLICT',502:'UPSTREAM_ERROR',503:'UNAVAILABLE'}[status]
    mensagens={'ligada_a_outra_empresa':'Esta loja do iFood já está ligada a outra empresa.','unidade':'Unidade não encontrada nesta empresa.',
               'nao_autorizada':'Esta loja não autorizou o aplicativo da Ticway no iFood.','confirmacao':'Digite o nome da loja para confirmar.'}
    detalhes={'motivo':motivo,**({'campo':campo} if campo else {})}
    return JSONResponse({'erro':{'codigo':codigo,'mensagem':MENSAGENS.get(motivo) or mensagens.get(motivo,'Não foi possível concluir.'),'detalhes':detalhes}},
                        status_code=status,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def _do_ifood(e:ErroIfood):
    return _falha(503 if e.motivo=='nao_configurado' else 502,e.motivo)


def cliente(request):
    estado=request.app.state
    prefixo=getattr(getattr(estado,'pipeline',None),'prefixo','gw')
    return Ifood(estado.http,estado.redis,estado.cofre,prefixo)


def _loja(r):
    return {'merchant_id':r['merchant_id'],'nome':r['nome'],'unidade':r['unidade'],'ativo':r['ativo'],'situacao':r['situacao'],
            'conferida_em':r['conferida_em'],'ligado_em':r['ligado_em'],'ligado_por':r['ligado_por']}


@router.get('/ifood/lojas-autorizadas')
async def autorizadas(request:Request):
    """Lojas que autorizaram o aplicativo no iFood e a qual empresa cada uma está ligada (só plataforma)."""
    try: lojas=await cliente(request).lojas()
    except ErroIfood as e: return _do_ifood(e)
    ligadas={r['merchant_id']:r for r in await request.app.state.db.fetch('SELECT merchant_id,tenant_id,unidade,ativo FROM ifood_lojas')}
    return {'itens':[{**l,'ligada_a':({'tenant_id':ligadas[l['merchant_id']]['tenant_id'],'unidade':ligadas[l['merchant_id']]['unidade'],
                                         'ativo':ligadas[l['merchant_id']]['ativo']} if l['merchant_id'] in ligadas else None)} for l in lojas]}


@router.get('/tenants/{tenant_id}/ifood/lojas')
async def da_empresa(request:Request,tenant_id:str):
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): return _falha(404,'NOT_FOUND')
    rows=await db.fetch('SELECT * FROM ifood_lojas WHERE tenant_id=$1 ORDER BY ligado_em,merchant_id',tenant_id)
    return {'configurado':cliente(request).configurado,'itens':[_loja(r) for r in rows]}


@router.put('/tenants/{tenant_id}/ifood/lojas/{merchant_id}')
async def ligar(request:Request,tenant_id:str,merchant_id:str,body:Ligacao,actor=Depends(painel)):
    """Liga (ou atualiza) a loja do iFood nesta empresa. A loja precisa estar entre as que autorizaram o aplicativo."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): return _falha(404,'NOT_FOUND')
    if not MERCHANT.fullmatch(merchant_id): return _falha(400,'formato','merchant_id')
    if body.unidade and not await db.fetchval('SELECT EXISTS(SELECT 1 FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2)',tenant_id,body.unidade):
        return _falha(400,'unidade','unidade')
    try: lojas={l['merchant_id']:l for l in await cliente(request).lojas()}
    except ErroIfood as e: return _do_ifood(e)
    if merchant_id not in lojas: return _falha(409,'nao_autorizada','merchant_id')
    async with db.acquire() as c,c.transaction():
        atual=await c.fetchrow('SELECT * FROM ifood_lojas WHERE merchant_id=$1 FOR UPDATE',merchant_id)
        if atual and atual['tenant_id']!=tenant_id: return _falha(409,'ligada_a_outra_empresa','merchant_id')
        r=await c.fetchrow("""INSERT INTO ifood_lojas(merchant_id,tenant_id,nome,unidade,ativo,situacao,conferida_em,ligado_por)
            VALUES ($1,$2,$3,$4,$5,'conectada',now(),$6)
            ON CONFLICT (merchant_id) DO UPDATE SET nome=EXCLUDED.nome,unidade=EXCLUDED.unidade,ativo=EXCLUDED.ativo,
                situacao='conectada',conferida_em=now(),atualizado_em=now()
            RETURNING *""",merchant_id,tenant_id,(lojas[merchant_id]['nome'] or '')[:200] or None,body.unidade,body.ativo,actor)
        antes=None if not atual else {'unidade':atual['unidade'],'ativo':atual['ativo']}
        if antes!={'unidade':r['unidade'],'ativo':r['ativo']}:
            await auditar(c,tenant_id,actor,'ifood.loja_ligada' if not atual else 'ifood.loja_alterada',merchant_id,
                          {'antes':antes,'depois':{'unidade':r['unidade'],'ativo':r['ativo']}})
    return _loja(r)


@router.post('/tenants/{tenant_id}/ifood/lojas/{merchant_id}/conferir')
async def conferir(request:Request,tenant_id:str,merchant_id:str):
    """Pergunta ao iFood se a loja ainda dá acesso ao aplicativo e grava a situação."""
    db=request.app.state.db
    r=await db.fetchrow('SELECT * FROM ifood_lojas WHERE tenant_id=$1 AND merchant_id=$2',tenant_id,merchant_id)
    if not r: return _falha(404,'NOT_FOUND')
    try:
        await cliente(request).loja(merchant_id);situacao='conectada'
    except ErroIfood as e:
        if e.motivo not in ('sem_permissao','nao_encontrado'): return _do_ifood(e)
        situacao='sem_permissao'
    r=await db.fetchrow('UPDATE ifood_lojas SET situacao=$3,conferida_em=now() WHERE tenant_id=$1 AND merchant_id=$2 RETURNING *',tenant_id,merchant_id,situacao)
    return _loja(r)


@router.delete('/tenants/{tenant_id}/ifood/lojas/{merchant_id}')
async def desligar(request:Request,tenant_id:str,merchant_id:str,body:Confirmacao,actor=Depends(painel)):
    """Desfaz a ligação (o nome da loja, ou o merchant_id se ela não tiver nome, como confirmação)."""
    async with request.app.state.db.acquire() as c,c.transaction():
        r=await c.fetchrow('SELECT * FROM ifood_lojas WHERE tenant_id=$1 AND merchant_id=$2 FOR UPDATE',tenant_id,merchant_id)
        if not r: return _falha(404,'NOT_FOUND')
        if body.confirmacao.strip() not in {(r['nome'] or '').strip(),merchant_id}-{''}: return _falha(400,'confirmacao','confirmacao')
        await c.execute('DELETE FROM ifood_lojas WHERE merchant_id=$1',merchant_id)
        await auditar(c,tenant_id,actor,'ifood.loja_desligada',merchant_id,{'unidade':r['unidade']})
    return {'ok':True}
