"""LOTE1-a no painel (docs/ESPEC_NFSE1_LOTE1.md): criar o lote, mandar os arquivos (um por requisição: PDF, XML ou ZIP), acompanhar o
progresso, ver as notas juntas (PDF + XML), fechar e apagar. Gestor e acima, como os documentos."""
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from nucleo import documentos as D
from nucleo import lotes as L
from admin.servico import auditar

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
ESTADOS=('aberto','lendo','concluido','expirado','apagado')


def _erro(e):
    from canais.documentos import Erro,_erro as resposta
    return resposta(Erro(e.status,e.campo,e.motivo,**e.extra))


async def _empresa(db,tenant):
    if not await Repositorio(db).tenant(tenant): raise HTTPException(404,'TENANT_NOT_FOUND')


async def _lote(db,tenant,lote_id):
    lote=await db.fetchrow('SELECT * FROM lotes WHERE tenant_id=$1 AND id=$2',tenant,lote_id)
    if lote is None: raise L.Recusa(404,'lote_id')
    return lote


def _json(corpo,status=200):
    return JSONResponse(jsonable_encoder(corpo),status_code=status,headers={'Cache-Control':'no-store'})


@router.post('/tenants/{tenant}/agentes/{agente_id}/lotes')
async def criar(request:Request,tenant:str,agente_id:str,actor=Depends(painel)):
    """Corpo opcional {"nome"} (até 120 caracteres, para reconhecer o lote). 201 com o progresso e os limites."""
    db=request.app.state.db;await _empresa(db,tenant)
    try:
        nome=None
        if (await request.body()).strip():
            try: d=await request.json()
            except ValueError: raise L.Recusa(400,'corpo') from None
            if not isinstance(d,dict) or set(d)-{'nome'}: raise L.Recusa(400,'corpo')
            nome=d.get('nome')
            if nome is not None and (not isinstance(nome,str) or len(nome.strip())>120): raise L.Recusa(400,'nome')
            nome=(nome or '').strip() or None
        lote=await L.criar(db,tenant,agente_id,actor or 'painel',nome)
        return _json(await L.resumo(db,lote),201)
    except L.Recusa as e: return _erro(e)


@router.get('/tenants/{tenant}/lotes')
async def listar(request:Request,tenant:str,agente_id:str|None=None,estado:str|None=None,limite:int=20,cursor:str|None=None):
    db=request.app.state.db;await _empresa(db,tenant)
    if not 1<=limite<=100: return _erro(L.Recusa(400,'limite'))
    if estado is not None and estado not in ESTADOS: return _erro(L.Recusa(400,'estado'))
    # Sem filtro de estado, os apagados não aparecem. Cursor = id do último item da página anterior.
    rows=await db.fetch("""SELECT * FROM lotes WHERE tenant_id=$1 AND ($2::text IS NULL OR agente_id=$2) AND ($3::text IS NULL OR estado=$3)
        AND (estado<>'apagado' OR $3::text='apagado')
        AND ($4::text IS NULL OR (criado_em,id)<(SELECT criado_em,id FROM lotes WHERE tenant_id=$1 AND id=$4)) ORDER BY criado_em DESC,id DESC LIMIT $5""",
        tenant,agente_id,estado,cursor,limite+1)
    return _json({'itens':[await L.resumo(db,r) for r in rows[:limite]],'proximo_cursor':rows[limite-1]['id'] if len(rows)>limite else None})


@router.get('/tenants/{tenant}/lotes/{lote_id}')
async def ver(request:Request,tenant:str,lote_id:str):
    db=request.app.state.db;await _empresa(db,tenant)
    try: return _json(await L.resumo(db,await _lote(db,tenant,lote_id)))
    except L.Recusa as e: return _erro(e)


@router.post('/tenants/{tenant}/lotes/{lote_id}/arquivos')
async def enviar(request:Request,tenant:str,lote_id:str,actor=Depends(painel)):
    """multipart com `arquivo` (um por requisição). 202 com os itens; ZIP devolve um item por arquivo de dentro (recusados com o motivo)."""
    db=request.app.state.db;await _empresa(db,tenant)
    try:
        try: form=await request.form(max_files=1,max_fields=5)
        except Exception: raise L.Recusa(400,'corpo','multipart_invalido') from None
        arquivo=form.get('arquivo')
        if arquivo is None or isinstance(arquivo,str): raise L.Recusa(400,'arquivo')
        dados=await arquivo.read(D.LIMITE_MAXIMO+1)
        itens=await L.receber(db,tenant,lote_id,dados,arquivo.filename,actor or 'painel')
        return _json({'arquivos':itens,'lote':await L.resumo(db,await _lote(db,tenant,lote_id))},202)
    except L.Recusa as e: return _erro(e)


@router.get('/tenants/{tenant}/lotes/{lote_id}/arquivos')
async def arquivos(request:Request,tenant:str,lote_id:str,estado:str|None=None,limite:int=100,cursor:str|None=None):
    db=request.app.state.db;await _empresa(db,tenant)
    try:
        await _lote(db,tenant,lote_id)
        if not 1<=limite<=500: raise L.Recusa(400,'limite')
        if estado is not None and estado not in L.CONTADORES: raise L.Recusa(400,'estado')
        # Cursor = id do último item da página anterior (ordem de chegada).
        rows=await db.fetch("""SELECT f.id,f.anexo_id,f.nome,f.zip,f.formato,f.tamanho_bytes,f.estado,f.motivo,f.criado_em,f.lido_em,a.tipo_documento,a.paginas
            FROM lote_arquivos f LEFT JOIN anexos a ON a.id=f.anexo_id
            WHERE f.tenant_id=$1 AND f.lote_id=$2 AND ($3::text IS NULL OR f.estado=$3)
            AND ($4::text IS NULL OR (f.criado_em,f.id)>(SELECT criado_em,id FROM lote_arquivos WHERE id=$4 AND lote_id=$2))
            ORDER BY f.criado_em,f.id LIMIT $5""",tenant,lote_id,estado,cursor,limite+1)
        itens=[{**{k:r[k] for k in ('anexo_id','nome','zip','formato','tamanho_bytes','estado','motivo','criado_em','lido_em','tipo_documento','paginas')},
                'arquivo_id':r['id']} for r in rows[:limite]]
        return _json({'itens':itens,'proximo_cursor':rows[limite-1]['id'] if len(rows)>limite else None})
    except L.Recusa as e: return _erro(e)


@router.get('/tenants/{tenant}/lotes/{lote_id}/notas')
async def notas(request:Request,tenant:str,lote_id:str):
    """Notas lidas, já juntando PDF e XML da mesma nota (até 400 arquivos: sem paginação)."""
    db=request.app.state.db;await _empresa(db,tenant)
    try:
        lote=await _lote(db,tenant,lote_id)
        itens=await L.notas(db,tenant,lote_id)
        return _json({'lote_id':lote['id'],'estado':lote['estado'],'total':len(itens),'itens':itens})
    except L.Recusa as e: return _erro(e)


@router.post('/tenants/{tenant}/lotes/{lote_id}/fechar')
async def fechar(request:Request,tenant:str,lote_id:str,actor=Depends(painel)):
    """Não aceita mais arquivo; termina de ler o que falta e fica `concluido`. 409 sem nenhum arquivo aceito."""
    db=request.app.state.db;await _empresa(db,tenant)
    try:
        async with db.acquire() as conn,conn.transaction():
            lote=await conn.fetchrow('SELECT * FROM lotes WHERE tenant_id=$1 AND id=$2 FOR UPDATE',tenant,lote_id)
            if lote is None or lote['estado']=='apagado': raise L.Recusa(404,'lote_id')
            if lote['estado']!='aberto': raise L.Recusa(409,'lote_id','lote_fechado')
            if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM lote_arquivos WHERE lote_id=$1 AND estado NOT IN ('recusado','duplicado'))",lote_id):
                raise L.Recusa(409,'lote_id','lote_vazio')
            await conn.execute("UPDATE lotes SET estado='lendo',fechado_em=now() WHERE id=$1",lote_id)
            await auditar(conn,tenant,actor or 'painel','lote.fechado',lote_id)
        # Já lido tudo: conclui na hora.
        await L._concluir(db)
        return _json(await L.resumo(db,await _lote(db,tenant,lote_id)),202)
    except L.Recusa as e: return _erro(e)


@router.delete('/tenants/{tenant}/lotes/{lote_id}')
async def apagar(request:Request,tenant:str,lote_id:str,actor=Depends(painel)):
    """Apaga os arquivos e as leituras do lote (LGPD). Auditado."""
    db=request.app.state.db;await _empresa(db,tenant)
    try:
        lote=await _lote(db,tenant,lote_id)
        if lote['estado']!='apagado': await L.apagar(db,tenant,lote,actor or 'painel')
        return _json(await L.resumo(db,await _lote(db,tenant,lote_id)))
    except L.Recusa as e: return _erro(e)
