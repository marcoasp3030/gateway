"""ENC1-a: atendimento identificado — começo, fim e reabertura (docs/ESPEC_ENC1A.md).

A conversa (empresa + canal + telefone) é uma só para sempre; o atendimento é um trecho dela. Mensagem do cliente numa conversa
sem atendimento aberto abre o próximo. Mensagem do sistema (aviso de status do pedido, fora do horário) e do atendente não abrem:
ficam sem atendimento e são adotadas pelo próximo que abrir (o agente vê o aviso a que o cliente está respondendo).

As linhas novas de mensagens, turnos, pedidos, solicitações e registros de Skill nascem ligadas ao atendimento aberto pelos
gatilhos da migração 045; aqui fica só o ciclo do atendimento. Encerrar nunca manda despedida (E1) nem mexe em carrinho ou pedido.
As regras de encerramento que dependem de um módulo (pedido concluído) vêm dos ganchos `encerra_no_turno`, `sem_retorno` e
`resumo_do_atendimento`."""
import json
import uuid
from datetime import datetime,timezone
from admin.servico import auditar

MOTIVOS_ENCERRAMENTO=('concluido','pedido_concluido','pedido_sem_retorno','vazio','manual')
MAX_RESUMO=300
TEXTO_DO_MOTIVO={'concluido':'Encerrado pelo agente','pedido_concluido':'Encerrado: pedido concluído e cliente agradeceu',
                 'pedido_sem_retorno':'Encerrado: pedido feito, sem retorno do cliente em 30 min','vazio':'Encerrado: sem mensagem de texto',
                 'manual':'Encerrado pelo painel'}


def _novo_id():
    return 'at_'+uuid.uuid4().hex[:24]


async def atual(conn,tenant,conversa):
    """O atendimento que a conversa aponta (aberto ou o último encerrado), ou None."""
    return await conn.fetchrow("""SELECT a.* FROM conversas c JOIN atendimentos a ON a.tenant_id=c.tenant_id AND a.id=c.atendimento_id
        WHERE c.tenant_id=$1 AND c.conversation_id=$2""",tenant,conversa)


async def abrir_se_preciso(conn,tenant,conversa,agente_id,mensagem_id=None):
    """Mensagem do cliente: abre atendimento se não há um aberto. Chamar sob a trava da conversa (pg_advisory_xact_lock).
    Devolve (atendimento, abriu)."""
    a=await atual(conn,tenant,conversa)
    if a and a['estado']=='aberto': return a,False
    numero=(await conn.fetchval('SELECT max(numero) FROM atendimentos WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa) or 0)+1
    motivo='primeiro_contato' if numero==1 else 'retorno_apos_encerramento'
    novo=await conn.fetchrow("""INSERT INTO atendimentos(id,tenant_id,conversation_id,numero,agente_id,motivo_inicio)
        VALUES ($1,$2,$3,$4,$5,$6) RETURNING *""",_novo_id(),tenant,conversa,numero,agente_id,motivo)
    await conn.execute('UPDATE conversas SET atendimento_id=$3 WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa,novo['id'])
    # O que chegou sem atendimento aberto (avisos do sistema, mensagens do atendente, a própria mensagem que abriu) entra neste.
    await conn.execute('UPDATE mensagens SET atendimento_id=$3 WHERE tenant_id=$1 AND conversation_id=$2 AND atendimento_id IS NULL',tenant,conversa,novo['id'])
    await auditar(conn,tenant,'sistema','atendimento.aberto',conversa)
    from nucleo.webhooks import emitir
    await emitir(conn,tenant,'atendimento.aberto',{'conversation_id':conversa,'atendimento_id':novo['id'],'numero':numero,'motivo_inicio':motivo})
    return novo,True


async def encerrar(conn,tenant,conversa,motivo,desfecho=None,actor='sistema'):
    """Encerra o atendimento aberto da conversa (o primeiro encerramento vale) e marca a conversa como encerrada, como antes.
    Devolve o atendimento encerrado, ou None se não havia um aberto."""
    if motivo not in MOTIVOS_ENCERRAMENTO: raise ValueError('motivo de encerramento inválido')
    await conn.execute("""UPDATE conversas SET encerrada_em=now(),desfecho=$3,atualizado_em=now()
        WHERE tenant_id=$1 AND conversation_id=$2 AND encerrada_em IS NULL""",tenant,conversa,desfecho)
    a=await conn.fetchrow("""UPDATE atendimentos SET estado='encerrado',encerrado_em=now(),motivo_encerramento=$3,desfecho=$4
        WHERE tenant_id=$1 AND conversation_id=$2 AND estado='aberto' RETURNING *""",tenant,conversa,motivo,desfecho)
    if a is None: return None
    texto=await resumo(conn,tenant,a)
    await conn.execute('UPDATE atendimentos SET resumo=$3 WHERE tenant_id=$1 AND id=$2',tenant,a['id'],texto)
    await auditar(conn,tenant,actor,'atendimento.encerrado',conversa)
    from nucleo.webhooks import emitir
    await emitir(conn,tenant,'atendimento.encerrado',{'conversation_id':conversa,'atendimento_id':a['id'],'numero':a['numero'],
                                                      'motivo_encerramento':motivo,'desfecho':desfecho})
    return dict(a,resumo=texto)


async def reabrir(conn,tenant,conversa,actor):
    """Painel: reabre o último atendimento, só se ele está encerrado (e portanto a conversa não tem outro aberto)."""
    a=await atual(conn,tenant,conversa)
    if a is None or a['estado']!='encerrado': return None
    ultimo=await conn.fetchval('SELECT max(numero) FROM atendimentos WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
    if a['numero']!=ultimo: return None
    r=await conn.fetchrow("""UPDATE atendimentos SET estado='aberto',encerrado_em=NULL,motivo_encerramento=NULL,desfecho=NULL,resumo=NULL,
        motivo_inicio=CASE WHEN motivo_inicio='migracao' THEN motivo_inicio ELSE 'reaberto_manual' END
        WHERE tenant_id=$1 AND id=$2 RETURNING *""",tenant,a['id'])
    await conn.execute('UPDATE conversas SET encerrada_em=NULL,desfecho=NULL,atualizado_em=now() WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
    await conn.execute('UPDATE mensagens SET atendimento_id=$3 WHERE tenant_id=$1 AND conversation_id=$2 AND atendimento_id IS NULL',tenant,conversa,a['id'])
    await auditar(conn,tenant,actor,'atendimento.reaberto',conversa)
    from nucleo.webhooks import emitir
    await emitir(conn,tenant,'atendimento.aberto',{'conversation_id':conversa,'atendimento_id':a['id'],'numero':a['numero'],'motivo_inicio':'reaberto_manual'})
    return r


async def resumo(conn,tenant,a):
    """Resumo factual e curto do atendimento (decisão 2), sem IA e sem texto da conversa nem dado pessoal: motivo do encerramento,
    o que os módulos registraram nele (pedidos com número, modalidade, unidade, total e status atual), solicitação à equipe,
    transferência. Até 300 caracteres."""
    partes=[]
    if a['motivo_encerramento']: partes.append(TEXTO_DO_MOTIVO.get(a['motivo_encerramento'],a['motivo_encerramento'])+'.')
    from modulos import ganchos
    for _,f in ganchos('resumo_do_atendimento'): partes+=await f(conn,tenant,a['id'])
    sol=await conn.fetch("""SELECT numero,tipo,status FROM solicitacoes WHERE tenant_id=$1 AND atendimento_id=$2 ORDER BY id""",tenant,a['id'])
    for s in sol: partes.append(f"Solicitação {s['numero']} à equipe ({s['tipo']}, {s['status'].replace('_',' ')}).")
    if await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM turnos WHERE tenant_id=$1 AND atendimento_id=$2 AND acao='transferir_humano' AND status='ok')""",tenant,a['id']):
        partes.append('Transferido para atendente.')
    texto=' '.join(partes)
    return texto if len(texto)<=MAX_RESUMO else texto[:MAX_RESUMO-1].rstrip()+'…'


async def do_pacote(conn,tenant,conversa,fuso,agora):
    """Bloco `atendimento` do pacote ao Skyone. `novo` = nenhum turno deste atendimento foi concluído ainda (lido do banco: vale
    também com o turno retomado depois de uma queda). `anterior` traz o resumo do atendimento anterior, refeito agora (status
    do pedido atualizado). Conversa sem atendimento (gravada pela imagem anterior) → nulo."""
    a=await atual(conn,tenant,conversa)
    if a is None: return None
    novo=not await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM turnos WHERE tenant_id=$1 AND atendimento_id=$2 AND etapa='concluido'
        AND status IN ('ok','erro'))""",tenant,a['id'])
    ant=await conn.fetchrow("""SELECT * FROM atendimentos WHERE tenant_id=$1 AND conversation_id=$2 AND numero<$3 AND estado='encerrado'
        ORDER BY numero DESC LIMIT 1""",tenant,conversa,a['numero'])
    anterior=None
    if ant:
        anterior={'numero':ant['numero'],'motivo_encerramento':ant['motivo_encerramento'],'desfecho':ant['desfecho'],
                  'encerrado_em':ant['encerrado_em'].astimezone(fuso).isoformat(timespec='seconds') if ant['encerrado_em'] else None,
                  'ha_segundos':max(0,int((agora-ant['encerrado_em']).total_seconds())) if ant['encerrado_em'] else None,
                  'resumo':await resumo(conn,tenant,ant)}
    return {'id':a['id'],'numero':a['numero'],'novo':novo,'motivo_inicio':a['motivo_inicio'],'anterior':anterior}


def para_o_painel(a,conv=None):
    """Atendimento atual no formato da API admin. `estado` em_atendimento_humano quando a conversa está com atendente."""
    if a is None: return None
    estado=a['estado']
    if estado=='aberto' and conv is not None:
        pausado=conv.get('bot_pausado_ate') and conv['bot_pausado_ate']>datetime.now(timezone.utc)
        if conv.get('aguardando_atendente_desde') or pausado: estado='em_atendimento_humano'
    return {'id':a['id'],'numero':a['numero'],'estado':estado,'aberto_em':a['aberto_em'],'encerrado_em':a['encerrado_em'],
            'motivo_encerramento':a['motivo_encerramento']}


def com_botoes_pendentes(meta):
    meta=json.loads(meta) if isinstance(meta,str) else meta
    return isinstance(meta,dict) and bool(meta.get('botoes'))
