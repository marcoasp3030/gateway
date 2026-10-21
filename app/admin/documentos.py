"""ARQ1-a1 no painel (docs/ESPEC_ARQ1.md, seção 14): chaves de formulário, documentos recebidos, simulador com arquivo e a capacidade
de documentos do agente. Chave de formulário, apagar documento e capacidade: admin e plataforma; ler e baixar: qualquer papel."""
import re
import secrets
from datetime import datetime
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse,Response
from nucleo.autenticacao import painel,exigir_admin,hash_chave
from nucleo.banco import Repositorio
from nucleo import documentos as D
from admin.servico import auditar
from admin.alteracoes import registrar

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
ORIGEM=re.compile(r'https?://[a-z0-9.-]+(:[0-9]{1,5})?')
CAMPOS_DA_CHAVE='id,nome,agente_id,limite_por_minuto,max_bytes,tipos_aceitos,modos,origens_permitidas,expira_em,criado_em,ultimo_uso_em,revogada_em,revogada_por'


def _erro(status,campo=None,motivo=None):
    from canais.documentos import Erro,_erro as resposta
    return resposta(Erro(status,campo,motivo))


async def _empresa(db,tenant):
    if not await Repositorio(db).tenant(tenant): raise HTTPException(404,'TENANT_NOT_FOUND')


# ---------------- chaves de formulário ----------------

def _validar_chave(d):
    if not isinstance(d,dict) or set(d)-{'nome','agente_id','limite_por_minuto','max_bytes','tipos','modos','origens','expira_em'}: return 'corpo'
    if not isinstance(d.get('nome'),str) or not 1<=len(d['nome'].strip())<=200: return 'nome'
    if not isinstance(d.get('agente_id'),str): return 'agente_id'
    lim=d.get('limite_por_minuto',10)
    if isinstance(lim,bool) or not isinstance(lim,int) or not 1<=lim<=600: return 'limite_por_minuto'
    mb=d.get('max_bytes')
    if mb is not None and (isinstance(mb,bool) or not isinstance(mb,int) or not 1024<=mb<=D.LIMITE_MAXIMO): return 'max_bytes'
    tipos=d.get('tipos')
    if tipos is not None and (not isinstance(tipos,list) or not tipos or any(t not in D.FORMATOS for t in tipos)): return 'tipos'
    modos=d.get('modos',['agente'])
    if not isinstance(modos,list) or not modos or any(m not in ('agente','extrair') for m in modos) or len(set(modos))!=len(modos): return 'modos'
    origens=d.get('origens',[])
    if not isinstance(origens,list) or len(origens)>20 or any(not isinstance(o,str) or not ORIGEM.fullmatch(o.lower().rstrip('/')) for o in origens): return 'origens'
    if d.get('expira_em') is not None:
        try:
            if datetime.fromisoformat(str(d['expira_em']).replace('Z','+00:00')).tzinfo is None: return 'expira_em'
        except ValueError: return 'expira_em'
    return None


@router.post('/tenants/{tenant}/chaves-formulario',status_code=201,dependencies=[Depends(exigir_admin)])
async def criar_chave(request:Request,tenant:str,actor=Depends(painel)):
    """Chave de formulário: só envia documento, presa a esta empresa e a um agente. A chave aparece só nesta resposta."""
    db=request.app.state.db;await _empresa(db,tenant)
    try: d=await request.json()
    except ValueError: return _erro(400,'corpo')
    campo=_validar_chave(d)
    if campo: return _erro(400,campo)
    if not await Repositorio(db).obter(tenant,'agentes',d['agente_id']): return _erro(404,'agente_id')
    segredo='gwf_'+secrets.token_urlsafe(32);kid='key_'+secrets.token_hex(12)
    expira=datetime.fromisoformat(str(d['expira_em']).replace('Z','+00:00')) if d.get('expira_em') else None
    async with db.acquire() as conn,conn.transaction():
        row=await conn.fetchrow(f'''INSERT INTO tenant_api_keys(id,tenant_id,nome,hash,escopos,agente_id,expira_em,tipo,limite_por_minuto,max_bytes,tipos_aceitos,modos,origens_permitidas)
            VALUES ($1,$2,$3,$4,ARRAY['documentos:formulario'],$5,$6,'formulario',$7,$8,$9,$10,$11) RETURNING {CAMPOS_DA_CHAVE}''',
            kid,tenant,d['nome'].strip(),hash_chave(segredo,request.app.state.infra.pepper),d['agente_id'],expira,d.get('limite_por_minuto',10),d.get('max_bytes'),
            [t for t in D.FORMATOS if t in d['tipos']] if d.get('tipos') else None,d.get('modos',['agente']),[o.lower().rstrip('/') for o in d.get('origens',[])])
        await registrar(conn,tenant,actor,'chave_formulario',kid,'criada',None,{'nome':row['nome'],'agente_id':row['agente_id'],'modos':row['modos'],
                        'origens':row['origens_permitidas'],'limite_por_minuto':row['limite_por_minuto']})
        await auditar(conn,tenant,actor,'chave_formulario.criada',kid)
    return {**jsonable_encoder(dict(row)),'chave':segredo}


@router.get('/tenants/{tenant}/chaves-formulario')
async def chaves(request:Request,tenant:str):
    db=request.app.state.db;await _empresa(db,tenant)
    rows=await db.fetch(f"SELECT {CAMPOS_DA_CHAVE} FROM tenant_api_keys WHERE tenant_id=$1 AND tipo='formulario' ORDER BY criado_em DESC",tenant)
    return {'itens':[dict(r) for r in rows]}


@router.delete('/tenants/{tenant}/chaves-formulario/{kid}',dependencies=[Depends(exigir_admin)])
async def revogar_chave(request:Request,tenant:str,kid:str,actor=Depends(painel)):
    db=request.app.state.db;await _empresa(db,tenant)
    async with db.acquire() as conn,conn.transaction():
        row=await conn.fetchrow(f"""UPDATE tenant_api_keys SET revogada_em=coalesce(revogada_em,now()),revogada_por=coalesce(revogada_por,$3)
            WHERE tenant_id=$1 AND id=$2 AND tipo='formulario' RETURNING {CAMPOS_DA_CHAVE}""",tenant,kid,actor)
        if row is None: return _erro(404,'id')
        await auditar(conn,tenant,actor,'chave_formulario.revogada',kid)
    return dict(row)


# ---------------- documentos ----------------

def _item(a):
    return {k:a[k] for k in ('id','agente_id','conversation_id','origem','modo','nome','formato','tamanho_bytes','estado','motivo','paginas','tipo_documento',
                             'referencia','criado_em','processado_em','expira_em','apagado_em')}


@router.get('/tenants/{tenant}/anexos')
async def anexos(request:Request,tenant:str,conversation_id:str|None=None,agente_id:str|None=None,estado:str|None=None,limite:int=50,cursor:str|None=None):
    db=request.app.state.db;await _empresa(db,tenant)
    if not 1<=limite<=100: return _erro(400,'limite')
    rows=await db.fetch("""SELECT * FROM anexos WHERE tenant_id=$1 AND ($2::text IS NULL OR conversation_id=$2) AND ($3::text IS NULL OR agente_id=$3)
        AND ($4::text IS NULL OR estado=$4) AND ($5::text IS NULL OR id<$5) ORDER BY id DESC LIMIT $6""",tenant,conversation_id,agente_id,estado,cursor,limite+1)
    return {'itens':[_item(a) for a in rows[:limite]],'proximo_cursor':rows[limite-1]['id'] if len(rows)>limite else None}


async def _anexo(db,tenant,aid):
    a=await db.fetchrow('SELECT * FROM anexos WHERE tenant_id=$1 AND id=$2',tenant,aid)
    if a is None: raise HTTPException(404,'NOT_FOUND')
    return a


@router.get('/tenants/{tenant}/anexos/{aid}')
async def anexo(request:Request,tenant:str,aid:str):
    """Detalhe com TODOS os campos (inclusive `a_conferir`) e a estrutura; o texto de cada página em /texto."""
    db=request.app.state.db;await _empresa(db,tenant);a=await _anexo(db,tenant,aid)
    doc=D.documento(a,D.resultado(a),completo=True,limite=64*1024)
    return {**_item(a),**{k:doc[k] for k in ('conteudo','verificacao_pagamento')}}


@router.get('/tenants/{tenant}/anexos/{aid}/texto')
async def texto(request:Request,tenant:str,aid:str,pagina:int=1):
    db=request.app.state.db;await _empresa(db,tenant);a=await _anexo(db,tenant,aid)
    p=next((x for x in (D.resultado(a) or {}).get('paginas',[]) if x['pagina']==pagina),None)
    if p is None: return _erro(404,'pagina')
    return {'anexo_id':a['id'],'pagina':p['pagina'],'origem':p['origem'],'texto':p['texto'],
            'tabelas':[{'id':f'tab_{p["pagina"]}_{n}','linhas':t['linhas']} for n,t in enumerate(p['tabelas'],1)]}


@router.get('/tenants/{tenant}/anexos/{aid}/original')
async def original(request:Request,tenant:str,aid:str,actor=Depends(painel)):
    """Download do original (sempre como anexo, nunca aberto no navegador). Auditado."""
    import asyncio
    db=request.app.state.db;await _empresa(db,tenant);a=await _anexo(db,tenant,aid)
    if a['apagado_em'] is not None or not a['caminho']: return _erro(404,'arquivo',a['estado'])
    dados=D.decifrar(tenant,a['id'],a['chave_cifrada'],await asyncio.to_thread(D.ler_arquivo,a['caminho']))
    async with db.acquire() as conn,conn.transaction(): await auditar(conn,tenant,actor or 'painel','documento.baixado',a['id'])
    tipos={'pdf':'application/pdf','xml':'application/xml','jpg':'image/jpeg','png':'image/png','webp':'image/webp'}
    return Response(dados,media_type=tipos[a['formato']],headers={'Content-Disposition':f'attachment; filename="{a["nome"]}"','Cache-Control':'no-store',
                                                                    'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})


@router.delete('/tenants/{tenant}/anexos/{aid}',dependencies=[Depends(exigir_admin)])
async def apagar(request:Request,tenant:str,aid:str,actor=Depends(painel)):
    db=request.app.state.db;await _empresa(db,tenant);a=await _anexo(db,tenant,aid)
    if a['apagado_em'] is None:
        async with db.acquire() as conn,conn.transaction():
            await D.apagar(conn,a)
            await auditar(conn,tenant,actor,'documento.apagado',a['id'])
            from nucleo.webhooks import emitir
            await emitir(conn,tenant,'documento.apagado',{'anexo_id':a['id'],'referencia':None})
    return _item(await _anexo(db,tenant,aid))


@router.post('/tenants/{tenant}/agentes/{agente_id}/simular-documento')
async def simular_documento(request:Request,tenant:str,agente_id:str,actor=Depends(painel)):
    """Simulador do painel com arquivo (multipart: arquivo, canal_id, identificador, mensagem?): como o modo agente, só com envio de teste."""
    from canais.documentos import Erro,_erro as resposta,receber
    db=request.app.state.db;await _empresa(db,tenant)
    try:
        cfg=await request.app.state.configuracoes.efetiva(tenant,agente_id)
        if cfg['send_mode']!='dry': raise Erro(400,'send_mode')
        try: form=await request.form(max_files=1,max_fields=10)
        except Exception: raise Erro(400,'corpo','multipart_invalido') from None
        arquivo=form.get('arquivo')
        if arquivo is None or isinstance(arquivo,str): raise Erro(400,'arquivo')
        dados=await arquivo.read(D.LIMITE_MAXIMO+1)
        status,corpo=await receber(request.app.state,tenant,agente_id,dados,arquivo.filename,'agente','simulador',None,contato=(form.get('identificador') or '').strip(),
            mensagem=(form.get('mensagem') or '').strip()[:2000] or None,canal_id=(form.get('canal_id') or '').strip() or None,simulacao=True,actor=actor)
        return JSONResponse(jsonable_encoder(corpo),status_code=status,headers={'Cache-Control':'no-store'})
    except Erro as e: return resposta(e)
    except LookupError: return _erro(404,'agente_id')


# ---------------- capacidade do agente ----------------

@router.get('/tenants/{tenant}/agentes/{agente_id}/capacidades')
async def capacidades(request:Request,tenant:str,agente_id:str):
    db=request.app.state.db;await _empresa(db,tenant)
    cap=await D.capacidade(db,tenant,agente_id)
    if cap is None: return _erro(404,'agente_id')
    return {'documentos':{**{k:cap[k] for k in D.PADRAO},'do_modelo':cap['do_modelo'],'do_agente':cap['do_agente'],'armazenamento':D.disponivel()}}


@router.put('/tenants/{tenant}/agentes/{agente_id}/capacidades/documentos',dependencies=[Depends(exigir_admin)])
async def restringir(request:Request,tenant:str,agente_id:str,actor=Depends(painel)):
    """No agente só se restringe o que o modelo permite (desligar, menos tipos, tamanho, páginas ou retenção). Corpo vazio = sem restrição."""
    import json
    db=request.app.state.db;await _empresa(db,tenant)
    try: d=await request.json()
    except ValueError: return _erro(400,'corpo')
    cap=await D.capacidade(db,tenant,agente_id)
    if cap is None: return _erro(404,'agente_id')
    try: novo=D.validar_capacidade(d,restringir_de=cap['do_modelo'])
    except ValueError as e: return _erro(400,str(e))
    async with db.acquire() as conn,conn.transaction():
        atual=await conn.fetchval('SELECT capacidades FROM agentes WHERE tenant_id=$1 AND id=$2 FOR UPDATE',tenant,agente_id)
        atual=json.loads(atual) if isinstance(atual,str) else (atual or {})
        await conn.execute('UPDATE agentes SET capacidades=$3::jsonb WHERE tenant_id=$1 AND id=$2',tenant,agente_id,json.dumps({**atual,'documentos':novo}))
        await registrar(conn,tenant,actor,'agente_capacidades',agente_id,'alterado',{'documentos':atual.get('documentos')},{'documentos':novo})
        await auditar(conn,tenant,actor,'agente.capacidades_alteradas',agente_id)
    return await capacidades(request,tenant,agente_id)
