"""Avisos internos (Fase 4, bloco 3): caixa de saída para o que o gateway envia por conta própria.

O aviso é gravado na mesma transação do fato (pedido fechado, pedido decidido, conversa aguardando
atendente) e enviado depois pelo agendador. A recepção nunca chama o canal. Aviso para unidade ou
alerta só sai de verdade quando o agente do canal está com envio real; senão fica "simulado"."""
import logging,re,time
from canais import uazapi
from nucleo.contatos import nome_da_conversa

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


# Avisos de status ao cliente (PED1). Textos padrão; [[ … ]] é trecho opcional: some quando alguma variável de dentro está vazia.
TEXTOS={
    'confirmado':'Oba! Seu pedido nº {numero} foi confirmado pela unidade {unidade} ✅[[ Previsão: {previsao}.]]',
    'em_preparo':'Seu pedido nº {numero} já está sendo preparado 🍣',
    'pronto_retirada':'Seu pedido nº {numero} está pronto! Já pode retirar na unidade {unidade} 🛍️',
    'saiu_entrega':'Seu pedido nº {numero} saiu para entrega e já está a caminho 🛵',
    'concluido':'Pedido nº {numero} entregue. Bom apetite[[, {nome_cliente}]]! 😊',
    'recusado':'Poxa, a unidade {unidade} não conseguiu aceitar o pedido nº {numero}.[[ Motivo: {motivo}.]] Seus itens continuam no carrinho, se quiser ajustar e pedir de novo é só me chamar.',
    'cancelado':'Seu pedido nº {numero} foi cancelado pela unidade {unidade}.[[ Motivo: {motivo}.]] Se precisar, estou por aqui para ajudar.',
}
# Concluído em retirada nasce desligado; se a empresa ligar, o texto padrão não fala em entrega.
TEXTOS_RETIRADA={'concluido':'Pedido nº {numero} retirado. Bom apetite[[, {nome_cliente}]]! 😊'}
VARIAVEIS=('numero','unidade','previsao','motivo','nome_cliente')
MODALIDADES={'pronto_retirada':('retirada',),'saiu_entrega':('delivery',)}
EXEMPLO={'numero':'12345','unidade':'Pátio Guedala','previsao':'cerca de 40 minutos','motivo':'Faltou um ingrediente','nome_cliente':'Ana'}
MAX_TEXTO=1000


def modalidades(status): return MODALIDADES.get(status,('delivery','retirada'))


def texto_padrao(status,modalidade): return TEXTOS_RETIRADA.get(status,TEXTOS[status]) if modalidade=='retirada' else TEXTOS[status]


def validar_texto(texto):
    """Motivo pelo qual o texto não serve, ou None. Variável só das conhecidas; [[ ]] fechado e sem aninhar."""
    if not isinstance(texto,str) or not 1<=len(texto.strip())<=MAX_TEXTO: return 'tamanho'
    desconhecidas=sorted({v for v in re.findall(r'\{(\w*)\}',texto) if v not in VARIAVEIS})
    if desconhecidas: return 'variavel_desconhecida'
    sem_opcionais=re.sub(r'\[\[(?:(?!\[\[|\]\]).)*\]\]','',texto,flags=re.S)
    if '[[' in sem_opcionais or ']]' in sem_opcionais or '{' in re.sub(r'\{\w+\}','',texto) or '}' in re.sub(r'\{\w+\}','',texto): return 'formato'
    return None


async def configuracao(conn,tenant):
    """{(status, modalidade): (ativo, texto)} com o que a empresa definiu; ausente ou nulo = padrão."""
    return {(r['status'],r['modalidade']):(r['ativo'],r['texto']) for r in await conn.fetch('SELECT status,modalidade,ativo,texto FROM pedido_avisos_config WHERE tenant_id=$1',tenant)}


def efetivo(config,status,modalidade):
    """(ligado, texto) que vale para a empresa neste status e modalidade."""
    ativo,texto=config.get((status,modalidade),(None,None))
    return (ligado(status,modalidade) if ativo is None else ativo),(texto or texto_padrao(status,modalidade))


def ligado(status,modalidade):
    """Padrão: tudo ligado, menos "em preparo"; "concluído" só avisa em entrega (na retirada o cliente acabou de buscar)."""
    if status=='em_preparo': return False
    if status=='concluido': return modalidade=='delivery'
    return status in TEXTOS


def previsao(minutos):
    if not minutos: return ''
    if minutos<60: return f'cerca de {minutos} minuto'+('' if minutos==1 else 's')
    horas,resto=divmod(minutos,60)
    if resto: return f'cerca de {horas}h{resto:02d}'
    return f'cerca de {horas} hora'+('' if horas==1 else 's')


def primeiro_nome(nome):
    """Primeiro nome para o aviso; vazio se o que há não parece nome (ponto, emoji, telefone)."""
    partes=str(nome or '').split()
    primeiro=partes[0].strip('.,;:!?') if partes else ''
    return primeiro[:30] if len(primeiro)>=2 and all(c.isalpha() or c in "-'" for c in primeiro) else ''


def renderizar(modelo,valores):
    """Troca as variáveis; trecho [[ … ]] com variável vazia some inteiro. Variável fora da lista fica como está."""
    def opcional(m):
        trecho=m.group(1)
        usadas=re.findall(r'\{(\w+)\}',trecho)
        return trecho if all(str(valores.get(v) or '').strip() for v in usadas) else ''
    texto=re.sub(r'\[\[(.*?)\]\]',opcional,modelo,flags=re.S)
    return re.sub(r'\{(\w+)\}',lambda m: str(valores.get(m.group(1)) or '') if m.group(1) in VARIAVEIS else m.group(0),texto).strip()


def texto_cliente(p,nome_unidade,modelo=None):
    motivo=(p['motivo'] or '').strip().rstrip('.')
    return renderizar(modelo or texto_padrao(p['status'],p['modalidade']),{'numero':p['numero'],'unidade':nome_unidade,'previsao':previsao(p['previsao_min']) if p['status'] in ('confirmado','em_preparo') else '',
                                           'motivo':motivo,'nome_cliente':primeiro_nome(p['cliente_nome'])})


def numero_para_o_alerta(telefone,mascarar):
    """Telefone do cliente no alerta: inteiro para a equipe interna; mascarado quando o destino é um grupo externo."""
    if mascarar: return mascarar_numero(telefone)
    from nucleo.contatos import e164,formatado
    return formatado(e164(telefone)) or '(sem número)'


def texto_transbordo(telefone,nome,motivo,detalhe,pedido,painel_url,mascarar=True,equipe_ausente=False):
    linhas=['⚠️ CONVERSA AGUARDANDO ATENDENTE',f'Cliente: {nome or "(sem nome)"} — {numero_para_o_alerta(telefone,mascarar)}',
            'Motivo: '+MOTIVOS.get(motivo,motivo)+(f' ({detalhe})' if detalhe else '')]
    if equipe_ausente: linhas.append('Fora do horário da equipe: o bot continua atendendo e o cliente foi avisado.')
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
    """Aviso do status atual ao cliente, uma vez por pedido e status. Devolve 'enfileirado' ou 'desligado'."""
    ativo,modelo=efetivo(await configuracao(conn,tenant),p['status'],p['modalidade'])
    if not ativo: return 'desligado'
    # {nome_cliente} pelo nome de hoje (cadastro > informado > perfil); o gravado no pedido fica de reserva.
    atual,_=await nome_da_conversa(conn,tenant,p['conversation_id']) if p['conversation_id'] else (None,None)
    await enfileirar(conn,tenant,'pedido_cliente',f'{p["numero"]}:{p["status"]}',
                     texto_cliente({**dict(p),'cliente_nome':atual or p['cliente_nome']},await nome_da_unidade(conn,tenant,p['unidade']),modelo),None,p['conversation_id'])
    return 'enfileirado'


# Falha em que o provedor recusou a chamada: a mensagem com certeza não saiu. Qualquer outra pode ter saído.
RECUSA_CERTA='recusado_pelo_provedor'


def incerto(erro): return erro!=RECUSA_CERTA


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
    pedido=await conn.fetchrow("SELECT numero,status FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1",tenant,conversa)
    marca=conv['aguardando_atendente_desde'].isoformat() if conv['aguardando_atendente_desde'] else str(time.time())
    nome,_=await nome_da_conversa(conn,tenant,conversa)
    return await enfileirar(conn,tenant,'transbordo',conversa+'@'+marca,
        texto_transbordo(conv['telefone'],nome,motivo,(detalhe or '')[:120] or None,pedido,painel_url,
                         bool((cfg or {}).get('transbordo_destino_externo')),equipe_ausente),destino,conversa)


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
        # Aviso transacional (PED1): sai mesmo com o bot pausado por atendente; respeita piloto e envio de teste.
        numero,_,status=a['referencia'].partition(':')
        return await pipeline.enviar_transacional(tenant,a['conversation_id'],a['texto'],{'numero':numero,'status':status})
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
            await db.execute("UPDATE avisos_internos SET status=$2,erro=$3 WHERE id=$1",a['id'],'pendente' if de_novo else 'falhou',
                             RECUSA_CERTA if getattr(erro,'certo',False) else type(erro).__name__)
            log.warning('Aviso interno não enviado (%s)%s',type(erro).__name__,'' if de_novo else '; desistindo')
    return enviados
