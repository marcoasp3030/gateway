"""Avisos internos (Fase 4, bloco 3): caixa de saída para o que o gateway envia por conta própria.

O aviso é gravado na mesma transação do fato (pedido fechado, pedido decidido, conversa aguardando
atendente) e enviado depois pelo agendador. A recepção nunca chama o canal. Aviso para unidade ou
alerta só sai de verdade quando o agente do canal está com envio real; senão fica "simulado"."""
import logging,re,time
from canais import uazapi

log=logging.getLogger('gateway.avisos')
MAX_TENTATIVAS=3
MOTIVOS={'erro_agente':'erro do agente','tempo_esgotado':'agente não respondeu a tempo','circuito_aberto':'agente fora do ar',
         'entrega_incerta':'entrega incerta de uma resposta','transferido_pelo_agente':'transferência pedida pelo agente',
         'silencio_sem_motivo':'agente ficou sem responder'}


def reais(valor):
    if valor is None: return 'a confirmar'
    inteiro,centavos=f'{float(valor):.2f}'.split('.')
    return 'R$ '+re.sub(r'(?<=\d)(?=(\d{3})+$)','.',inteiro)+','+centavos


def mascarar_numero(telefone):
    d=''.join(c for c in str(telefone or '') if c.isdigit())
    return d[:4]+'****'+d[-4:] if len(d)>=10 else '(sem número)'


def texto_pedido_unidade(p,itens,nome_unidade):
    linhas=[f'🍱 NOVO PEDIDO {p["numero"]}','',f'Unidade: {nome_unidade}',f'Cliente: {p["cliente_nome"] or "(sem nome)"}',
            f'Telefone: {p["cliente_telefone"] or "(não informado)"}','']
    for i in itens:
        linhas.append(f'{i["quantidade"]}× {i["nome"]} ({reais(i["preco_unitario"])}) = {reais(i["subtotal"])}')
        if i.get('observacao'): linhas.append(f'   obs.: {i["observacao"]}')
    linhas+=['─────────────────',f'Subtotal: {reais(p["subtotal"])}',f'Taxa de entrega: {reais(p["taxa_entrega"]) if p["modalidade"]=="delivery" else "não se aplica"}',
             f'Total: {reais(p["total"])}','']
    linhas.append('Modalidade: '+(f'Entrega — {p["endereco"]}' if p['modalidade']=='delivery' else 'Retirada'))
    if p['horario']: linhas.append(f'Horário: {p["horario"]}')
    if p['observacao']: linhas.append(f'Observação: {p["observacao"]}')
    if p['tem_alcoolico']: linhas.append('Contém bebida alcoólica (cliente confirmou ter 18 anos ou mais).')
    linhas+=['',f'Para confirmar, responda: OK {p["numero"]}',f'Para recusar, responda: NÃO {p["numero"]} motivo']
    return '\n'.join(linhas)


def texto_cliente(p,nome_unidade):
    if p['status']=='confirmado':
        return f'Seu pedido nº {p["numero"]} foi confirmado pela unidade {nome_unidade}. ✅'
    base=f'Seu pedido nº {p["numero"]} não pôde ser aceito pela unidade {nome_unidade}.'
    if p['motivo']: base+=f'\nMotivo: {p["motivo"]}'
    return base+'\nOs itens voltaram para o seu carrinho, caso queira ajustar e pedir de novo.'


def texto_transbordo(telefone,nome,motivo,detalhe,pedido,painel_url):
    linhas=['⚠️ CONVERSA AGUARDANDO ATENDENTE',f'Cliente: {nome or "(sem nome)"} — {mascarar_numero(telefone)}',
            'Motivo: '+MOTIVOS.get(motivo,motivo)+(f' ({detalhe})' if detalhe else '')]
    if pedido: linhas.append(f'Pedido: {pedido["numero"]} ({pedido["status"]})')
    if painel_url: linhas.append(f'Painel: {painel_url}')
    return '\n'.join(linhas)


async def enfileirar(conn,tenant,tipo,referencia,texto,destino=None,conversa=None):
    """Grava o aviso (uma vez por fato). Chamar dentro da transação do fato."""
    return await conn.fetchval('''INSERT INTO avisos_internos(tenant_id,tipo,referencia,texto,destino,conversation_id)
        VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (tenant_id,tipo,referencia) DO NOTHING RETURNING id''',tenant,tipo,referencia,texto[:4000],destino,conversa)


async def nome_da_unidade(conn,tenant,codigo):
    return await conn.fetchval('SELECT nome FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2',tenant,codigo) or codigo


async def avisar_unidade_do_pedido(conn,tenant,p,itens):
    """Pedido novo → WhatsApp de pedidos da unidade. Sem número cadastrado, o pedido fica só no painel."""
    u=await conn.fetchrow('SELECT nome,whatsapp_pedidos FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2 AND ativo',tenant,p['unidade'])
    if not u or not u['whatsapp_pedidos']: return None
    return await enfileirar(conn,tenant,'pedido_unidade',p['numero'],texto_pedido_unidade(p,itens,u['nome']),u['whatsapp_pedidos'],p['conversation_id'])


async def avisar_cliente_do_pedido(conn,tenant,p):
    return await enfileirar(conn,tenant,'pedido_cliente',f'{p["numero"]}:{p["status"]}',
                            texto_cliente(p,await nome_da_unidade(conn,tenant,p['unidade'])),None,p['conversation_id'])


async def avisar_transbordo(conn,tenant,conversa,motivo,detalhe=None,painel_url=None):
    """Conversa entrou em "aguardando atendente" → aviso curto ao número de alerta da empresa (vazio = desligado)."""
    destino=await conn.fetchval('SELECT whatsapp_alerta_transbordo FROM tenants WHERE id=$1',tenant)
    if not destino: return None
    conv=await conn.fetchrow('SELECT telefone,nome,aguardando_atendente_desde FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
    if not conv: return None
    pedido=await conn.fetchrow("SELECT numero,status FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1",tenant,conversa)
    marca=conv['aguardando_atendente_desde'].isoformat() if conv['aguardando_atendente_desde'] else str(time.time())
    return await enfileirar(conn,tenant,'transbordo',conversa+'@'+marca,
        texto_transbordo(conv['telefone'],conv['nome'],motivo,(detalhe or '')[:120] or None,pedido,painel_url),destino,conversa)


async def _canal_de_envio(db,tenant,conversa):
    """Canal por onde o aviso sai: o da conversa, se for uazapi; senão o primeiro uazapi ativo da empresa com credencial."""
    if conversa:
        c=await db.fetchrow("""SELECT k.* FROM conversas v JOIN tenant_canais k ON k.tenant_id=v.tenant_id AND k.id=v.canal_id
            WHERE v.tenant_id=$1 AND v.conversation_id=$2 AND k.tipo='uazapi' AND k.ativo AND k.credenciais_enc ? 'fernet'""",tenant,conversa)
        if c: return c
    return await db.fetchrow("SELECT * FROM tenant_canais WHERE tenant_id=$1 AND tipo='uazapi' AND ativo AND credenciais_enc ? 'fernet' ORDER BY id LIMIT 1",tenant)


async def _enviar(pipeline,a):
    """Devolve o status final do aviso."""
    db=pipeline.db;tenant=a['tenant_id']
    if a['tipo']=='pedido_cliente':
        conv=await db.fetchrow('SELECT agente_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,a['conversation_id'])
        if not conv: return 'descartado'
        cfg=await pipeline.cfg.efetiva(tenant,conv['agente_id'])
        # Mesmo caminho das respostas do bot: respeita piloto, pausa do atendente e envio de teste.
        if not await pipeline.enviar(tenant,a['conversation_id'],conv['agente_id'],a['texto']): return 'descartado'
        return 'enviado' if cfg['send_mode']=='live' else 'simulado'
    import json
    canal=await _canal_de_envio(db,tenant,a['conversation_id'])
    if not canal: return 'simulado'
    canal=dict(canal)
    if isinstance(canal['credenciais_enc'],str): canal['credenciais_enc']=json.loads(canal['credenciais_enc'])
    cfg=await pipeline.cfg.efetiva(tenant,canal['agente_id'])
    if cfg['send_mode']!='live' or cfg['status']!='ativo': return 'simulado'
    await uazapi.enviar(pipeline.http,pipeline.cofre,tenant,canal,a['destino'],a['texto'])
    return 'enviado'


async def despachar(pipeline,limite=20):
    """Envia os avisos pendentes. Aviso preso em "enviando" (processo caiu no meio) não é reenviado."""
    db=pipeline.db
    await db.execute("UPDATE avisos_internos SET status='falhou',erro='envio_interrompido' WHERE status='enviando' AND criado_em<now()-interval '10 minutes'")
    enviados=0
    for linha in await db.fetch("SELECT id FROM avisos_internos WHERE status='pendente' ORDER BY id LIMIT $1",limite):
        a=await db.fetchrow("UPDATE avisos_internos SET status='enviando',tentativas=tentativas+1 WHERE id=$1 AND status='pendente' RETURNING *",linha['id'])
        if not a: continue
        try:
            final=await _enviar(pipeline,a)
            await db.execute("UPDATE avisos_internos SET status=$2,erro=NULL,enviado_em=CASE WHEN $2 IN ('enviado','simulado') THEN now() END WHERE id=$1",a['id'],final)
            enviados+=int(final=='enviado')
        except Exception as erro:
            # Falha antes de sair (ou recusa do provedor): tenta de novo até o limite. Sem detalhes do erro no log.
            # Aviso ao cliente nunca é reenviado (poderia chegar duas vezes); os internos tentam de novo.
            de_novo=a['tipo']!='pedido_cliente' and a['tentativas']<MAX_TENTATIVAS
            await db.execute("UPDATE avisos_internos SET status=$2,erro=$3 WHERE id=$1",a['id'],'pendente' if de_novo else 'falhou',type(erro).__name__)
            log.warning('Aviso interno não enviado (%s)%s',type(erro).__name__,'' if de_novo else '; desistindo')
    return enviados
