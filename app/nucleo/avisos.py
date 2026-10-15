"""Avisos internos (Fase 4, bloco 3; no núcleo desde o MULTI1-b): caixa de saída para o que o gateway envia por conta própria.

O aviso é gravado na mesma transação do fato (conversa aguardando atendente, solicitação à equipe, e o que os módulos
avisam — pedido para a unidade, status do pedido ao cliente) e enviado depois pelo agendador. A recepção nunca chama o canal.
Aviso para unidade ou alerta só sai de verdade quando o agente do canal está com envio real; senão fica "simulado"."""
import json,logging,time
from canais import uazapi
from nucleo.contatos import nome_da_conversa

log=logging.getLogger('gateway.avisos')
MAX_TENTATIVAS=3
MOTIVOS={'erro_agente':'erro do agente','tempo_esgotado':'agente não respondeu a tempo','circuito_aberto':'agente fora do ar',
         'entrega_incerta':'entrega incerta de uma resposta','transferido_pelo_agente':'transferência pedida pelo agente',
         'silencio_sem_motivo':'agente ficou sem responder'}
# Falha em que o provedor recusou a chamada: a mensagem com certeza não saiu. Qualquer outra pode ter saído.
RECUSA_CERTA='recusado_pelo_provedor'


def incerto(erro): return erro!=RECUSA_CERTA


def mascarar_numero(telefone):
    d=''.join(c for c in str(telefone or '') if c.isdigit())
    return d[:4]+'****'+d[-4:] if len(d)>=10 else '(sem número)'


def numero_para_o_alerta(telefone,mascarar):
    """Telefone do cliente no alerta: inteiro para a equipe interna; mascarado quando o destino é um grupo externo."""
    if mascarar: return mascarar_numero(telefone)
    from nucleo.contatos import e164,formatado
    return formatado(e164(telefone)) or '(sem número)'


def texto_transbordo(telefone,nome,motivo,detalhe,extras,painel_url,mascarar=True,equipe_ausente=False):
    """`extras`: linhas que os módulos acrescentam (ex.: "Pedido: 48213 (aguardando_loja)")."""
    linhas=['⚠️ CONVERSA AGUARDANDO ATENDENTE',f'Cliente: {nome or "(sem nome)"} — {numero_para_o_alerta(telefone,mascarar)}',
            'Motivo: '+MOTIVOS.get(motivo,motivo)+(f' ({detalhe})' if detalhe else '')]
    if equipe_ausente: linhas.append('Fora do horário da equipe: o bot continua atendendo e o cliente foi avisado.')
    linhas+=list(extras or [])
    if painel_url: linhas.append(f'Painel: {painel_url}')
    return '\n'.join(linhas)


async def enfileirar(conn,tenant,tipo,referencia,texto,destino=None,conversa=None):
    """Grava o aviso (uma vez por fato). Chamar dentro da transação do fato."""
    return await conn.fetchval('''INSERT INTO avisos_internos(tenant_id,tipo,referencia,texto,destino,conversation_id)
        VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (tenant_id,tipo,referencia) DO NOTHING RETURNING id''',tenant,tipo,referencia,texto[:4000],destino,conversa)


async def destino_do_alerta(conn,tenant,cfg=None,conversa=None):
    """(telefone de alerta da equipe ou None, pendência ou None). T1: vale a configuração `transbordo_alerta`
    (com herança) e, na falta dela, o número antigo da empresa. O número da própria instância não serve de destino:
    o alerta não é enviado e a pendência fica no log e na auditoria da empresa."""
    destino=(cfg or {}).get('transbordo_alerta')
    if not destino:
        destino=await conn.fetchval('SELECT coalesce(c.transbordo_alerta,t.whatsapp_alerta_transbordo) FROM tenants t LEFT JOIN tenant_config c ON c.tenant_id=t.id WHERE t.id=$1',tenant)
    if not destino: return None,None
    if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM tenant_canais WHERE tenant_id=$1 AND numero_instancia=$2)',tenant,destino):
        log.warning('Alerta à equipe não enviado: o telefone de alerta é o número da própria instância')
        from admin.servico import auditar
        await auditar(conn,tenant,'sistema','transbordo.alerta_pendente',conversa or tenant)
        return None,'numero_da_instancia'
    return destino,None


async def avisar_transbordo(conn,tenant,conversa,motivo,detalhe=None,painel_url=None,cfg=None,equipe_ausente=False):
    """Conversa entrou em "aguardando atendente" → aviso curto ao telefone de alerta da equipe (vazio = desligado)."""
    destino,_=await destino_do_alerta(conn,tenant,cfg,conversa)
    if not destino: return None
    conv=await conn.fetchrow('SELECT telefone,nome,aguardando_atendente_desde FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
    if not conv: return None
    from modulos import ganchos
    extras=[linha for _,f in ganchos('resumo_para_alerta') for linha in await f(conn,tenant,conversa)]
    marca=conv['aguardando_atendente_desde'].isoformat() if conv['aguardando_atendente_desde'] else str(time.time())
    nome,_=await nome_da_conversa(conn,tenant,conversa)
    return await enfileirar(conn,tenant,'transbordo',conversa+'@'+marca,
        texto_transbordo(conv['telefone'],nome,motivo,(detalhe or '')[:120] or None,extras,painel_url,
                         bool((cfg or {}).get('transbordo_destino_externo')),equipe_ausente),destino,conversa)


async def _canal_de_envio(db,tenant,conversa):
    """Canal por onde o aviso sai: o da conversa, se for uazapi; senão o primeiro uazapi ativo da empresa com credencial."""
    if conversa:
        c=await db.fetchrow("""SELECT k.* FROM conversas v JOIN tenant_canais k ON k.tenant_id=v.tenant_id AND k.id=v.canal_id
            WHERE v.tenant_id=$1 AND v.conversation_id=$2 AND k.tipo='uazapi' AND k.ativo AND k.credenciais_enc ? 'fernet'""",tenant,conversa)
        if c: return c
    return await db.fetchrow("SELECT * FROM tenant_canais WHERE tenant_id=$1 AND tipo='uazapi' AND ativo AND credenciais_enc ? 'fernet' ORDER BY id LIMIT 1",tenant)


def _do_modulo(atributo):
    """Junta o que os módulos declaram no módulo de ganchos (ENVIO_DE_AVISO, AVISOS_SEM_REENVIO)."""
    import importlib
    from modulos import REGISTRO
    saida={}
    for m in REGISTRO.values():
        if not m.ganchos: continue
        valor=getattr(importlib.import_module(m.ganchos),atributo,None)
        if isinstance(valor,dict): saida.update(valor)
        elif valor: saida.update({x:True for x in valor})
    return saida


async def _enviar(pipeline,a):
    """Devolve o status final do aviso."""
    db=pipeline.db;tenant=a['tenant_id']
    proprio=_do_modulo('ENVIO_DE_AVISO').get(a['tipo'])
    if proprio: return await proprio(pipeline,a)
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
    enviados=0;sem_reenvio=_do_modulo('AVISOS_SEM_REENVIO')
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
            de_novo=a['tipo'] not in sem_reenvio and a['tentativas']<MAX_TENTATIVAS
            await db.execute("UPDATE avisos_internos SET status=$2,erro=$3 WHERE id=$1",a['id'],'pendente' if de_novo else 'falhou',
                             RECUSA_CERTA if getattr(erro,'certo',False) else type(erro).__name__)
            log.warning('Aviso interno não enviado (%s)%s',type(erro).__name__,'' if de_novo else '; desistindo')
    return enviados
