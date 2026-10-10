"""Rotas de carrinho e pré-pedido para as Skills do agente (X-API-Key, escopo ferramentas:pedidos)
e consulta administrativa. A empresa vem sempre da chave, nunca de quem chama."""
from typing import Literal
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from starlette.responses import JSONResponse
from nucleo.autenticacao import contexto_da_chave,painel
from nucleo.banco import Repositorio
from nucleo.webhooks import emitir
from admin.servico import auditar
from modulos import habilitado
from . import servico,avisos

router=APIRouter()
chave=contexto_da_chave('ferramentas:pedidos')


class Estrito(BaseModel):
    model_config=ConfigDict(extra='forbid')


class NovoItem(Estrito):
    item_id:int=Field(gt=0)
    quantidade:int=Field(1,ge=1,le=servico.MAX_QUANTIDADE)
    observacao:str|None=Field(None,max_length=300)


class MudarItem(Estrito):
    quantidade:int|None=Field(None,ge=0,le=servico.MAX_QUANTIDADE)
    observacao:str|None=Field(None,max_length=300)


class Entrega(Estrito):
    unidade:str|None=Field(None,pattern=r'^[a-z0-9][a-z0-9_]{0,39}$')
    modalidade:Literal['delivery','retirada']|None=None
    endereco:str|None=Field(None,max_length=500)
    horario:str|None=Field(None,max_length=100)
    observacao:str|None=Field(None,max_length=500)
    maioridade_confirmada:bool|None=None


class Fechar(Estrito):
    conversation_id:str=Field(min_length=1,max_length=300)


class Cancelar(Estrito):
    conversation_id:str=Field(min_length=1,max_length=300)
    motivo:str|None=Field(None,max_length=300)


async def conversa_da_chave(request,ctx,conversa):
    """Módulo ligado na empresa e no modelo do agente; conversa existente e do agente da chave."""
    repo=Repositorio(request.app.state.db);t=ctx['tenant_id']
    await habilitado(repo,t,'pedidos')
    conv=await repo.obter(t,'conversas',conversa)
    if not conv or (ctx['agente_id'] and conv['agente_id']!=ctx['agente_id']): raise HTTPException(404,'NOT_FOUND')
    cfg=await request.app.state.configuracoes.efetiva(t,conv['agente_id'])
    modelo=await repo.modelo(t,cfg['modelo_id'],cfg['modelo_versao'])
    if 'pedidos' not in modelo['modulos']: raise HTTPException(404,'NOT_FOUND')
    return t


def recusa(r,carrinho=None):
    """Recusa de regra de negócio: envelope padrão + motivo para o agente + carrinho atual."""
    corpo={'erro':{'codigo':{404:'NOT_FOUND',409:'CONFLICT'}.get(r.status,'VALIDATION_ERROR'),'mensagem':r.mensagem,
                   'detalhes':{'campo':r.campo,'motivo':r.motivo}}}
    if carrinho is not None: corpo['carrinho']=carrinho
    return JSONResponse(corpo,status_code=r.status,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


async def escrever(request,tenant,conversa,operacao):
    """Roda a operação numa transação e devolve o carrinho completo atualizado (ou a recusa com ele)."""
    async with request.app.state.db.acquire() as conn:
        try:
            async with conn.transaction():
                await operacao(conn)
        except servico.Recusa as r:
            return recusa(r,await servico.retrato(conn,tenant,conversa))
        return {'ok':True,'carrinho':await servico.retrato(conn,tenant,conversa)}


@router.get('/api/v1/carrinho/{conversation_id}')
async def ver(request:Request,conversation_id:str,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    return {'ok':True,'carrinho':await servico.retrato(request.app.state.db,t,conversation_id)}


@router.post('/api/v1/carrinho/{conversation_id}/itens')
async def adicionar(request:Request,conversation_id:str,dados:NovoItem,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    return await escrever(request,t,conversation_id,lambda c: servico.adicionar(c,t,conversation_id,dados.item_id,dados.quantidade,dados.observacao))


@router.patch('/api/v1/carrinho/{conversation_id}/itens/{linha_id}')
async def alterar(request:Request,conversation_id:str,linha_id:int,dados:MudarItem,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    return await escrever(request,t,conversation_id,lambda c: servico.alterar(c,t,conversation_id,linha_id,dados.quantidade,dados.observacao,'observacao' in dados.model_fields_set))


@router.delete('/api/v1/carrinho/{conversation_id}/itens/{linha_id}')
async def remover(request:Request,conversation_id:str,linha_id:int,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    return await escrever(request,t,conversation_id,lambda c: servico.remover(c,t,conversation_id,linha_id))


@router.delete('/api/v1/carrinho/{conversation_id}')
async def esvaziar(request:Request,conversation_id:str,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    return await escrever(request,t,conversation_id,lambda c: servico.esvaziar(c,t,conversation_id))


@router.put('/api/v1/carrinho/{conversation_id}/entrega')
async def entrega(request:Request,conversation_id:str,dados:Entrega,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    enviados=dados.model_dump(include=dados.model_fields_set)
    return await escrever(request,t,conversation_id,lambda c: servico.entrega(c,t,conversation_id,enviados))


def resumo_evento(p):
    """Dados do pedido para webhooks de saída: sem telefone nem endereço (ficam no painel)."""
    r=servico.retrato_pedido(p)
    return {'numero':r['numero'],'status':r['status'],'conversation_id':r['conversation_id'],'unidade':r['unidade'],'modalidade':r['modalidade'],
            'itens':[{'codigo':i['codigo'],'nome':i['nome'],'quantidade':i['quantidade'],'preco_unitario':i['preco_unitario']} for i in r['itens']],
            'subtotal':r['subtotal'],'taxa_entrega':r['taxa_entrega'],'total':r['total'],'tem_alcoolico':r['tem_alcoolico']}


@router.post('/api/v1/pedidos',status_code=201)
async def fechar(request:Request,dados:Fechar,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,dados.conversation_id);conversa=dados.conversation_id
    async with request.app.state.db.acquire() as conn:
        try:
            async with conn.transaction():
                p=await servico.fechar(conn,t,conversa)
                if p is not None:
                    await auditar(conn,t,'agente','pedido.criado',p['numero'])
                    await emitir(conn,t,'pedido.criado',resumo_evento(p))
                    # Unidade com WhatsApp de pedidos recebe o resumo; sem número, o pedido fica só no painel.
                    await avisos.avisar_unidade_do_pedido(conn,t,p,servico.retrato_pedido(p)['itens'])
        except servico.Recusa as r:
            return recusa(r,await servico.retrato(conn,t,conversa))
        if p is None:
            # Preço mudou no catálogo: o carrinho foi atualizado e volta para o cliente confirmar.
            return recusa(servico.Recusa('preco_alterado',409),await servico.retrato(conn,t,conversa))
        return {'ok':True,'pedido':servico.retrato_pedido(p),'carrinho':await servico.retrato(conn,t,conversa)}


@router.get('/api/v1/pedidos/{numero}')
async def status(request:Request,numero:str,conversation_id:str,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    try: p=await servico.da_conversa(request.app.state.db,t,conversation_id,numero)
    except servico.Recusa as r: return recusa(r)
    return {'ok':True,'pedido':servico.retrato_pedido(p)}


@router.post('/api/v1/pedidos/{numero}/cancelar')
async def cancelar(request:Request,numero:str,dados:Cancelar,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,dados.conversation_id)
    async with request.app.state.db.acquire() as conn:
        try:
            async with conn.transaction():
                antes=await servico.da_conversa(conn,t,dados.conversation_id,numero)
                p=await servico.cancelar(conn,t,dados.conversation_id,numero,dados.motivo)
                if antes['status']!='cancelado':
                    await auditar(conn,t,'cliente','pedido.cancelado',p['numero'])
                    await emitir(conn,t,'pedido.cancelado',resumo_evento(p))
        except servico.Recusa as r: return recusa(r)
    return {'ok':True,'pedido':servico.retrato_pedido(p)}


async def decidir_pedido(conn,tenant,numero,novo,motivo,por):
    """Decisão da unidade, igual para painel e WhatsApp: grava, audita, dispara webhook e avisa o cliente.
    Chamar dentro de transação. Devolve o pedido, None (não existe) ou levanta servico.JaDecidido."""
    p=await servico.decidir(conn,tenant,numero,novo,motivo,por)
    if p is None: return None
    await auditar(conn,tenant,por,'pedido.'+novo,p['numero'])
    await emitir(conn,tenant,'pedido.'+novo,{**resumo_evento(p),'motivo':p['motivo']})
    await avisos.avisar_cliente_do_pedido(conn,tenant,p)
    return p


class Decisao(Estrito):
    motivo:str|None=Field(None,max_length=300)


admin=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])


@admin.get('/tenants/{tenant_id}/pedidos')
async def lista(request:Request,tenant_id:str,status:str|None=None,unidade:str|None=None,limite:int=50,cursor:int=0):
    if not await Repositorio(request.app.state.db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    if not 1<=limite<=100 or cursor<0 or (status and status not in ('aguardando_loja','confirmado','recusado','cancelado')): raise HTTPException(400,'VALIDATION_ERROR')
    rows=await request.app.state.db.fetch('''SELECT * FROM pedidos WHERE tenant_id=$1 AND ($2::text IS NULL OR status=$2)
        AND ($3::text IS NULL OR unidade=$3) AND ($4=0 OR id<$4) ORDER BY id DESC LIMIT $5''',tenant_id,status,unidade,cursor,limite+1)
    # Painel vê também telefone do cliente (interno).
    itens=[{**servico.retrato_pedido(r),'id':r['id'],'cliente_telefone':r['cliente_telefone'],'decidido_por':r['decidido_por']} for r in rows[:limite]]
    return {'itens':itens,'proximo_cursor':rows[limite-1]['id'] if len(rows)>limite else None}


@admin.get('/tenants/{tenant_id}/pedidos/{numero}')
async def detalhe(request:Request,tenant_id:str,numero:str):
    r=await request.app.state.db.fetchrow('SELECT * FROM pedidos WHERE tenant_id=$1 AND numero=$2',tenant_id,numero)
    if not r: raise HTTPException(404,'NOT_FOUND')
    return {**servico.retrato_pedido(r),'id':r['id'],'cliente_telefone':r['cliente_telefone'],'decidido_por':r['decidido_por']}


async def _decidir_pelo_painel(request,tenant_id,numero,novo,motivo,actor):
    async with request.app.state.db.acquire() as conn:
        try:
            async with conn.transaction():
                p=await decidir_pedido(conn,tenant_id,numero,novo,motivo,actor)
        except servico.JaDecidido as ja:
            # Quem decidiu primeiro vale (painel ou WhatsApp da unidade).
            return JSONResponse({'erro':{'codigo':'CONFLICT','mensagem':'O pedido já foi decidido.','detalhes':{'campo':'status','status':ja.pedido['status']}}},
                                status_code=409,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    if p is None: raise HTTPException(404,'NOT_FOUND')
    return {**servico.retrato_pedido(p),'id':p['id'],'cliente_telefone':p['cliente_telefone'],'decidido_por':p['decidido_por']}


@admin.post('/tenants/{tenant_id}/pedidos/{numero}/confirmar')
async def confirmar(request:Request,tenant_id:str,numero:str,actor=Depends(painel)):
    return await _decidir_pelo_painel(request,tenant_id,numero,'confirmado',None,actor)


@admin.post('/tenants/{tenant_id}/pedidos/{numero}/recusar')
async def recusar(request:Request,tenant_id:str,numero:str,dados:Decisao|None=None,actor=Depends(painel)):
    return await _decidir_pelo_painel(request,tenant_id,numero,'recusado',dados.motivo if dados else None,actor)


@admin.get('/tenants/{tenant_id}/avisos')
async def lista_avisos(request:Request,tenant_id:str,limite:int=50):
    """Avisos internos (para a unidade, alerta de transbordo e avisos de pedido ao cliente) e o que aconteceu com cada um."""
    if not await Repositorio(request.app.state.db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    if not 1<=limite<=200: raise HTTPException(400,'VALIDATION_ERROR')
    rows=await request.app.state.db.fetch('SELECT id,tipo,destino,conversation_id,referencia,status,tentativas,erro,criado_em,enviado_em FROM avisos_internos WHERE tenant_id=$1 ORDER BY id DESC LIMIT $2',tenant_id,limite)
    return {'itens':[dict(r) for r in rows]}


async def mensagem_de_numero_interno(conn,tenant,telefone,unidades,texto,message_id):
    """Mensagem vinda de um WhatsApp de unidade ou do número de alerta: nunca vira conversa de cliente.
    Se for "OK <numero>" / "NÃO <numero> motivo" de uma unidade dona do pedido, decide o pedido e responde."""
    comando=servico.comando_da_unidade(texto)
    if not comando or not unidades: return {'ok':True,'ignorado':True,'interno':True}
    novo,numero,motivo=comando;por='unidade:whatsapp'
    ref=str(message_id or f'{numero}:{novo}')
    p=await conn.fetchrow('SELECT * FROM pedidos WHERE tenant_id=$1 AND numero=$2',tenant,numero)
    if not p or p['unidade'] not in unidades:
        # Número inexistente ou de outra unidade: não revela nada além de "não encontrado".
        await avisos.enfileirar(conn,tenant,'resposta_unidade',ref,f'Pedido {numero} não encontrado para esta unidade.',telefone)
        return {'ok':True,'interno':True,'comando':novo,'resultado':'nao_encontrado'}
    try:
        async with conn.transaction():
            p=await decidir_pedido(conn,tenant,numero,novo,motivo,por)
        resposta=f'Pedido {numero} {"confirmado ✅" if novo=="confirmado" else "recusado ❌"}. O cliente foi avisado.';resultado=novo
    except servico.JaDecidido as ja:
        resposta=f'Pedido {numero} já estava {ja.pedido["status"].replace("_"," ")}; nada foi alterado.';resultado='ja_decidido'
    await avisos.enfileirar(conn,tenant,'resposta_unidade',ref,resposta,telefone)
    return {'ok':True,'interno':True,'comando':novo,'resultado':resultado}
