import json
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import Field
from .modelos import Estrito
from .servico import auditar,validar
from nucleo.autenticacao import painel,exigir_plataforma
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
    """Chave de cada módulo na empresa (sem registro = ligado) e, para o menu do painel (MULTI1-a), os módulos que algum agente
    ativo da empresa usa de fato (`em_uso`) e as telas que eles trazem (`telas`)."""
    db=request.app.state.db
    await empresa(db,tenant_id)
    em_uso=set()
    for a in await db.fetch("SELECT id FROM agentes WHERE tenant_id=$1 AND status<>'pausado'",tenant_id):
        em_uso.update((await request.app.state.configuracoes.efetiva(tenant_id,a['id']))['modulos'])
    em_uso=[m for m in REGISTRO if m in em_uso]
    return {'itens':await Repositorio(db).listar(tenant_id,'modulos_tenant',100),'em_uso':em_uso,
            'telas':[t for m in em_uso for t in REGISTRO[m].telas]}

@router.put('/tenants/{tenant_id}/modulos/{modulo}',dependencies=[Depends(exigir_plataforma)])
async def salvar_modulo(request:Request,tenant_id:str,modulo:str,dados:dict,actor=Depends(painel)):
    """Chave do módulo na empresa. Só a plataforma (MULTI1-a)."""
    if modulo not in REGISTRO: raise HTTPException(404,'NOT_FOUND')
    d=validar(ConfigModulo,dados)
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,tenant_id)
        await c.execute('INSERT INTO modulos_tenant(tenant_id,modulo,ativo,config) VALUES ($1,$2,$3,$4::jsonb) ON CONFLICT(tenant_id,modulo) DO UPDATE SET ativo=EXCLUDED.ativo,config=EXCLUDED.config',tenant_id,modulo,d['ativo'],json.dumps(d.get('config',{})))
        await auditar(c,tenant_id,actor,'modulo.alterado',modulo)
    request.app.state.configuracoes.invalidar(tenant_id)
    return {'ok':True}

@router.get('/tenants/{tenant_id}/agentes/{agente_id}/modulos')
async def modulos_do_agente(request:Request,tenant_id:str,agente_id:str):
    """Módulos que o agente usa de fato (permitido no modelo E ligado na empresa E ligado no agente) e cada camada."""
    from modulos import chaves_da_empresa
    db=request.app.state.db;repo=Repositorio(db)
    await empresa(db,tenant_id)
    agente=await repo.obter(tenant_id,'agentes',agente_id)
    if not agente: raise HTTPException(404,'NOT_FOUND')
    cfg=await request.app.state.configuracoes.efetiva(tenant_id,agente_id)
    modelo=await repo.modelo(tenant_id,agente['modelo_id'],agente['modelo_versao'])
    permitidos=modelo.get('modulos_permitidos') if modelo.get('modulos_permitidos') is not None else modelo.get('modulos') or []
    padrao=modelo.get('modulos_padrao') if modelo.get('modulos_padrao') is not None else permitidos
    chaves=await chaves_da_empresa(db,tenant_id)
    return {'agente_id':agente_id,'modelo':{'id':modelo['id'],'versao':modelo['versao'],'nome':modelo['nome']},'segmento':modelo.get('segmento'),
            'modulos':cfg['modulos'],'permitidos':[m for m in REGISTRO if m in permitidos],
            'ligados_no_agente':agente.get('modulos_ligados'),'padrao_do_modelo':[m for m in REGISTRO if m in padrao],
            'da_empresa':{m:chaves.get(m,True) for m in REGISTRO if m in permitidos}}

from typing import Literal
from .modelos import Comportamento
from nucleo.banco import registro

class ItemDeLista(Estrito):
    """Motivo de transferência ou tipo de solicitação declarado no modelo."""
    id:str=Field(pattern=r'^[a-z0-9_]{2,40}$')
    rotulo:str=Field(min_length=2,max_length=60)
    converte_em_solicitacao:bool|None=None


class Modelo(Estrito):
    id:str|None=Field(None,pattern=r'^[a-z0-9_]{3,80}$')
    tenant_id:str|None=None
    nome:str|None=Field(None,min_length=1,max_length=200)
    tipo:Literal['atendimento','interno']|None=None
    # MULTI1-a. `modulos` é o nome antigo de `modulos_permitidos` (continua aceito).
    segmento:str|None=Field(None,pattern=r'^[a-z0-9_]{2,40}$')
    modulos:list[str]|None=None
    modulos_permitidos:list[str]|None=None
    modulos_padrao:list[str]|None=None
    motivos_transferencia:list[ItemDeLista]|None=Field(None,max_length=30)
    tipos_solicitacao:list[ItemDeLista]|None=Field(None,max_length=30)
    comportamento_padrao:dict|None=None
    perfil_padrao:dict|None=None
    desfechos:list[str]|None=None
    followups_padrao:list[dict]|None=None
    skyone_fluxo_modelo:str|None=Field(None,max_length=200)
    skyone_ref:str|None=Field(None,max_length=200)
    ativo:bool|None=None
    # ARQ1-a1: capacidades transversais do núcleo ({"documentos": {...}}); ausente = desligada.
    capacidades:dict|None=None

COLUNAS_DO_MODELO=('id','versao','tenant_id','nome','tipo','modulos','comportamento_padrao','desfechos','followups_padrao','skyone_fluxo_modelo','perfil_padrao',
                   'segmento','modulos_permitidos','modulos_padrao','motivos_transferencia','tipos_solicitacao','skyone_ref','ativo','capacidades')
JSONB_DO_MODELO=('comportamento_padrao','followups_padrao','perfil_padrao','motivos_transferencia','tipos_solicitacao','capacidades')

@router.get('/modelos-agente')
async def modelos(request:Request,tenant:str|None=None,segmento:str|None=None,ativo:bool|None=None):
    if tenant: await empresa(request.app.state.db,tenant)
    rows=await request.app.state.db.fetch("""SELECT * FROM modelos_agente WHERE (tenant_id IS NULL OR tenant_id=$1) AND ($2::text IS NULL OR segmento=$2)
        AND ($3::boolean IS NULL OR ativo=$3) ORDER BY id,versao""",tenant,segmento,ativo)
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
    for campo in ('motivos_transferencia','tipos_solicitacao'):
        if d.get(campo) is not None:
            d[campo]=[{k:v for k,v in i.items() if v is not None} for i in d[campo]]
            if len({i['id'] for i in d[campo]})!=len(d[campo]): raise HTTPException(400,{'campo':campo,'motivo':'repetido'})
    if d.get('modulos') is not None and d.get('modulos_permitidos') is None: d['modulos_permitidos']=d['modulos']
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
            final={'tenant_id':None,'tipo':'atendimento','modulos_permitidos':[],'comportamento_padrao':{},'desfechos':[],'followups_padrao':[],'skyone_fluxo_modelo':None,
                   'perfil_padrao':None,'segmento':None,'modulos_padrao':None,'motivos_transferencia':[],'tipos_solicitacao':[],'skyone_ref':None,'ativo':True,**d,'id':mid,'versao':1}
        if not final.get('nome'): raise HTTPException(400,'Nome obrigatório')
        if final['tenant_id']: await empresa(c,final['tenant_id'])
        permitidos=final.get('modulos_permitidos') if final.get('modulos_permitidos') is not None else final.get('modulos') or []
        padrao=final.get('modulos_padrao') if final.get('modulos_padrao') is not None else permitidos
        if final['tipo'] not in ('atendimento','interno') or not isinstance(permitidos,list) or any(m not in REGISTRO for m in permitidos): raise HTTPException(400,{'campo':'modulos_permitidos'})
        if any(m not in permitidos for m in padrao): raise HTTPException(400,{'campo':'modulos_padrao','motivo':'fora_dos_permitidos'})
        final['modulos_permitidos']=[m for m in REGISTRO if m in permitidos];final['modulos_padrao']=[m for m in REGISTRO if m in padrao]
        # A coluna antiga continua igual aos permitidos (a imagem anterior lê dela).
        final['modulos']=final['modulos_permitidos']
        if final.get('skyone_ref') is None and final.get('skyone_fluxo_modelo'): final['skyone_ref']=final['skyone_fluxo_modelo']
        if not isinstance(final.get('capacidades') or {},dict) or set(final.get('capacidades') or {})-{'documentos'}: raise HTTPException(400,{'campo':'capacidades'})
        if (final.get('capacidades') or {}).get('documentos') is not None:
            from nucleo.documentos import validar_capacidade
            try: final['capacidades']={**final['capacidades'],'documentos':validar_capacidade(final['capacidades']['documentos'])}
            except ValueError as erro: raise HTTPException(400,{'campo':str(erro)}) from None
        final['capacidades']=final.get('capacidades') or {}
        if not isinstance(final['comportamento_padrao'],dict): raise HTTPException(400,'Comportamento inválido')
        validar(Comportamento,final['comportamento_padrao'])
        if final.get('perfil_padrao') is not None:
            from nucleo.atendimento import validar_perfil
            from modulos import motivos_efetivos
            _,erros=validar_perfil(final['perfil_padrao'],[x['id'] for x in motivos_efetivos(final,final['modulos_permitidos'])])
            if erros: raise HTTPException(400,{'campo':'perfil_padrao','motivo':erros[0]['campo']})
        valores=[]
        for k in COLUNAS_DO_MODELO:
            v=final.get(k)
            if v is None and k in ('comportamento_padrao','followups_padrao'): v={} if k=='comportamento_padrao' else []
            if v is None and k=='desfechos': v=[]
            if v is None and k=='perfil_padrao': v={}
            valores.append(json.dumps(v) if k in JSONB_DO_MODELO and v is not None else v)
        marcas=','.join(f'${n}'+('::jsonb' if k in JSONB_DO_MODELO else '') for n,k in enumerate(COLUNAS_DO_MODELO,1))
        row=await c.fetchrow(f'INSERT INTO modelos_agente({",".join(COLUNAS_DO_MODELO)}) VALUES ({marcas}) RETURNING *',*valores)
        await auditar(c,final['tenant_id'],actor,'modelo.versionado',mid+':'+str(final['versao']))
    return registro(row)

@router.post('/modelos-agente',status_code=201,dependencies=[Depends(exigir_plataforma)])
async def criar_modelo(request:Request,dados:dict,actor=Depends(painel)):
    """Cria um modelo de agente (versão 1). Só a plataforma (MULTI1-a). Segmento novo sem módulo novo = só este cadastro."""
    return await gravar_modelo(request,dados,actor)

@router.patch('/modelos-agente/{modelo_id}',dependencies=[Depends(exigir_plataforma)])
async def alterar_modelo(request:Request,modelo_id:str,dados:dict,tenant:str|None=None,actor=Depends(painel)):
    """Cria a próxima versão do modelo. Agentes continuam na versão em que estão."""
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


@router.get('/tenants/{tenant_id}/modelos-disponiveis')
async def modelos_disponiveis(request:Request,tenant_id:str):
    """PAPEL1-a: para a empresa criar agente sem a rota global. Modelos ativos (globais + os da empresa), a versão mais recente de cada."""
    await empresa(request.app.state.db,tenant_id)
    rows=await request.app.state.db.fetch("""SELECT DISTINCT ON (id) * FROM modelos_agente WHERE (tenant_id IS NULL OR tenant_id=$1) AND ativo
        ORDER BY id,versao DESC""",tenant_id)
    campos=('id','versao','tenant_id','nome','tipo','segmento','modulos_permitidos','capacidades')
    return {'itens':[{k:registro(r).get(k) for k in campos} for r in rows]}
