import json,secrets
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import Field
from .modelos import Estrito
from .capacidades import empresa
from .servico import validar,auditar
from typing import Literal
from nucleo.autenticacao import painel,pode
from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from pydantic import AwareDatetime
from nucleo import horarios as H
from nucleo.horarios import validar_sem_sobreposicao as validar_horarios
from .alteracoes import registrar
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
    # E.164 sem "+": só dígitos, de 10 a 15, com DDI.
    whatsapp_pedidos:str|None=Field(None,pattern=r'^\d{10,15}$')
    formas_pagamento:str|None=Field(None,max_length=500)
    taxa_entrega:float|None=Field(None,ge=0,le=99999)
    pedido_minimo:float|None=Field(None,ge=0,le=99999)
    area_entrega:str|None=Field(None,max_length=1000)
    aceita_retirada:bool|None=None
    regras:str|None=Field(None,max_length=2000)
    # Gestão G1: fuso próprio da unidade (nulo herda o da empresa).
    fuso:str|None=Field(None,max_length=64)
    # CAL1: cidade e UF da unidade, para os feriados estaduais e municipais do calendário do pacote. Nulo = só nacionais.
    cidade:str|None=Field(None,max_length=80)
    uf:str|None=Field(None,pattern=r'^[A-Z]{2}$')
    # Gestão G4-a: entrega. Nulo = não informado ("a unidade confirma"). Só cadastro até o G4-b.
    delivery_ativo:bool|None=None
    taxa_modo:Literal['fixa','bairro','cep']|None=None
    frete_gratis_acima:float|None=Field(None,gt=0,le=99999)
    tempo_entrega_min:int|None=Field(None,ge=1,le=600)
    tempo_retirada_min:int|None=Field(None,ge=1,le=600)

EXTRAS=('codigo','endereco','telefone','whatsapp_pedidos','formas_pagamento','taxa_entrega','pedido_minimo','area_entrega','aceita_retirada','regras','fuso',
        'delivery_ativo','taxa_modo','frete_gratis_acima','tempo_entrega_min','tempo_retirada_min','cidade','uf')
# Taxas e formas de pagamento: só admin e plataforma alteram (decisão 1 do G4). Vale quando o valor muda.
SO_ADMIN=('taxa_entrega','taxa_modo','frete_gratis_acima','delivery_ativo','formas_pagamento')

CAMPOS_HISTORICO=('nome','ativo','horarios')+EXTRAS

def formato(row):
    d=dict(row);d['horarios']=json.loads(d['horarios']) if isinstance(d['horarios'],str) else d['horarios']
    for campo in ('taxa_entrega','pedido_minimo','frete_gratis_acima'):
        if d.get(campo) is not None: d[campo]=float(d[campo])
    return d

async def fuso_da_empresa(c,t):
    return await c.fetchval('SELECT timezone FROM tenants WHERE id=$1',t)

async def com_situacao(c,t,row,fuso_empresa=None):
    """Unidade como o painel vê: dados + situação agora (aberta, motivo, próxima abertura, pausa)."""
    d=formato(row);fuso_empresa=fuso_empresa or await fuso_da_empresa(c,t)
    d['fuso_efetivo']=d['fuso'] or fuso_empresa
    d['situacao']=await H.situacao_da_unidade(c,t,row,fuso_empresa)
    return d

async def obter(c,t,uid):
    row=await c.fetchrow('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND id=$2',t,uid)
    if not row: raise HTTPException(404,'NOT_FOUND')
    return formato(row)

async def linha(c,t,uid,travar=False):
    row=await c.fetchrow('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND id=$2'+(' FOR UPDATE' if travar else ''),t,uid)
    if not row: raise HTTPException(404,'NOT_FOUND')
    return row

@router.get('/tenants/{tenant_id}/unidades')
async def lista(request:Request,tenant_id:str):
    await empresa(request.app.state.db,tenant_id)
    db=request.app.state.db;fuso=await fuso_da_empresa(db,tenant_id)
    return {'itens':[await com_situacao(db,tenant_id,r,fuso) for r in await db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 ORDER BY id',tenant_id)]}

@router.get('/tenants/{tenant_id}/unidades/{uid}')
async def detalhe(request:Request,tenant_id:str,uid:str):
    db=request.app.state.db
    return await com_situacao(db,tenant_id,await linha(db,tenant_id,uid))

async def salvar(request,t,dados,actor,uid=None):
    d=validar(Unidade,dados);novo=uid is None;uid=uid or 'uni_'+secrets.token_hex(12)
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,t)
        old={'nome':None,'telefone_whatsapp':None,'horarios':{},'ativo':True,**{k:None for k in EXTRAS}} if novo else await obter(c,t,uid)
        final={**old,**d}
        if not final['nome'] or not isinstance(final['ativo'],bool): raise HTTPException(400,'VALIDATION_ERROR')
        validar_horarios(final['horarios'])
        if final['fuso']:
            try: ZoneInfo(final['fuso'])
            except (ValueError,KeyError,OSError): raise HTTPException(400,{'campo':'fuso'}) from None
        # Texto só com espaços vale como vazio.
        for k in EXTRAS:
            if isinstance(final[k],str) and not final[k].strip(): final[k]=None
        if not pode(request,'gestor') and any(final[k]!=old.get(k) for k in SO_ADMIN): raise HTTPException(403,'FORBIDDEN')
        if final['codigo'] and await c.fetchval('SELECT EXISTS(SELECT 1 FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2 AND id<>$3)',t,final['codigo'],uid):
            raise HTTPException(409,{'campo':'codigo'})
        if novo:
            await c.execute('INSERT INTO tenant_unidades(id,tenant_id,nome,telefone_whatsapp,horarios,ativo) VALUES ($1,$2,$3,$4,$5::jsonb,$6)',uid,t,final['nome'],final['telefone_whatsapp'],json.dumps(final['horarios']),final['ativo'])
        else:
            await c.execute('UPDATE tenant_unidades SET nome=$3,telefone_whatsapp=$4,horarios=$5::jsonb,ativo=$6 WHERE tenant_id=$1 AND id=$2',t,uid,final['nome'],final['telefone_whatsapp'],json.dumps(final['horarios']),final['ativo'])
        await c.execute('UPDATE tenant_unidades SET '+','.join(f'{k}=${i}' for i,k in enumerate(EXTRAS,3))+' WHERE tenant_id=$1 AND id=$2',t,uid,*(final[k] for k in EXTRAS))
        await auditar(c,t,actor,'unidade.salva',uid)
        row=await linha(c,t,uid)
        await registrar(c,t,actor,'unidade',uid,'criado' if novo else 'alterado',None if novo else {k:old.get(k) for k in CAMPOS_HISTORICO},{k:formato(row).get(k) for k in CAMPOS_HISTORICO})
        return await com_situacao(c,t,row)

@router.post('/tenants/{tenant_id}/unidades',status_code=201)
async def criar(request:Request,tenant_id:str,dados:dict,actor=Depends(painel)): return await salvar(request,tenant_id,dados,actor)
@router.patch('/tenants/{tenant_id}/unidades/{uid}')
async def alterar(request:Request,tenant_id:str,uid:str,dados:dict,actor=Depends(painel)): return await salvar(request,tenant_id,dados,actor,uid)
@router.delete('/tenants/{tenant_id}/unidades/{uid}')
async def remover(request:Request,tenant_id:str,uid:str,actor=Depends(painel)): return await salvar(request,tenant_id,{'ativo':False},actor,uid)


# ---------------- Gestão G1: horários, exceções e pausa ----------------

class Pausa(Estrito):
    ate:AwareDatetime|None=None
    indefinida:bool|None=None
    motivo:str|None=Field(None,max_length=200)

class Excecao(Estrito):
    data:date
    fechado:bool|None=None
    faixas:list|None=None
    motivo:str|None=Field(None,max_length=200)

MAX_PAUSA=timedelta(days=60)

def valida(modelo,dados):
    try: return modelo.model_validate(dados,strict=False)
    except ValueError: raise HTTPException(400,'VALIDATION_ERROR') from None

async def _pausa(request,t,uid,actor,ate,indefinida,motivo,acao):
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,t);antes=await linha(c,t,uid,travar=True)
        row=await c.fetchrow('UPDATE tenant_unidades SET pausa_ate=$3,pausa_indefinida=$4,pausa_motivo=$5 WHERE tenant_id=$1 AND id=$2 RETURNING *',
                             t,uid,ate,indefinida,(motivo or '').strip() or None)
        campos=('pausa_ate','pausa_indefinida','pausa_motivo')
        if await registrar(c,t,actor,'unidade',uid,acao,{k:antes[k] for k in campos},{k:row[k] for k in campos}):
            await auditar(c,t,actor,'unidade.'+acao,uid)
        return await com_situacao(c,t,row)

@router.post('/tenants/{tenant_id}/unidades/{uid}/pausar')
async def pausar(request:Request,tenant_id:str,uid:str,dados:dict,actor=Depends(painel)):
    """Fecha a unidade agora: {"ate": "<data e hora com fuso>"} ou {"indefinida": true}; "motivo" opcional."""
    p=valida(Pausa,dados);agora=datetime.now(timezone.utc)
    if bool(p.ate)==bool(p.indefinida): raise HTTPException(400,{'campo':'ate'})
    if p.ate and not agora<p.ate<=agora+MAX_PAUSA: raise HTTPException(400,{'campo':'ate'})
    return await _pausa(request,tenant_id,uid,actor,p.ate,bool(p.indefinida),p.motivo,'pausado')

@router.post('/tenants/{tenant_id}/unidades/{uid}/reabrir')
async def reabrir(request:Request,tenant_id:str,uid:str,actor=Depends(painel)):
    return await _pausa(request,tenant_id,uid,actor,None,False,None,'reaberto')

@router.put('/tenants/{tenant_id}/unidades/{uid}/horarios')
async def trocar_horarios(request:Request,tenant_id:str,uid:str,dados:dict,actor=Depends(painel)):
    """Substitui a grade semanal inteira: {"seg": [["12:00","15:00"],["19:00","22:00"]], ...}. Dia ausente = fechado."""
    try: validar_horarios(dados)
    except ValueError: raise HTTPException(400,{'campo':'horarios'}) from None
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,tenant_id);antes=formato(await linha(c,tenant_id,uid,travar=True))
        row=await c.fetchrow('UPDATE tenant_unidades SET horarios=$3::jsonb WHERE tenant_id=$1 AND id=$2 RETURNING *',tenant_id,uid,json.dumps(dados))
        if await registrar(c,tenant_id,actor,'unidade',uid,'horarios',{'horarios':antes['horarios']},{'horarios':dados}):
            await auditar(c,tenant_id,actor,'unidade.horarios',uid)
        return await com_situacao(c,tenant_id,row)

def formato_excecao(r):
    d=dict(r);d['data']=d['data'].isoformat();d['faixas']=json.loads(d['faixas']) if isinstance(d['faixas'],str) else d['faixas'];return d

@router.get('/tenants/{tenant_id}/unidades/{uid}/excecoes')
async def excecoes(request:Request,tenant_id:str,uid:str,desde:date|None=None,ate:date|None=None):
    """Exceções por data. Sem filtro, de ontem em diante."""
    db=request.app.state.db;await linha(db,tenant_id,uid)
    desde=desde or (datetime.now(timezone.utc).date()-timedelta(days=1))
    return {'itens':[formato_excecao(r) for r in await H.excecoes_da_unidade(db,tenant_id,uid,desde,ate)]}

@router.post('/tenants/{tenant_id}/unidades/{uid}/excecoes')
async def salvar_excecao(request:Request,tenant_id:str,uid:str,dados:dict,actor=Depends(painel)):
    """Cria ou substitui a exceção da data: {"data": "2026-12-25", "fechado": true} ou {"data": ..., "faixas": [["12:00","16:00"]]}."""
    e=valida(Excecao,dados);faixas=e.faixas or []
    if bool(e.fechado)==bool(faixas): raise HTTPException(400,{'campo':'faixas'})
    try: H.validar_faixas_do_dia(faixas)
    except ValueError: raise HTTPException(400,{'campo':'faixas'}) from None
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,tenant_id);await linha(c,tenant_id,uid,travar=True)
        antes=await c.fetchrow('SELECT fechado,faixas,motivo FROM unidade_excecoes WHERE tenant_id=$1 AND unidade_id=$2 AND data=$3',tenant_id,uid,e.data)
        row=await c.fetchrow("""INSERT INTO unidade_excecoes(tenant_id,unidade_id,data,fechado,faixas,motivo) VALUES ($1,$2,$3,$4,$5::jsonb,$6)
            ON CONFLICT (tenant_id,unidade_id,data) DO UPDATE SET fechado=EXCLUDED.fechado,faixas=EXCLUDED.faixas,motivo=EXCLUDED.motivo
            RETURNING id,data,fechado,faixas,motivo""",tenant_id,uid,e.data,bool(e.fechado),json.dumps(faixas),(e.motivo or '').strip() or None)
        novo=formato_excecao(row)
        if await registrar(c,tenant_id,actor,'excecao',f'{uid}:{novo["data"]}','alterado' if antes else 'criado',
                           {'fechado':antes['fechado'],'faixas':antes['faixas'],'motivo':antes['motivo']} if antes else None,
                           {k:novo[k] for k in ('fechado','faixas','motivo')}):
            await auditar(c,tenant_id,actor,'unidade.excecao',uid)
        return novo

@router.delete('/tenants/{tenant_id}/unidades/{uid}/excecoes/{eid}')
async def remover_excecao(request:Request,tenant_id:str,uid:str,eid:int,actor=Depends(painel)):
    async with request.app.state.db.acquire() as c,c.transaction():
        row=await c.fetchrow('DELETE FROM unidade_excecoes WHERE tenant_id=$1 AND unidade_id=$2 AND id=$3 RETURNING id,data,fechado,faixas,motivo',tenant_id,uid,eid)
        if not row: raise HTTPException(404,'NOT_FOUND')
        antigo=formato_excecao(row)
        await registrar(c,tenant_id,actor,'excecao',f'{uid}:{antigo["data"]}','removido',{k:antigo[k] for k in ('fechado','faixas','motivo')},None)
        await auditar(c,tenant_id,actor,'unidade.excecao',uid)
    return {'ok':True}

@router.get('/tenants/{tenant_id}/unidades/{uid}/agenda')
async def agenda(request:Request,tenant_id:str,uid:str,dias:int=7):
    """Prévia dia a dia (grade + exceções) a partir de hoje, no fuso da unidade, e a situação agora."""
    if not 1<=dias<=62: raise HTTPException(400,'VALIDATION_ERROR')
    db=request.app.state.db;row=await linha(db,tenant_id,uid);u=formato(row);fuso=u['fuso'] or await fuso_da_empresa(db,tenant_id)
    hoje=datetime.now(timezone.utc).date()
    exc=await H.excecoes_da_unidade(db,tenant_id,uid,hoje-timedelta(days=2),hoje+timedelta(days=dias+2))
    return {'fuso':fuso,'situacao':await H.situacao_da_unidade(db,tenant_id,row,fuso),'dias':H.agenda(u['horarios'],fuso,exc,dias)}
