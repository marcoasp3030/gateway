"""FUP1-a: follow-up — pendência declarada pelo agente, agendamento, cancelamento e opt-out (docs/ESPEC_FUP1A.md).

Só ensaio: quando um follow-up vence, o gateway confere tudo de novo e registra o que TERIA feito (`ensaio_enviaria`) ou por que
não faria (`cancelado` + motivo). Nada é enviado e o Skyone não é chamado (o envio é do FUP1-b).

A fila fica no Postgres (`follow_up_tarefas`): nada se perde numa queda do Redis. Toda mudança numa tarefa acontece sob a trava
da conversa (pg_advisory_xact_lock), a mesma da recepção: mensagem do cliente e vencimento nunca se cruzam.
As regras que dependem de um módulo (pedido em andamento, unidade do carrinho) vêm dos ganchos `pendencia_valida` e
`unidade_da_conversa`; agente sem módulo que responda não arma no escopo "pedido em andamento"."""
import json
import logging
import re
import unicodedata
from datetime import datetime,timedelta,timezone
from admin.servico import auditar

log=logging.getLogger('gateway.follow_up')

# Tipos de pendência (lista fechada, decisão de 21/10). Tipo fora da lista vira "outro", com aviso.
TIPOS=('confirmar_pedido','escolher_item','informar_quantidade','informar_horario','informar_endereco','escolher_unidade',
       'informar_modalidade','outro')
# "Pedido em andamento" (padrão) aceita os tipos do pedido; "pergunta pendente" aceita também "outro".
TIPOS_DO_ESCOPO={'pedido_em_andamento':tuple(t for t in TIPOS if t!='outro'),'pergunta_pendente':TIPOS}
MAX_PERGUNTA=120
IDADE_MAXIMA=timedelta(hours=24)
# Até onde "adiar para a abertura" pode empurrar o vencimento (contado da criação da tarefa).
ADIAR_ATE=timedelta(hours=24)
VENCIDAS_POR_VEZ=100
# Estados que contam no limite do dia (o que saiu ou, no ensaio, teria saído).
CONTAM_NO_DIA=('ensaio_enviaria','gerando','enviado','incerto')
JANELA_PADRAO={d:[['09:00','22:00']] for d in ('seg','ter','qua','qui','sex','sab','dom')}

# ---------------- Limpeza da pergunta (sem dado pessoal) ----------------

_EMAIL=re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_DOCUMENTO=re.compile(r'(?<!\d)(?:\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)')
_CEP=re.compile(r'(?<!\d)\d{5}-\d{3}(?!\d)')
# Sequência com 8 dígitos ou mais (telefone, documento sem formato, cartão), com ou sem separadores.
_NUMERO=re.compile(r'\+?\(?\d[\d ().\-/]{6,}\d')
_ENDERECO=re.compile(r'\b(?:rua|r\.|avenida|av\.?|alameda|travessa|estrada|rodovia|pra[cç]a|largo|viela)\s+[^,.;:!?\n]{2,60}?'
                     r'(?:,?\s*(?:n[º°o.]?\s*)?\d{1,6}[a-z]?)?(?=\s*[,.;:!?\n]|\s*$)',re.I)


def _sem_acento(texto):
    return ''.join(c for c in unicodedata.normalize('NFKD',texto) if not unicodedata.combining(c))


def limpar_pergunta(texto,nomes=()):
    """A pergunta da pendência sem dado pessoal, em até MAX_PERGUNTA caracteres: e-mail, CPF/CNPJ, CEP, telefone e qualquer
    número longo, endereço (rua, avenida… com número) e o nome do cliente viram marcadores. Vazia → None."""
    if not isinstance(texto,str): return None
    texto=''.join(' ' if ord(c)<32 or ord(c)==127 else c for c in texto)
    texto=_EMAIL.sub('[email]',texto)
    texto=_DOCUMENTO.sub('[documento]',texto)
    texto=_CEP.sub('[cep]',texto)
    texto=_ENDERECO.sub('[endereço]',texto)
    texto=_NUMERO.sub(lambda m:'[número]' if sum(c.isdigit() for c in m.group())>=8 else m.group(),texto)
    for nome in nomes or ():
        for parte in str(nome or '').split():
            parte=parte.strip('.,;:!?')
            if len(parte)>=3:
                texto=re.sub(r'(?<!\w)'+re.escape(parte)+r'(?!\w)','[nome]',texto,flags=re.I)
    texto=' '.join(texto.split())
    if not texto: return None
    return texto if len(texto)<=MAX_PERGUNTA else texto[:MAX_PERGUNTA-1].rstrip()+'…'


def do_retorno(valor,nomes=()):
    """Campo `pendencia` do retorno do agente → {"tipo","pergunta"} (+ "aviso" se o tipo veio fora da lista), ou None.
    Ausente ou em formato errado não arma nada e nunca recusa a resposta."""
    if valor is None: return None
    if not isinstance(valor,dict):
        log.warning('pendencia ignorada: formato inválido');return None
    tipo=valor.get('tipo');saida={}
    if tipo not in TIPOS:
        nome=tipo if isinstance(tipo,str) and re.fullmatch(r'[a-z0-9_]{1,40}',tipo) else 'inválido'
        log.warning('pendencia.tipo fora da lista (%s); gravado como outro',nome)
        tipo='outro';saida['aviso']='tipo_fora_da_lista'
    return {'tipo':tipo,'pergunta':limpar_pergunta(valor.get('pergunta'),nomes),**saida}


# ---------------- Opt-out por frase (a mensagem inteira) ----------------

FRASES_OPT_OUT=frozenset('''pare
parar
para
chega
stop
sair
para de mandar mensagem
para de mandar mensagens
pare de mandar mensagem
pare de mandar mensagens
para de me mandar mensagem
para de me mandar mensagens
pare de me mandar mensagem
pare de me mandar mensagens
para de me chamar
pare de me chamar
para de me mandar mensagem por aqui
nao quero mais mensagens
nao quero mais mensagem
nao quero receber mensagens
nao quero receber mais mensagens
nao quero mais receber mensagens
nao quero receber mensagem
nao me mande mais mensagens
nao me mande mais mensagem
nao mande mais mensagens
nao me mandem mais mensagens
nao me chame mais
nao quero ser lembrado
nao quero lembrete
nao quero lembretes
sem mais mensagens
descadastrar
me descadastra
me tira da lista'''.split('\n'))
# Cortesia no começo ou no fim não muda o pedido ("pare, por favor").
CORTESIAS=('por favor','pfv','pf','pls','obrigado','obrigada','ok','tá','ta')


def _normalizar(texto):
    texto=_sem_acento(str(texto or '').lower())
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in texto).split())


def e_opt_out(texto):
    """A mensagem é SÓ um pedido para parar de receber mensagens? ("pare", "não quero mais mensagens"). Frase com mais
    conteúdo ("não quero mais esse item", "para de mandar o molho") não conta."""
    frase=_normalizar(texto)
    mudou=True
    while mudou and frase:
        mudou=False
        for c in CORTESIAS:
            c=_normalizar(c)
            if frase.endswith(' '+c): frase=frase[:-len(c)-1];mudou=True
            elif frase.startswith(c+' '): frase=frase[len(c)+1:];mudou=True
    return frase in FRASES_OPT_OUT


# ---------------- Banco ----------------

def _iso(quando):
    return quando.isoformat(timespec='seconds') if quando else None


async def _evento(conn,tenant,evento,t,**extra):
    from nucleo.webhooks import emitir
    pend=json.loads(t['pendencia']) if isinstance(t['pendencia'],str) else (t['pendencia'] or {})
    # Sem texto: a pergunta fica só no gateway e no painel.
    await emitir(conn,tenant,evento,{'conversation_id':t['conversation_id'],'atendimento_id':t['atendimento_id'],'agente_id':t['agente_id'],
                                     'follow_up_id':t['id'],'ordinal':t['ordinal'],'tipo':pend.get('tipo'),
                                     'previsto_em':_iso(t['previsto_em']),'estado':t['estado'],'motivo':t['motivo'],**extra})


async def cancelar(conn,tenant,conversa,motivo,actor='sistema',contato_id=None,sem_esperar=False):
    """Cancela as tarefas agendadas da conversa (ou de todas as conversas do contato, com `contato_id`). Devolve quantas.
    `sem_esperar`: para quem pode já ter a trava da auditoria sem ter a da conversa (fim do turno, encerramento): a tarefa que o
    agendador está conferindo neste instante é pulada (ele mesmo revalida e cancela), e a ordem das travas nunca se inverte."""
    pular=' FOR UPDATE SKIP LOCKED' if sem_esperar else ''
    rows=await conn.fetch(f"""UPDATE follow_up_tarefas SET estado='cancelado',motivo=$3,atualizado_em=now()
        WHERE tenant_id=$1 AND id IN (SELECT id FROM follow_up_tarefas WHERE tenant_id=$1 AND estado='agendado'
            AND (CASE WHEN $4::text IS NULL THEN conversation_id=$2 ELSE contato_id=$4 END){pular}) RETURNING *""",tenant,conversa,motivo,contato_id)
    for t in rows:
        await auditar(conn,tenant,actor,'follow_up.cancelado',t['conversation_id'])
        await _evento(conn,tenant,'follow_up.cancelado',t)
    return len(rows)


async def em_opt_out(conn,tenant,contato_id):
    if not contato_id: return False
    return bool(await conn.fetchval('SELECT EXISTS(SELECT 1 FROM follow_up_opt_out WHERE tenant_id=$1 AND contato_id=$2 AND removido_em IS NULL)',
                                    tenant,contato_id))


async def registrar_opt_out(conn,tenant,contato_id,origem,actor):
    """Contato pediu para não receber follow-up (vale entre atendimentos e agentes da empresa). Cancela o que estava agendado.
    Devolve True se o opt-out é novo."""
    if not contato_id: return False
    novo=bool(await conn.fetchval("""INSERT INTO follow_up_opt_out(tenant_id,contato_id,origem,actor) VALUES ($1,$2,$3,$4)
        ON CONFLICT (tenant_id,contato_id) DO UPDATE SET origem=EXCLUDED.origem,actor=EXCLUDED.actor,criado_em=now(),removido_em=NULL,removido_por=NULL
        WHERE follow_up_opt_out.removido_em IS NOT NULL RETURNING true""",tenant,contato_id,origem,actor[:100]))
    if novo: await auditar(conn,tenant,actor,'follow_up.opt_out',contato_id)
    await cancelar(conn,tenant,None,'opt_out',actor,contato_id=contato_id)
    return novo


async def remover_opt_out(conn,tenant,contato_id,actor):
    """Reativa o follow-up do contato (só pelo painel, admin ou plataforma). Devolve True se havia opt-out."""
    feito=bool(await conn.fetchval("""UPDATE follow_up_opt_out SET removido_em=now(),removido_por=$3
        WHERE tenant_id=$1 AND contato_id=$2 AND removido_em IS NULL RETURNING true""",tenant,contato_id,actor[:100]))
    if feito: await auditar(conn,tenant,actor,'follow_up.opt_out_removido',contato_id)
    return feito


async def opt_out_do_painel(conn,tenant,contato_id):
    r=await conn.fetchrow('SELECT origem,actor,criado_em,removido_em,removido_por FROM follow_up_opt_out WHERE tenant_id=$1 AND contato_id=$2',
                          tenant,contato_id)
    ativo=bool(r) and r['removido_em'] is None
    return {'ativo':ativo,'origem':r['origem'] if r else None,'criado_em':r['criado_em'] if r else None,'actor':r['actor'] if r else None,
            'removido_em':r['removido_em'] if r else None,'removido_por':r['removido_por'] if r else None}


def ligado(cfg):
    return cfg.get('follow_up_ativo')=='on' and int(cfg.get('follow_up_maximo') or 0)>=1


async def _do_dia(conn,tenant,contato_id,fuso,agora,sem=None,agendados=True):
    """Follow-ups do contato que saíram (no ensaio: que teriam saído) hoje, no fuso da empresa, somando agentes; com `agendados`,
    mais os que estão agendados. `sem` = id de uma tarefa que não conta."""
    if not contato_id: return 0
    return await conn.fetchval("""SELECT count(*) FROM follow_up_tarefas WHERE tenant_id=$1 AND contato_id=$2 AND id<>coalesce($6,0)
        AND ((estado='agendado' AND $7) OR (estado=ANY($3::text[]) AND (atualizado_em AT TIME ZONE $4)::date=($5::timestamptz AT TIME ZONE $4)::date))""",
        tenant,contato_id,list(CONTAM_NO_DIA),fuso,agora,sem,agendados)


async def _pendencia_valida(conn,tenant,conversa,atendimento_id,tipo,cfg):
    """Escopo "pedido em andamento": o módulo confirma (carrinho aberto com itens…). Sem módulo que responda: não vale."""
    from modulos import ganchos
    respostas=[await f(conn,tenant,conversa,atendimento_id,tipo) for _,f in ganchos('pendencia_valida',cfg.get('modulos') or ())]
    return bool(respostas) and all(respostas)


async def armar(conn,tenant,conversa,cfg,turno,resultado,status):
    """Fim do turno (pipeline._concluir), na transação e sob a trava da conversa. Arma o 1º follow-up se tudo vale (seção 2.2).
    Devolve (tarefa, None) ou (None, motivo de não armar)."""
    pend=(resultado or {}).get('pendencia')
    if not pend: return None,'sem_pendencia'
    if pend.get('aviso'): await auditar(conn,tenant,'agente','follow_up.tipo_fora_da_lista',conversa)
    if not ligado(cfg): return None,'desligado'
    if turno['origem']!='cliente' or status!='ok' or resultado.get('acao')!='nenhuma': return None,'turno'
    n=len(resultado.get('resposta') or [])
    if n==0: return None,'sem_resposta'
    enviadas=await conn.fetchval("""SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND turno_request_id=$2 AND parte BETWEEN 1 AND $3
        AND entrega='enviada'""",tenant,turno['request_id'],n)
    if enviadas!=n: return None,'resposta_incompleta'
    escopo=cfg.get('follow_up_escopo') or 'pedido_em_andamento'
    if pend['tipo'] not in TIPOS_DO_ESCOPO.get(escopo,()): return None,'tipo_fora_do_escopo'
    conv=await conn.fetchrow('SELECT * FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
    if conv['aguardando_atendente_desde'] or (conv['bot_pausado_ate'] and conv['bot_pausado_ate']>datetime.now(timezone.utc)): return None,'com_atendente'
    a=await conn.fetchrow("SELECT * FROM atendimentos WHERE tenant_id=$1 AND id=$2 AND estado='aberto'",tenant,conv['atendimento_id']) if conv['atendimento_id'] else None
    if a is None: return None,'encerrado'
    # O cliente já mandou outra coisa depois do lote deste turno: a mensagem dele vence.
    if await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='entrada'
            AND origem='cliente' AND processada_em IS NULL AND NOT (id=ANY($3::bigint[])))""",tenant,conversa,turno['mensagem_ids']): return None,'cliente_respondeu'
    if await em_opt_out(conn,tenant,conv['contato_id']): return None,'opt_out'
    if escopo=='pedido_em_andamento' and not await _pendencia_valida(conn,tenant,conversa,a['id'],pend['tipo'],cfg): return None,'pendencia_invalida'
    fuso=await conn.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant)
    agora=await conn.fetchval('SELECT now()')
    anterior=await conn.fetchval("SELECT id FROM follow_up_tarefas WHERE tenant_id=$1 AND atendimento_id=$2 AND estado='agendado'",tenant,a['id'])
    if await _do_dia(conn,tenant,conv['contato_id'],fuso,agora,anterior)>=int(cfg.get('follow_up_limite_dia') or 0): return None,'limite_do_dia'
    # Pendência nova substitui a anterior.
    if anterior: await cancelar(conn,tenant,conversa,'substituido')
    t=await conn.fetchrow("""INSERT INTO follow_up_tarefas(tenant_id,atendimento_id,conversation_id,agente_id,contato_id,ordinal,pendencia,previsto_em,request_id)
        VALUES ($1,$2,$3,$4,$5,1,$6::jsonb,now()+make_interval(mins=>$7),$8) RETURNING *""",tenant,a['id'],conversa,cfg['id'],conv['contato_id'],
        json.dumps({'tipo':pend['tipo'],'pergunta':pend.get('pergunta')},ensure_ascii=False),int(cfg['follow_up_primeiro_min']),turno['request_id'])
    await auditar(conn,tenant,'sistema','follow_up.agendado',conversa)
    await _evento(conn,tenant,'follow_up.agendado',t)
    return t,None


# ---------------- Vencimento (agendador, a cada 30 s) ----------------

async def _unidade(conn,tenant,conversa,cfg,conv):
    """Linha da unidade que vale para a janela: a do carrinho (módulo), senão a preferida da conversa; None = a empresa."""
    from modulos import ganchos
    codigo=None
    for _,f in ganchos('unidade_da_conversa',cfg.get('modulos') or ()):
        codigo=await f(conn,tenant,conversa)
        if codigo: break
    codigo=codigo or conv['unidade_preferida']
    if not codigo: return None
    return await conn.fetchrow('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2',tenant,codigo)


async def _aberto(conn,tenant,janela,unidade,fuso_empresa,quando):
    """(aberto, próximo instante a conferir ou None) da interseção janela ∩ horário da unidade (ou da empresa), no fuso da unidade."""
    from nucleo import horarios,expediente
    fuso=(unidade['fuso'] if unidade else None) or fuso_empresa
    j=horarios.situacao(janela,fuso,quando)
    if unidade is not None:
        u=await horarios.situacao_da_unidade(conn,tenant,unidade,fuso_empresa,quando)
        # Sem horário cadastrado (None) não fecha nada.
        u_aberta=u['aberta_agora'] is not False;u_proxima=u.get('proxima_abertura') or u.get('pausada_ate')
    else:
        e=await expediente.da_empresa(conn,tenant,fuso_empresa,quando)
        u_aberta=not e['fora'];u_proxima=e['proxima']
    j_aberta=j['aberta_agora'] is not False
    if j_aberta and u_aberta: return True,None
    candidatos=[]
    if not j_aberta:
        if not j.get('proxima_abertura'): return False,None
        candidatos.append(datetime.fromisoformat(j['proxima_abertura']))
    if not u_aberta:
        if not u_proxima: return False,None
        candidatos.append(datetime.fromisoformat(u_proxima))
    return False,max(candidatos)


async def proxima_abertura_comum(conn,tenant,janela,unidade,fuso_empresa,desde,ate):
    """Primeiro instante entre `desde` e `ate` em que a janela e a unidade estão abertas juntas, ou None."""
    quando=desde
    for _ in range(20):
        aberto,proximo=await _aberto(conn,tenant,janela,unidade,fuso_empresa,quando)
        if aberto: return quando
        if proximo is None or proximo>ate or proximo<=quando: return None
        quando=proximo
    return None


async def conferir(conn,configuracoes,t,agora):
    """Revalida a tarefa vencida (seções 2.2, 2.3 e 2.4). Devolve ('ensaio_enviaria', None) | ('cancelado', motivo) |
    ('adiar', instante)."""
    from nucleo.banco import Repositorio
    tenant,conversa=t['tenant_id'],t['conversation_id']
    repo=Repositorio(conn)
    conv=await conn.fetchrow('SELECT * FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
    if conv is None or not conv['telefone']: return 'cancelado','anonimizado'
    try: cfg=await configuracoes.efetiva(tenant,t['agente_id'])
    except LookupError: return 'cancelado','desligado'
    empresa=await repo.tenant(tenant);canal=await repo.obter(tenant,'tenant_canais',conv['canal_id'])
    if (conv['agente_id']!=t['agente_id'] or not canal or not canal['ativo'] or canal['agente_id']!=t['agente_id'] or not empresa
            or empresa['status']!='ativo' or cfg['status']!='ativo' or not ligado(cfg)): return 'cancelado','desligado'
    if cfg['modo_piloto'] and conv['telefone'] not in cfg['contatos_permitidos']: return 'cancelado','fora_do_piloto'
    if agora-t['criado_em']>IDADE_MAXIMA: return 'cancelado','pendencia_expirada'
    a=await conn.fetchrow('SELECT estado FROM atendimentos WHERE tenant_id=$1 AND id=$2',tenant,t['atendimento_id'])
    if conv['atendimento_id']!=t['atendimento_id'] or a is None or a['estado']!='aberto': return 'cancelado','encerrado'
    if conv['aguardando_atendente_desde'] or (conv['bot_pausado_ate'] and conv['bot_pausado_ate']>agora): return 'cancelado','com_atendente'
    if await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='entrada'
            AND origem='cliente' AND criado_em>$3)""",tenant,conversa,t['criado_em']): return 'cancelado','cliente_respondeu'
    if await em_opt_out(conn,tenant,t['contato_id']): return 'cancelado','opt_out'
    pend=json.loads(t['pendencia']) if isinstance(t['pendencia'],str) else t['pendencia']
    escopo=cfg.get('follow_up_escopo') or 'pedido_em_andamento'
    if pend.get('tipo') not in TIPOS_DO_ESCOPO.get(escopo,()): return 'cancelado','pendencia_invalida'
    if escopo=='pedido_em_andamento' and not await _pendencia_valida(conn,tenant,conversa,t['atendimento_id'],pend.get('tipo'),cfg):
        return 'cancelado','pendencia_invalida'
    if await _do_dia(conn,tenant,t['contato_id'],empresa['timezone'],agora,t['id'],agendados=False)>=int(cfg.get('follow_up_limite_dia') or 0):
        return 'cancelado','limite_do_dia'
    janela=cfg.get('follow_up_janela') or JANELA_PADRAO
    janela=json.loads(janela) if isinstance(janela,str) else janela
    unidade=await _unidade(conn,tenant,conversa,cfg,conv)
    aberto,_=await _aberto(conn,tenant,janela,unidade,empresa['timezone'],agora)
    if aberto: return 'ensaio_enviaria',None
    if cfg.get('follow_up_fora_unidade')=='adiar_abertura':
        quando=await proxima_abertura_comum(conn,tenant,janela,unidade,empresa['timezone'],agora,t['criado_em']+ADIAR_ATE)
        if quando: return 'adiar',quando
    return 'cancelado','fora_da_janela'


async def vencer(db,configuracoes,agora=None,limite=VENCIDAS_POR_VEZ):
    """Tarefas vencidas: cada uma conferida de novo sob a trava da conversa. Devolve {estado: quantas}."""
    contagem={}
    rows=await db.fetch("""SELECT tenant_id,id,conversation_id FROM follow_up_tarefas WHERE estado='agendado' AND previsto_em<=coalesce($1,now())
        ORDER BY previsto_em LIMIT $2""",agora,limite)
    for r in rows:
        try:
            async with db.acquire() as conn,conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',r['conversation_id'])
                t=await conn.fetchrow("SELECT * FROM follow_up_tarefas WHERE tenant_id=$1 AND id=$2 AND estado='agendado' FOR UPDATE",r['tenant_id'],r['id'])
                if t is None: continue
                momento=agora or await conn.fetchval('SELECT now()')
                estado,detalhe=await conferir(conn,configuracoes,t,momento)
                if estado=='adiar':
                    t=await conn.fetchrow('UPDATE follow_up_tarefas SET previsto_em=$3,atualizado_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING *',
                                          t['tenant_id'],t['id'],detalhe)
                    await auditar(conn,t['tenant_id'],'sistema','follow_up.adiado',t['conversation_id'])
                else:
                    t=await conn.fetchrow('UPDATE follow_up_tarefas SET estado=$3,motivo=$4,atualizado_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING *',
                                          t['tenant_id'],t['id'],estado,detalhe)
                    if estado=='ensaio_enviaria':
                        await auditar(conn,t['tenant_id'],'sistema','follow_up.ensaio',t['conversation_id'])
                        await _evento(conn,t['tenant_id'],'follow_up.ensaio',t)
                    else:
                        await auditar(conn,t['tenant_id'],'sistema','follow_up.cancelado',t['conversation_id'])
                        await _evento(conn,t['tenant_id'],'follow_up.cancelado',t)
                contagem[estado]=contagem.get(estado,0)+1
        except Exception as erro:
            log.warning('Follow-up %s não conferido (%s); tenta de novo no próximo ciclo',r['id'],type(erro).__name__)
    return contagem


async def cancelar_desligados(db,configuracoes):
    """Agente, canal ou empresa desligados, ou follow_up_ativo desligado: o que estava agendado é cancelado (`desligado`)
    no ciclo seguinte do agendador, sem esperar vencer."""
    feitos=0
    for r in await db.fetch("SELECT DISTINCT tenant_id,agente_id FROM follow_up_tarefas WHERE estado='agendado'"):
        try:
            try: cfg=await configuracoes.efetiva(r['tenant_id'],r['agente_id']);ok=cfg['status']=='ativo' and ligado(cfg)
            except LookupError: ok=False
            ok=ok and await db.fetchval("SELECT status='ativo' FROM tenants WHERE id=$1",r['tenant_id'])
            if ok:
                # Canal desligado (ou trocado de agente): só as conversas dele.
                convs=await db.fetch("""SELECT DISTINCT f.conversation_id FROM follow_up_tarefas f JOIN conversas c ON c.tenant_id=f.tenant_id
                    AND c.conversation_id=f.conversation_id LEFT JOIN tenant_canais k ON k.tenant_id=c.tenant_id AND k.id=c.canal_id
                    WHERE f.tenant_id=$1 AND f.agente_id=$2 AND f.estado='agendado' AND (k.id IS NULL OR NOT k.ativo OR k.agente_id<>f.agente_id)""",
                    r['tenant_id'],r['agente_id'])
            else:
                convs=await db.fetch("SELECT DISTINCT conversation_id FROM follow_up_tarefas WHERE tenant_id=$1 AND agente_id=$2 AND estado='agendado'",
                                     r['tenant_id'],r['agente_id'])
            for c in convs:
                async with db.acquire() as conn,conn.transaction():
                    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',c['conversation_id'])
                    feitos+=await cancelar(conn,r['tenant_id'],c['conversation_id'],'desligado')
        except Exception as erro:
            log.warning('Follow-ups de agente desligado não conferidos (%s)',type(erro).__name__)
    return feitos


async def ciclo(db,configuracoes):
    """O que o agendador da fila chama a cada 30 s."""
    await cancelar_desligados(db,configuracoes)
    return await vencer(db,configuracoes)


# ---------------- Pacote e painel ----------------

def do_pacote(cfg):
    """`recursos.follow_up` do pacote: o agente só declara `pendencia` quando vale a pena."""
    return {'ativo':ligado(cfg),'escopo':cfg.get('follow_up_escopo') or 'pedido_em_andamento'}


def para_o_painel(t):
    pend=json.loads(t['pendencia']) if isinstance(t['pendencia'],str) else (t['pendencia'] or {})
    return {'id':t['id'],'atendimento_id':t['atendimento_id'],'agente_id':t['agente_id'],'ordinal':t['ordinal'],'tipo':pend.get('tipo'),
            'pergunta':pend.get('pergunta'),'estado':t['estado'],'motivo':t['motivo'],'previsto_em':t['previsto_em'],
            'criado_em':t['criado_em'],'atualizado_em':t['atualizado_em']}


async def das_conversas(db,tenant,conversas):
    """Lista de conversas do painel: {conversa: {"estado","previsto_em"}} das que têm follow-up agendado."""
    if not conversas: return {}
    return {r['conversation_id']:{'estado':r['estado'],'previsto_em':r['previsto_em']} for r in await db.fetch(
        """SELECT conversation_id,estado,previsto_em FROM follow_up_tarefas WHERE tenant_id=$1 AND conversation_id=ANY($2::text[])
           AND estado='agendado'""",tenant,list(conversas))}


async def anonimizar(conn,tenant,conversa):
    """LGPD: a conversa anonimizada perde a pergunta guardada (tarefas e turnos) e nada segue agendado."""
    await cancelar(conn,tenant,conversa,'anonimizado')
    await conn.execute("""UPDATE follow_up_tarefas SET pendencia=jsonb_set(pendencia,'{pergunta}','null'::jsonb)
        WHERE tenant_id=$1 AND conversation_id=$2""",tenant,conversa)
    await conn.execute('UPDATE turnos SET pendencia=NULL WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
