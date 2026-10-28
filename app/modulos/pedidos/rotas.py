"""Rotas de carrinho e pré-pedido para as Skills do agente (X-API-Key, escopo ferramentas:pedidos)
e consulta administrativa. A empresa vem sempre da chave, nunca de quem chama."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from starlette.responses import JSONResponse
from nucleo.autenticacao import contexto_da_chave,painel,exigir_admin
from nucleo.banco import Repositorio
from nucleo.webhooks import emitir
from nucleo import follow_up
from admin.servico import auditar
from modulos import habilitado
from . import servico,avisos

router=APIRouter()
chave=contexto_da_chave('ferramentas:pedidos')


class Estrito(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Escolha(Estrito):
    """CAT3-b: uma escolha num grupo de opções do item (ids devolvidos por buscar_catalogo)."""
    grupo_id:int=Field(gt=0)
    opcao_id:int=Field(gt=0)
    quantidade:int=Field(1,ge=1,le=20)


class NovoItem(Estrito):
    item_id:int=Field(gt=0)
    quantidade:int=Field(1,ge=1,le=servico.MAX_QUANTIDADE)
    observacao:str|None=Field(None,max_length=300)
    opcoes:list[Escolha]|None=Field(None,max_length=50)


class MudarItem(Estrito):
    quantidade:int|None=Field(None,ge=0,le=servico.MAX_QUANTIDADE)
    observacao:str|None=Field(None,max_length=300)
    # Substitui todas as escolhas da linha ([] = nenhuma).
    opcoes:list[Escolha]|None=Field(None,max_length=50)


class Entrega(Estrito):
    unidade:str|None=Field(None,pattern=r'^[a-z0-9][a-z0-9_]{0,39}$')
    modalidade:Literal['delivery','retirada']|None=None
    endereco:str|None=Field(None,max_length=500)
    horario:str|None=Field(None,max_length=100)
    # PED2: data e hora do pedido agendado (AAAA-MM-DDTHH:MM, no fuso da unidade). Conferido na regra, não aqui:
    # formato errado volta como horario_invalido, com a orientação para o agente.
    agendado_para:str|None=Field(None,max_length=40)
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
    # Módulo que vale para o agente da conversa: permitido no modelo, ligado na empresa e no agente (MULTI1-a).
    if 'pedidos' not in cfg['modulos']: raise HTTPException(404,'NOT_FOUND')
    return t


def recusa(r,carrinho=None):
    """Recusa de regra de negócio: envelope padrão + motivo para o agente + carrinho atual."""
    corpo={'erro':{'codigo':{404:'NOT_FOUND',409:'CONFLICT'}.get(r.status,'VALIDATION_ERROR'),'mensagem':r.mensagem,
                   'detalhes':{'campo':r.campo,'motivo':r.motivo,**r.detalhes}}}
    if carrinho is not None: corpo['carrinho']=carrinho
    return JSONResponse(corpo,status_code=r.status,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


async def ref_do_item(request,tenant,item_id=None,linha_id=None,quantidade=None):
    """Valores da chamada para o registro das Skills (DISP1): item, unidade e nome do catálogo — nada do cliente."""
    db=request.app.state.db
    if linha_id is not None:
        r=await db.fetchrow('SELECT i.id,i.unidade,i.nome FROM carrinho_itens l JOIN catalogo_itens i ON i.tenant_id=l.tenant_id AND i.id=l.item_id WHERE l.tenant_id=$1 AND l.id=$2',tenant,linha_id)
    else: r=await db.fetchrow('SELECT id,unidade,nome FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant,item_id)
    request.state.skill_ref={**({'linha_id':linha_id} if linha_id is not None else {}),'item_id':r['id'] if r else item_id,
                             'item_unidade':r['unidade'] if r else None,'item_nome':r['nome'] if r else None,'quantidade':quantidade}
    return r['nome'] if r else None


async def escrever(request,tenant,conversa,operacao,skill=None,dados=None):
    """Roda a operação numa transação e devolve o carrinho completo atualizado (ou a recusa com ele).
    Com `skill`, deixa o registro curto da chamada (para o turno refeito saber o que já foi feito)."""
    async with request.app.state.db.acquire() as conn:
        try:
            async with conn.transaction():
                await operacao(conn)
        except servico.Recusa as r:
            if skill: await servico.registrar_skill(conn,tenant,conversa,skill,dados or {},r.motivo)
            return recusa(r,await servico.retrato(conn,tenant,conversa))
        if skill: await servico.registrar_skill(conn,tenant,conversa,skill,dados or {},'ok')
        return {'ok':True,'carrinho':await servico.retrato(conn,tenant,conversa)}


@router.get('/api/v1/carrinho/{conversation_id}')
async def ver(request:Request,conversation_id:str,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    return {'ok':True,'carrinho':await servico.retrato(request.app.state.db,t,conversation_id)}


@router.post('/api/v1/carrinho/{conversation_id}/itens')
async def adicionar(request:Request,conversation_id:str,dados:NovoItem,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    nome=await ref_do_item(request,t,item_id=dados.item_id,quantidade=dados.quantidade)
    opcoes=[o.model_dump() for o in dados.opcoes or []]
    return await escrever(request,t,conversation_id,lambda c: servico.adicionar(c,t,conversation_id,dados.item_id,dados.quantidade,dados.observacao,opcoes),
        'adicionar_item',{'item':nome,'quantidade':dados.quantidade,'item_id':dados.item_id})


@router.patch('/api/v1/carrinho/{conversation_id}/itens/{linha_id}')
async def alterar(request:Request,conversation_id:str,linha_id:int,dados:MudarItem,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    nome=await ref_do_item(request,t,linha_id=linha_id,quantidade=dados.quantidade)
    opcoes=[o.model_dump() for o in dados.opcoes or []]
    return await escrever(request,t,conversation_id,lambda c: servico.alterar(c,t,conversation_id,linha_id,dados.quantidade,dados.observacao,'observacao' in dados.model_fields_set,
                                                                           opcoes,'opcoes' in dados.model_fields_set),
        'alterar_item',{'item':nome,'quantidade':dados.quantidade,'item_id':request.state.skill_ref['item_id']})


@router.delete('/api/v1/carrinho/{conversation_id}/itens/{linha_id}')
async def remover(request:Request,conversation_id:str,linha_id:int,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    nome=await ref_do_item(request,t,linha_id=linha_id)
    return await escrever(request,t,conversation_id,lambda c: servico.remover(c,t,conversation_id,linha_id),
        'remover_item',{'item':nome,'item_id':request.state.skill_ref['item_id']})


@router.delete('/api/v1/carrinho/{conversation_id}')
async def esvaziar(request:Request,conversation_id:str,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    return await escrever(request,t,conversation_id,lambda c: servico.esvaziar(c,t,conversation_id),'esvaziar_carrinho')


@router.put('/api/v1/carrinho/{conversation_id}/entrega')
async def entrega(request:Request,conversation_id:str,dados:Entrega,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    enviados=dados.model_dump(include=dados.model_fields_set)
    # Só os nomes dos campos informados: endereço e observação não ficam no registro.
    request.state.skill_ref={k:enviados.get(k) for k in ('unidade','modalidade','agendado_para') if k in enviados}
    r=await escrever(request,t,conversation_id,lambda c: servico.entrega(c,t,conversation_id,enviados),
        'definir_entrega',{'detalhe':', '.join(sorted(enviados))})
    # DISP1: com o horário novo, algum item do carrinho deixou de ser servido? O agente avisa o cliente na hora, não só no fechamento.
    if isinstance(r,dict):
        r['itens_indisponiveis']=r['carrinho']['itens_indisponiveis']
        # HOR2: a unidade (ou o relógio) mudou e o horário guardado não serve mais para ela? Avisa na hora, sem recusar a troca.
        r['aviso_horario']=r['carrinho']['horario_aviso']
    return r


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
                    # FUP1-a: pedido fechado → o follow-up da conversa sai.
                    await follow_up.cancelar(conn,t,conversa,'pedido_fechado',sem_esperar=True)
                    await auditar(conn,t,'agente','pedido.criado',p['numero'])
                    await emitir(conn,t,'pedido.criado',resumo_evento(p))
                    # Unidade com WhatsApp de pedidos recebe o resumo; sem número, o pedido fica só no painel.
                    await avisos.avisar_unidade_do_pedido(conn,t,p,servico.retrato_pedido(p)['itens'])
        except servico.Recusa as r:
            await servico.registrar_skill(conn,t,conversa,'fechar_pedido',{},r.motivo)
            return recusa(r,await servico.retrato(conn,t,conversa))
        if p is None:
            # Preço mudou no catálogo: o carrinho foi atualizado e volta para o cliente confirmar.
            await servico.registrar_skill(conn,t,conversa,'fechar_pedido',{},'preco_alterado')
            return recusa(servico.Recusa('preco_alterado',409),await servico.retrato(conn,t,conversa))
        await servico.registrar_skill(conn,t,conversa,'fechar_pedido',{'detalhe':'pedido '+str(p['numero'])},'ok')
        request.state.skill_ref={'numero':p['numero']}
        # `numero` também na raiz (PED2): é o que o agente diz ao cliente; não depende de ler dentro de `pedido`.
        return {'ok':True,'numero':p['numero'],'pedido':servico.retrato_pedido(p),'carrinho':await servico.retrato(conn,t,conversa)}


@router.get('/api/v1/pedidos')
async def ultimos_pedidos(request:Request,conversation_id:str,ctx=Depends(chave)):
    """consultar_pedido sem número: os últimos pedidos desta conversa (mais recente primeiro)."""
    t=await conversa_da_chave(request,ctx,conversation_id)
    return {'ok':True,'pedidos':await servico.ultimos(request.app.state.db,t,conversation_id)}


@router.get('/api/v1/pedidos/{numero}')
async def status(request:Request,numero:str,conversation_id:str,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,conversation_id)
    request.state.skill_ref={'numero':str(numero)[:12]}
    try: p=await servico.da_conversa(request.app.state.db,t,conversation_id,numero)
    except servico.Recusa as r: return recusa(r)
    return {'ok':True,'pedido':await servico.para_o_agente(request.app.state.db,t,p)}


@router.post('/api/v1/pedidos/{numero}/cancelar')
async def cancelar(request:Request,numero:str,dados:Cancelar,ctx=Depends(chave)):
    t=await conversa_da_chave(request,ctx,dados.conversation_id)
    request.state.skill_ref={'numero':str(numero)[:12]}
    async with request.app.state.db.acquire() as conn:
        try:
            async with conn.transaction():
                antes=await servico.da_conversa(conn,t,dados.conversation_id,numero)
                p=await servico.cancelar(conn,t,dados.conversation_id,numero,dados.motivo)
                if antes['status']!='cancelado':
                    await follow_up.cancelar(conn,t,dados.conversation_id,'pedido_fechado',sem_esperar=True)
                    await auditar(conn,t,'cliente','pedido.cancelado',p['numero'])
                    await emitir(conn,t,'pedido.cancelado',resumo_evento(p))
        except servico.Recusa as r:
            await servico.registrar_skill(conn,t,dados.conversation_id,'cancelar_pedido',{'detalhe':'pedido '+str(numero)[:12]},r.motivo)
            return recusa(r)
        await servico.registrar_skill(conn,t,dados.conversation_id,'cancelar_pedido',{'detalhe':'pedido '+str(p['numero'])},'ok')
    return {'ok':True,'pedido':servico.retrato_pedido(p)}


async def mudar_status(conn,tenant,numero,novo,por,origem,motivo=None,previsao_min=None):
    """Uma mudança de status, igual para painel e WhatsApp da unidade: grava, registra na linha do tempo, audita,
    dispara o webhook de saída e enfileira o aviso ao cliente. Chamar dentro de transação.
    Devolve o pedido, None (não existe) ou levanta servico.TransicaoInvalida."""
    feito=await servico.transicionar(conn,tenant,numero,novo,por,origem,motivo,previsao_min)
    if feito is None: return None
    p,evento=feito
    await auditar(conn,tenant,por,'pedido.'+novo,p['numero'])
    await emitir(conn,tenant,'pedido.'+novo,{**resumo_evento(p),'motivo':p['motivo']})
    aviso=await avisos.avisar_cliente_do_pedido(conn,tenant,p)
    if evento: await conn.execute('UPDATE pedido_eventos SET aviso=$3 WHERE tenant_id=$1 AND id=$2',tenant,evento,aviso)
    return p


async def decidir_pedido(conn,tenant,numero,novo,motivo,por,origem='painel',previsao_min=None):
    """Confirmação ou recusa pela unidade. Devolve o pedido, None (não existe) ou levanta servico.JaDecidido."""
    try: return await mudar_status(conn,tenant,numero,novo,por,origem,motivo,previsao_min)
    except servico.TransicaoInvalida as t: raise servico.JaDecidido(t.pedido)


class Decisao(Estrito):
    motivo:str|None=Field(None,max_length=300)
    previsao_min:int|None=Field(None,ge=1,le=600)


class MudancaDeStatus(Estrito):
    status:Literal['confirmado','em_preparo','pronto_retirada','saiu_entrega','concluido','recusado','cancelado']
    motivo:str|None=Field(None,max_length=300)
    previsao_min:int|None=Field(None,ge=1,le=600)


admin=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
from nucleo.contatos import ICONES
SELECAO="""SELECT p.*,u.nome AS unidade_nome,k.id AS canal_id,k.tipo AS canal_tipo,k.nome AS canal_nome,
        a.status AS aviso_status,e.aviso AS aviso_evento
    FROM pedidos p
    LEFT JOIN tenant_unidades u ON u.tenant_id=p.tenant_id AND u.codigo=p.unidade
    LEFT JOIN conversas v ON v.tenant_id=p.tenant_id AND v.conversation_id=p.conversation_id
    LEFT JOIN tenant_canais k ON k.tenant_id=v.tenant_id AND k.id=v.canal_id
    LEFT JOIN pedido_eventos e ON e.tenant_id=p.tenant_id AND e.pedido_id=p.id AND e.para=p.status
    LEFT JOIN avisos_internos a ON a.tenant_id=p.tenant_id AND a.tipo='pedido_cliente' AND a.referencia=p.numero||':'||p.status"""


def e164(telefone):
    d=''.join(c for c in str(telefone or '') if c.isdigit())
    return '+'+d if 10<=len(d)<=15 else None


def resumo_painel(r):
    """Pedido como o painel vê na lista (inclui telefone do cliente: uso interno)."""
    b=servico.retrato_pedido(r)
    aviso=None if r['aviso_evento'] is None else {'status':'desligado' if r['aviso_evento']=='desligado' else (r['aviso_status'] or 'pendente')}
    # LGPD1/AN1: em que condição o pedido nasceu (real, piloto, simulado, teste) e por onde entrou.
    return {**b,'id':r['id'],'origem':r['origem'],'canal':r['canal'],'cliente_telefone':r['cliente_telefone'],'decidido_por':r['decidido_por'],
            'unidade_nome':r['unidade_nome'] or r['unidade'],'numero_itens':sum(i['quantidade'] for i in b['itens']),
            'cliente':{'nome':r['cliente_nome'],'telefone':e164(r['cliente_telefone']),
                       'canal':{'id':r['canal_id'],'tipo':r['canal_tipo'],'nome':r['canal_nome'],'icone':ICONES.get(r['canal_tipo'])} if r['canal_id'] else None},
            'aviso':aviso}


async def detalhe_painel(db,tenant_id,numero):
    r=await db.fetchrow(SELECAO+' WHERE p.tenant_id=$1 AND p.numero=$2',tenant_id,numero)
    if not r: raise HTTPException(404,'NOT_FOUND')
    return {**resumo_painel(r),'entrega':{'modalidade':r['modalidade'],'endereco':r['endereco'],'horario':r['horario'],'agendado_para':r['agendado_para'],'observacao':r['observacao']},
            'status_descricao':servico.DESCRICAO[r['status']],'proximos_status':servico.proximos(r),'linha_do_tempo':await servico.linha_do_tempo(db,tenant_id,r)}


@admin.get('/tenants/{tenant_id}/pedidos')
async def lista(request:Request,tenant_id:str,status:str|None=None,unidade:str|None=None,modalidade:Literal['delivery','retirada']|None=None,
                desde:datetime|None=None,ate:datetime|None=None,busca:str|None=None,limite:int=50,cursor:int=0,origem:str|None=None):
    if not await Repositorio(request.app.state.db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    varios=[s for s in (status or '').split(',') if s] or None
    origens=[o for o in (origem or '').split(',') if o] or None
    if origens and any(o not in servico.ORIGENS for o in origens): raise HTTPException(400,{'campo':'origem','motivo':'opcao'})
    if (not 1<=limite<=100 or cursor<0 or (varios and any(s not in servico.STATUS for s in varios)) or (busca is not None and not 2<=len(busca.strip())<=100)
            or any(d is not None and d.tzinfo is None for d in (desde,ate))): raise HTTPException(400,'VALIDATION_ERROR')
    termo=busca.strip() if busca else None;digitos=''.join(c for c in termo or '' if c.isdigit())
    rows=await request.app.state.db.fetch(SELECAO+"""
        WHERE p.tenant_id=$1 AND ($2::text[] IS NULL OR p.status=ANY($2)) AND ($3::text IS NULL OR p.unidade=$3) AND ($4=0 OR p.id<$4)
        AND ($6::text IS NULL OR p.modalidade=$6) AND ($7::timestamptz IS NULL OR p.criado_em>=$7) AND ($8::timestamptz IS NULL OR p.criado_em<$8)
        AND ($9::text IS NULL OR strpos(lower(p.numero),lower($9))>0 OR strpos(lower(coalesce(p.cliente_nome,'')),lower($9))>0
             OR ($10<>'' AND strpos(coalesce(p.cliente_telefone,''),$10)>0))
        AND ($11::text[] IS NULL OR p.origem=ANY($11))
        ORDER BY p.id DESC LIMIT $5""",tenant_id,varios,unidade,cursor,limite+1,modalidade,desde,ate,termo,digitos,origens)
    return {'itens':[resumo_painel(r) for r in rows[:limite]],'proximo_cursor':rows[limite-1]['id'] if len(rows)>limite else None}


@admin.get('/tenants/{tenant_id}/pedidos/contagem')
async def contagem(request:Request,tenant_id:str):
    """Pedidos novos por unidade (para o alerta do painel) e quantos estão em andamento. Feita para consulta frequente."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    novos=await db.fetch("""SELECT p.unidade,coalesce(u.nome,p.unidade) AS nome,count(*) AS total,min(p.criado_em) AS mais_antigo_em
        FROM pedidos p LEFT JOIN tenant_unidades u ON u.tenant_id=p.tenant_id AND u.codigo=p.unidade
        WHERE p.tenant_id=$1 AND p.status='aguardando_loja' GROUP BY 1,2 ORDER BY 4""",tenant_id)
    andamento={r['status']:r['n'] for r in await db.fetch("""SELECT status,count(*) n FROM pedidos WHERE tenant_id=$1
        AND status IN ('confirmado','em_preparo','pronto_retirada','saiu_entrega') GROUP BY 1""",tenant_id)}
    falhas=await db.fetchval("""SELECT count(*) FROM avisos_internos a JOIN pedidos p ON p.tenant_id=a.tenant_id AND a.referencia=p.numero||':'||p.status
        WHERE a.tenant_id=$1 AND a.tipo='pedido_cliente' AND a.status='falhou'""",tenant_id)
    return {'aguardando_loja':{'total':sum(r['total'] for r in novos),'mais_antigo_em':novos[0]['mais_antigo_em'] if novos else None,
                               'por_unidade':[dict(r) for r in novos]},
            'em_andamento':{s:andamento.get(s,0) for s in ('confirmado','em_preparo','pronto_retirada','saiu_entrega')},
            'avisos_com_falha':falhas}


class AvisoConfig(Estrito):
    modalidade:Literal['delivery','retirada']|None=None
    ativo:bool|None=None
    texto:str|None=None


class Previa(Estrito):
    modalidade:Literal['delivery','retirada']|None=None
    texto:str|None=None


class Reenvio(Estrito):
    mesmo_incerto:bool=False


STATUS_COM_AVISO=tuple(avisos.TEXTOS)


async def _avisos_da_empresa(db,tenant_id):
    config=await avisos.configuracao(db,tenant_id);itens=[]
    for status in STATUS_COM_AVISO:
        for modalidade in avisos.modalidades(status):
            ativo,texto=avisos.efetivo(config,status,modalidade);proprio=config.get((status,modalidade),(None,None))
            itens.append({'status':status,'status_descricao':servico.DESCRICAO[status],'modalidade':modalidade,'ativo':ativo,'texto':texto,
                          'ativo_padrao':avisos.ligado(status,modalidade),'texto_padrao':avisos.texto_padrao(status,modalidade),
                          'padrao':proprio[0] is None and proprio[1] is None})
    return {'variaveis':list(avisos.VARIAVEIS),'max_texto':avisos.MAX_TEXTO,'itens':itens}


def _status_com_aviso(status,modalidade=None):
    if status not in STATUS_COM_AVISO: raise HTTPException(404,'NOT_FOUND')
    if modalidade and modalidade not in avisos.modalidades(status): raise HTTPException(400,'VALIDATION_ERROR')
    return [modalidade] if modalidade else list(avisos.modalidades(status))


def texto_invalido(motivo):
    return JSONResponse({'erro':{'codigo':'VALIDATION_ERROR','mensagem':'Texto do aviso inválido.','detalhes':{'campo':'texto','motivo':motivo,'variaveis':list(avisos.VARIAVEIS)}}},
                        status_code=400,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@admin.get('/tenants/{tenant_id}/pedidos/avisos')
async def avisos_config(request:Request,tenant_id:str):
    """Texto e liga/desliga do aviso ao cliente em cada status e modalidade, com o padrão ao lado."""
    if not await Repositorio(request.app.state.db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    return await _avisos_da_empresa(request.app.state.db,tenant_id)


@admin.put('/tenants/{tenant_id}/pedidos/avisos/{status}',dependencies=[Depends(exigir_admin)])
async def salvar_aviso(request:Request,tenant_id:str,status:str,dados:AvisoConfig,actor=Depends(painel)):
    """Só admin ou plataforma (X-Role). Sem `modalidade`, vale para as duas. `texto: null` volta ao padrão; `ativo: null` também."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    alvo=_status_com_aviso(status,dados.modalidade);enviados=dados.model_fields_set-{'modalidade'}
    if not enviados: raise HTTPException(400,'VALIDATION_ERROR')
    if dados.texto is not None:
        motivo=avisos.validar_texto(dados.texto)
        if motivo: return texto_invalido(motivo)
    async with db.acquire() as conn,conn.transaction():
        for modalidade in alvo:
            await conn.execute("""INSERT INTO pedido_avisos_config(tenant_id,status,modalidade,ativo,texto,atualizado_por) VALUES ($1,$2,$3,$4,$5,$8)
                ON CONFLICT (tenant_id,status,modalidade) DO UPDATE SET ativo=CASE WHEN $6 THEN EXCLUDED.ativo ELSE pedido_avisos_config.ativo END,
                texto=CASE WHEN $7 THEN EXCLUDED.texto ELSE pedido_avisos_config.texto END,atualizado_em=now(),atualizado_por=$8""",
                tenant_id,status,modalidade,dados.ativo,dados.texto.strip() if dados.texto else None,'ativo' in enviados,'texto' in enviados,actor[:100])
        await auditar(conn,tenant_id,actor,'pedido.aviso_configurado',status)
    return await _avisos_da_empresa(db,tenant_id)


@admin.post('/tenants/{tenant_id}/pedidos/avisos/{status}/previa')
async def previa_do_aviso(request:Request,tenant_id:str,status:str,dados:Previa|None=None):
    """Como o aviso ficaria, com valores de exemplo. Nada é enviado nem gravado. Sem `texto`, usa o que vale hoje para a empresa."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    modalidade=_status_com_aviso(status,dados.modalidade if dados else None)[0]
    modelo=dados.texto if dados and dados.texto is not None else avisos.efetivo(await avisos.configuracao(db,tenant_id),status,modalidade)[1]
    motivo=avisos.validar_texto(modelo)
    if motivo: return texto_invalido(motivo)
    exemplo={**avisos.EXEMPLO,'unidade':await db.fetchval('SELECT nome FROM tenant_unidades WHERE tenant_id=$1 AND ativo ORDER BY nome LIMIT 1',tenant_id) or avisos.EXEMPLO['unidade']}
    return {'status':status,'modalidade':modalidade,'texto':avisos.renderizar(modelo,exemplo),
            # O mesmo aviso quando não há previsão, motivo nem nome: mostra o efeito dos trechos [[ ]].
            'texto_sem_opcionais':avisos.renderizar(modelo,{**exemplo,'previsao':'','motivo':'','nome_cliente':''})}


@admin.post('/tenants/{tenant_id}/pedidos/{numero}/avisos/{status}/reenviar',status_code=202)
async def reenviar_aviso(request:Request,tenant_id:str,numero:str,status:str,dados:Reenvio|None=None,actor=Depends(painel)):
    """Reenvio manual de aviso que falhou. Nunca reenvia aviso entregue, nem de status que o pedido já deixou."""
    def recusa(motivo,atual):
        return JSONResponse({'erro':{'codigo':'CONFLICT','mensagem':'Este aviso não pode ser reenviado.','detalhes':{'campo':'aviso','status':atual,'motivo':motivo}}},
                            status_code=409,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    async with request.app.state.db.acquire() as conn,conn.transaction():
        p=await conn.fetchrow('SELECT id,status FROM pedidos WHERE tenant_id=$1 AND numero=$2 FOR UPDATE',tenant_id,numero)
        a=p and await conn.fetchrow("SELECT id,status,erro FROM avisos_internos WHERE tenant_id=$1 AND tipo='pedido_cliente' AND referencia=$2 FOR UPDATE",tenant_id,f'{numero}:{status}')
        if not p or not a: raise HTTPException(404,'NOT_FOUND')
        if a['status']!='falhou': return recusa('aviso_nao_falhou',a['status'])
        if p['status']!=status: return recusa('pedido_mudou_de_status',a['status'])
        if avisos.incerto(a['erro']) and not (dados and dados.mesmo_incerto): return recusa('entrega_incerta',a['status'])
        await conn.execute("UPDATE avisos_internos SET status='pendente',erro=NULL WHERE id=$1",a['id'])
        await auditar(conn,tenant_id,actor,'pedido.aviso_reenviado',numero)
    return {'ok':True,'aviso':{'status':'pendente'}}


@admin.get('/tenants/{tenant_id}/pedidos/{numero}')
async def detalhe(request:Request,tenant_id:str,numero:str):
    return await detalhe_painel(request.app.state.db,tenant_id,numero)


def conflito(pedido,motivo):
    return JSONResponse({'erro':{'codigo':'CONFLICT','mensagem':'Mudança de status não permitida.',
                                 'detalhes':{'campo':'status','status':pedido['status'],'motivo':motivo}}},
                        status_code=409,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


async def _mudar_pelo_painel(request,tenant_id,numero,novo,motivo,previsao_min,actor):
    motivo=(motivo or '').strip() or None
    # Cancelar exige dizer por quê; previsão só faz sentido quando a unidade assume o pedido.
    if (novo=='cancelado' and (not motivo or len(motivo)<3)) or (previsao_min is not None and novo not in servico.ACEITA_PREVISAO):
        raise HTTPException(400,'VALIDATION_ERROR')
    # PAPEL1-a: cancelar pedido que a unidade já confirmou é do supervisor para cima.
    from nucleo.autenticacao import pode
    if novo=='cancelado' and not pode(request,'supervisor'):
        atual=await request.app.state.db.fetchval('SELECT status FROM pedidos WHERE tenant_id=$1 AND numero=$2',tenant_id,numero)
        if atual and atual!='aguardando_loja': raise HTTPException(403,'FORBIDDEN')
    async with request.app.state.db.acquire() as conn:
        try:
            async with conn.transaction():
                p=await mudar_status(conn,tenant_id,numero,novo,actor,'painel',motivo,previsao_min)
        except servico.TransicaoInvalida as t:
            # Quem chegou primeiro vale (painel ou WhatsApp da unidade); o painel recarrega com o status atual.
            return conflito(t.pedido,t.motivo)
    if p is None: raise HTTPException(404,'NOT_FOUND')
    return await detalhe_painel(request.app.state.db,tenant_id,numero)


@admin.post('/tenants/{tenant_id}/pedidos/{numero}/status')
async def mudar(request:Request,tenant_id:str,numero:str,dados:MudancaDeStatus,actor=Depends(painel)):
    return await _mudar_pelo_painel(request,tenant_id,numero,dados.status,dados.motivo,dados.previsao_min,actor)


@admin.post('/tenants/{tenant_id}/pedidos/{numero}/confirmar')
async def confirmar(request:Request,tenant_id:str,numero:str,dados:Decisao|None=None,actor=Depends(painel)):
    return await _mudar_pelo_painel(request,tenant_id,numero,'confirmado',None,dados.previsao_min if dados else None,actor)


@admin.post('/tenants/{tenant_id}/pedidos/{numero}/recusar')
async def recusar(request:Request,tenant_id:str,numero:str,dados:Decisao|None=None,actor=Depends(painel)):
    return await _mudar_pelo_painel(request,tenant_id,numero,'recusado',dados.motivo if dados else None,None,actor)


@admin.get('/tenants/{tenant_id}/skills/registro')
async def registro_de_skills(request:Request,tenant_id:str,conversation_id:str|None=None,skill:str|None=None,limite:int=50,cursor:int=0,
                             turno_id:int|None=None):
    """Todas as chamadas de Skill da empresa (PED2), da mais recente para a mais antiga: nomes dos campos recebidos, status, motivo e tempo.
    ENC1-a: cada chamada diz o turno (turno_id; fora_do_turno=true quando não havia turno em andamento) e o atendimento; ?turno_id= filtra."""
    if not await Repositorio(request.app.state.db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    if not 1<=limite<=200 or cursor<0 or (turno_id is not None and turno_id<1): raise HTTPException(400,'VALIDATION_ERROR')
    rows=await request.app.state.db.fetch("""SELECT id,conversation_id,skill,metodo,campos,ref,status,motivo,duracao_ms,criado_em,turno_id,fora_do_turno,atendimento_id
        FROM skill_registro WHERE tenant_id=$1 AND ($2::text IS NULL OR conversation_id=$2) AND ($3::text IS NULL OR skill=$3) AND ($4=0 OR id<$4)
        AND ($6::bigint IS NULL OR turno_id=$6) ORDER BY id DESC LIMIT $5""",tenant_id,conversation_id,skill,cursor,limite+1,turno_id)
    import json
    return {'itens':[{**dict(r),'ref':json.loads(r['ref']) if isinstance(r['ref'],str) else r['ref']} for r in rows[:limite]],
            'proximo_cursor':rows[limite-1]['id'] if len(rows)>limite else None}


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
            p=await decidir_pedido(conn,tenant,numero,novo,motivo,por,'unidade_whatsapp')
        resposta=f'Pedido {numero} {"confirmado ✅" if novo=="confirmado" else "recusado ❌"}. O cliente foi avisado.';resultado=novo
    except servico.JaDecidido as ja:
        resposta=f'Pedido {numero} já estava {ja.pedido["status"].replace("_"," ")}; nada foi alterado.';resultado='ja_decidido'
    await avisos.enfileirar(conn,tenant,'resposta_unidade',ref,resposta,telefone)
    return {'ok':True,'interno':True,'comando':novo,'resultado':resultado}
