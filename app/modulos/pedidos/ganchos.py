"""Ganchos do módulo de pedidos (MULTI1-b, docs/ESPEC_MULTI1.md seção 4).

O núcleo não conhece carrinho nem pedido: ele chama estas funções pelo registro de módulos (modulos.ganchos) nos pontos
em que antes tinha a regra de delivery escrita nele. O que cada uma faz é exatamente o que o núcleo fazia."""
from . import anuncio,servico

# Pedido em andamento: a unidade ainda vai (ou está) atendendo.
PEDIDO_ATIVO=('aguardando_loja','confirmado','em_preparo','pronto_retirada','saiu_entrega')
# HOR2: recado ao agente quando ele transfere por "pedido_para_confirmar" sem nenhuma Skill ter recusado nada no turno.
AVISO_TRANSFERENCIA=('Você pediu transferência por pedido_para_confirmar, mas nenhuma Skill recusou nada neste turno. '
                     'Chame a Skill necessária (buscar_catalogo, adicionar_item) antes; só transfira se ela recusar, ou explique o motivo real.')
RECADOS_DE_RECUSA={'pedido_para_confirmar':AVISO_TRANSFERENCIA}


async def antes_do_turno(db,tenant,conversa,cfg):
    """UX3: carrinho parado há mais de carrinho_expira_h é arquivado antes de montar o pacote."""
    from admin.servico import auditar
    async with db.acquire() as conn,conn.transaction():
        if await servico.expirar(conn,tenant,conversa,cfg.get('carrinho_expira_h')):
            await auditar(conn,tenant,'sistema','carrinho.expirado',conversa)


async def numero_interno(db,tenant,identificador,m):
    """WhatsApp de pedidos de uma unidade e número de alerta nunca são clientes (Fase 4): a mensagem não vira conversa nem
    turno; se for "OK <numero>" / "NÃO <numero> motivo", decide o pedido. Devolve a resposta da recepção, ou None (é cliente)."""
    unidades=await servico.numero_interno(db,tenant,identificador)
    if unidades is None: return None
    if m.get('tipo') not in (None,'texto'): return {'ok':True,'ignorado':True,'interno':True}
    from .rotas import mensagem_de_numero_interno
    async with db.acquire() as conn,conn.transaction():
        return await mensagem_de_numero_interno(conn,tenant,identificador,unidades,m['texto'],m.get('message_id'))


async def em_andamento(db,tenant,conversa,para):
    """Há algo deste módulo em andamento na conversa? `para` diz quem pergunta:
    'fora_do_horario' (modo "só avisar": quem tem pedido ativo é atendido), 'aviso_espera' (carrinho aberto com itens),
    'solicitacao' (venda em andamento: carrinho com itens ou pedido ativo)."""
    carrinho="""EXISTS(SELECT 1 FROM carrinhos k JOIN carrinho_itens l ON l.tenant_id=k.tenant_id AND l.carrinho_id=k.id
        WHERE k.tenant_id=$1 AND k.conversation_id=$2 AND k.status='aberto')"""
    if para=='aviso_espera': return bool(await db.fetchval('SELECT '+carrinho,tenant,conversa))
    pedido='EXISTS(SELECT 1 FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 AND status=ANY($3::text[]))'
    if para=='fora_do_horario': return bool(await db.fetchval('SELECT '+pedido,tenant,conversa,list(PEDIDO_ATIVO)))
    return bool(await db.fetchval(f'SELECT {carrinho} OR {pedido}',tenant,conversa,list(PEDIDO_ATIVO)))


async def acoes_do_turno(db,tenant,conversa,inicio,fim):
    """UX2: o que a tentativa descartada já fez nas Skills de carrinho e pedido."""
    return await servico.acoes_do_turno(db,tenant,conversa,inicio,fim)


async def acoes_recentes(db,tenant,conversa,cfg):
    """UX3.1: chamadas de Skill de carrinho e pedido desta conversa nos últimos acoes_recentes_min minutos."""
    return await servico.acoes_recentes(db,tenant,conversa,cfg.get('acoes_recentes_min'))


async def _pedido_anunciado_sem_fechar(db,tenant,conversa,resultado,desde):
    """PED2: a resposta anuncia pedido fechado sem fechar_pedido de sucesso (desde o início da cadeia de turnos refeitos)?"""
    if not anuncio.sinal(resultado): return None
    return await anuncio.avaliar(db,tenant,conversa,resultado,desde)


def _resposta_segura(original):
    return {'resposta':[anuncio.TEXTO_AO_CLIENTE],'acao':'nenhuma','botoes':{'opcoes':[dict(anuncio.BOTAO)]},
            'segurado':{k:original.get(k) for k in ('resposta','acao','desfecho','motivo')}}


# Guarda da resposta: refeita uma vez com o recado; se repetir, a resposta é trocada pela segura e a operação é avisada.
GUARDAS_DA_RESPOSTA=({'coluna':'anuncio_falso','avaliar':_pedido_anunciado_sem_fechar,'recado':anuncio.AVISO_AO_AGENTE,
                      'resposta_segura':_resposta_segura,'auditoria':'pedido.anuncio_segurado',
                      'log':'Pedido anunciado sem fechar_pedido: resposta do agente segurada',
                      'alerta':('pedido anunciado sem fechar','O agente disse que fechou o pedido, mas fechar_pedido não teve sucesso. '
                                'O cliente recebeu o pedido para confirmar de novo.')},)


async def resumo_para_alerta(conn,tenant,conversa):
    """Linhas extras do alerta de transbordo: o último pedido da conversa."""
    pedido=await conn.fetchrow("SELECT numero,status FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1",tenant,conversa)
    return [f'Pedido: {pedido["numero"]} ({pedido["status"]})'] if pedido else []


async def resumo_das_conversas(db,tenant,conversas):
    """Para a lista de conversas do painel: {conversa: {'pedido_aberto': {numero,status}|None, 'unidade': código do último carrinho}}."""
    saida={c:{'pedido_aberto':None,'unidade':None} for c in conversas}
    for r in await db.fetch("""SELECT DISTINCT ON (conversation_id) conversation_id,numero,status FROM pedidos WHERE tenant_id=$1
            AND conversation_id=ANY($2::text[]) AND status NOT IN ('concluido','recusado','cancelado') ORDER BY conversation_id,id DESC""",tenant,list(conversas)):
        saida[r['conversation_id']]['pedido_aberto']={'numero':r['numero'],'status':r['status']}
    for r in await db.fetch("""SELECT DISTINCT ON (conversation_id) conversation_id,unidade FROM carrinhos WHERE tenant_id=$1
            AND conversation_id=ANY($2::text[]) AND unidade IS NOT NULL ORDER BY conversation_id,id DESC""",tenant,list(conversas)):
        saida[r['conversation_id']]['unidade']=r['unidade']
    return saida


async def anonimizar(conn,tenant,antiga,nova):
    """LGPD: carrinho e pedido acompanham a troca do identificador (ON UPDATE CASCADE); saem endereço, observações (a geral e a de
    cada item, LGPD1) e dados do cliente. O registro curto das Skills (sem endereço nem observação) passa para o identificador novo."""
    await conn.execute('UPDATE carrinhos SET endereco=NULL,observacao=NULL WHERE tenant_id=$1 AND conversation_id=$2',tenant,nova)
    await conn.execute('UPDATE carrinho_itens SET observacao=NULL WHERE tenant_id=$1 AND carrinho_id IN '
                       '(SELECT id FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2)',tenant,nova)
    # Cada item do pedido continua com o campo "observacao", vazio.
    await conn.execute("""UPDATE pedidos SET endereco=NULL,observacao=NULL,cliente_nome=NULL,cliente_telefone=NULL,
        itens=(SELECT coalesce(jsonb_agg(CASE WHEN jsonb_typeof(i)='object' AND i ? 'observacao' THEN i||'{"observacao":null}'::jsonb ELSE i END ORDER BY n),'[]'::jsonb)
               FROM jsonb_array_elements(itens) WITH ORDINALITY AS x(i,n))
        WHERE tenant_id=$1 AND conversation_id=$2""",tenant,nova)
    await conn.execute("UPDATE pedido_eventos SET motivo=NULL,por='anonimizado' WHERE tenant_id=$1 AND pedido_id IN (SELECT id FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2)",tenant,nova)
    await conn.execute('UPDATE skill_chamadas SET conversation_id=$3 WHERE tenant_id=$1 AND conversation_id=$2',tenant,antiga,nova)


async def metricas(db,tenant,agente):
    """PED2: turnos em que o agente anunciou pedido fechado sem fechar_pedido de sucesso (refeitos com aviso; segurados = texto não enviado)."""
    falsos=await db.fetchrow("""SELECT count(*) FILTER (WHERE anuncio_falso='refeito') AS refeitos,count(*) FILTER (WHERE anuncio_falso='segurado') AS segurados
        FROM turnos WHERE tenant_id=$1 AND anuncio_falso IS NOT NULL AND ($2::text IS NULL OR agente_id=$2)""",tenant,agente)
    return {'anuncios_falsos_refeitos':falsos['refeitos'],'anuncios_falsos_segurados':falsos['segurados']}


async def _aviso_ao_cliente(pipeline,a):
    # Aviso transacional (PED1): sai mesmo com o bot pausado por atendente; respeita piloto e envio de teste.
    numero,_,status=a['referencia'].partition(':')
    return await pipeline.enviar_transacional(a['tenant_id'],a['conversation_id'],a['texto'],{'numero':numero,'status':status},'aviso_pedido','pedido.aviso')


# Avisos internos deste módulo com envio próprio (os demais saem pelo canal da empresa) e que nunca são reenviados.
ENVIO_DE_AVISO={'pedido_cliente':_aviso_ao_cliente}
AVISOS_SEM_REENVIO=('pedido_cliente',)

