"""Solicitações para a equipe (SOL1, docs/ESPEC_SOL1.md): o agente avisa a equipe (no delivery: evento, catering, encomenda…)
e continua atendendo. Aqui ficam a validação do que o agente devolve, o registro (uma aberta por conversa e motivo),
o alerta e a conversão de transferência em aviso."""
import json
import re
import logging
from nucleo.atendimento import MOTIVOS_TRANSFERENCIA
from nucleo.contatos import nome_da_conversa,e164,formatado

log=logging.getLogger('gateway.solicitacoes')
# MULTI1-b: o núcleo só tem "outro"; os tipos de cada segmento vêm do modelo do agente e dos módulos (modulos.tipos_efetivos).
TIPOS=('outro',)
MAX_DATA,MAX_LOCAL,MAX_OBSERVACAO,MAX_PESSOAS=80,160,500,100000
CAMPOS=('data','pessoas','local','observacao')
ABERTAS=('aberta','em_andamento')
STATUS=('aberta','em_andamento','concluida','cancelada')
INTERVALO_DE_ATUALIZACAO_MIN=30


def _texto(valor,maximo):
    if not isinstance(valor,str): return None
    limpo=' '.join(''.join(c if c.isprintable() else ' ' for c in valor).split())
    return limpo[:maximo] or None


def validar(valor):
    """Campo "solicitacao" do retorno do agente, só com o que é do contrato. Campo inválido é ignorado, sem erro.
    Devolve {"tipo": ..., "data"?, "pessoas"?, "local"?, "observacao"?} ou None se não veio um objeto."""
    if not isinstance(valor,dict): return None
    tipo=valor.get('tipo');tipo=tipo.strip().lower() if isinstance(tipo,str) else ''
    # MULTI1-a: o tipo é conferido contra a lista do agente no registro; aqui só o formato.
    limpo={'tipo':tipo if re.fullmatch(r'[a-z0-9_]{2,40}',tipo) else 'outro'}
    for campo,maximo in (('data',MAX_DATA),('local',MAX_LOCAL),('observacao',MAX_OBSERVACAO)):
        texto=_texto(valor.get(campo),maximo)
        if texto: limpo[campo]=texto
    pessoas=valor.get('pessoas')
    if isinstance(pessoas,str) and re.fullmatch(r'\s*\d{1,6}\s*',pessoas): pessoas=int(pessoas)
    if type(pessoas) is int and 1<=pessoas<=MAX_PESSOAS: limpo['pessoas']=pessoas
    return limpo


def que_nao_pausam(cfg):
    """Motivos de transferência que viram aviso à equipe com venda em andamento: os marcados `converte_em_solicitacao`
    na lista do agente (MULTI1-a)."""
    return [m['id'] for m in cfg.get('motivos_transferencia_permitidos') or [] if m.get('converte_em_solicitacao')]


def motivo_do_catalogo(motivo,motivos=None):
    """(motivo do catálogo, texto recebido se não era do catálogo). `motivos`: ids que valem para o agente (MULTI1-a)."""
    m=(motivo or '').strip().lower()
    if m in (motivos if motivos is not None else MOTIVOS_TRANSFERENCIA): return m,None
    return 'fora_do_escopo',(' '.join((motivo or '').split())[:200] or None)


async def venda_em_andamento(db,tenant,conversa):
    """Há venda em andamento nesta conversa, segundo os módulos (pedidos: carrinho aberto com itens, ou pedido ativo)?"""
    from modulos import ganchos
    for _,f in ganchos('em_andamento'):
        if await f(db,tenant,conversa,'solicitacao'): return True
    return False


async def converter(db,tenant,conversa,cfg,resultado):
    """Proteção: transferir_humano por um motivo que converte em solicitação, com venda em andamento, vira notificar_equipe.
    Altera `resultado` no lugar (acao e acao_original) e devolve True se converteu. Falha aqui nunca derruba o turno."""
    try:
        if (not resultado or resultado.get('acao')!='transferir_humano' or cfg.get('notificar_equipe')!='on'
                or (resultado.get('motivo') or '').strip().lower() not in que_nao_pausam(cfg)): return False
        if not await venda_em_andamento(db,tenant,conversa): return False
        resultado['acao_original']='transferir_humano';resultado['acao']='notificar_equipe'
        log.info('Transferência convertida em aviso à equipe: acao_original=transferir_humano acao_aplicada=notificar_equipe motivo=%s',resultado.get('motivo'))
        return True
    except Exception:
        log.warning('Conversão de transferência não avaliada; segue como o agente pediu')
        return False


def texto_do_alerta(s,nome,telefone,mascarar,painel_url,atualizacao=False):
    d=s['dados'] if isinstance(s['dados'],dict) else json.loads(s['dados'] or '{}')
    if mascarar:
        digitos=''.join(c for c in str(telefone or '') if c.isdigit());fone=digitos[:4]+'****'+digitos[-4:] if len(digitos)>=10 else '(sem número)'
    else: fone=formatado(e164(telefone)) or '(sem número)'
    linhas=[('🔄 SOLICITAÇÃO ATUALIZADA' if atualizacao else '📋 SOLICITAÇÃO PARA A EQUIPE')+f' {s["numero"]} — {s["tipo"]}',f'Cliente: {nome or "(sem nome)"} — {fone}']
    resumo=' · '.join(x for x in (f'Data: {d["data"]}' if d.get('data') else '',f'Pessoas: {d["pessoas"]}' if d.get('pessoas') else '',f'Local: {d["local"]}' if d.get('local') else '') if x)
    if resumo: linhas.append(resumo)
    if d.get('observacao'): linhas.append(f'Obs.: {d["observacao"]}')
    linhas.append('O bot continua atendendo esta conversa.')
    if painel_url: linhas.append(f'Painel: {painel_url}')
    return '\n'.join(linhas)


async def registrar(conn,tenant,conversa,cfg,resultado,painel_url=None):
    """Cria ou atualiza a solicitação da conversa para o motivo e enfileira o alerta. Chamar dentro da transação do turno.
    Devolve ('criada'|'atualizada', id) ou None se o recurso está desligado."""
    if cfg.get('notificar_equipe')!='on': return None
    from admin.servico import auditar
    from nucleo.webhooks import emitir
    from nucleo import avisos
    motivos=[m['id'] for m in cfg['motivos_transferencia_permitidos']] if cfg.get('motivos_transferencia_permitidos') else None
    tipos=[t['id'] for t in cfg['tipos_solicitacao_permitidos']] if cfg.get('tipos_solicitacao_permitidos') else list(TIPOS)
    motivo,informado=motivo_do_catalogo(resultado.get('motivo'),motivos)
    sol=dict(resultado['solicitacao']) if isinstance(resultado.get('solicitacao'),dict) else {'tipo':motivo if motivo in tipos else 'outro'}
    # Tipo que não é deste agente vira "outro" (MULTI1-a).
    if sol.get('tipo') not in tipos: sol['tipo']='outro'
    dados={c:sol[c] for c in CAMPOS if sol.get(c) is not None}
    origem='convertida' if resultado.get('acao_original') else 'agente'
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','solicitacoes:'+tenant)
    atual=await conn.fetchrow("SELECT * FROM solicitacoes WHERE tenant_id=$1 AND conversation_id=$2 AND motivo=$3 AND status=ANY($4::text[]) FOR UPDATE",tenant,conversa,motivo,list(ABERTAS))
    conv=await conn.fetchrow('SELECT telefone,contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
    destino,_=await avisos.destino_do_alerta(conn,tenant,cfg,conversa)
    nome,_=await nome_da_conversa(conn,tenant,conversa)
    if atual is None:
        n=1+await conn.fetchval('SELECT count(*) FROM solicitacoes WHERE tenant_id=$1',tenant)
        s=await conn.fetchrow('''INSERT INTO solicitacoes(tenant_id,numero,conversation_id,contato_id,agente_id,motivo,motivo_informado,tipo,dados,origem)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10) RETURNING *''',tenant,f'S-{n:04d}',conversa,conv['contato_id'] if conv else None,cfg['id'],
            motivo,informado,sol.get('tipo') or 'outro',json.dumps(dados,ensure_ascii=False),origem)
        feito='criada';alertar=True;atualizacao=False
    else:
        antes=atual['dados'] if isinstance(atual['dados'],dict) else json.loads(atual['dados'] or '{}')
        # Campo que veio preenchido substitui; campo ausente não apaga o que já havia. "Novo" = estava vazio.
        novos=[c for c in dados if antes.get(c) is None]
        juntos={**antes,**dados};tipo=sol['tipo'] if sol.get('tipo') and sol['tipo']!='outro' else atual['tipo']
        s=await conn.fetchrow("UPDATE solicitacoes SET dados=$3::jsonb,tipo=$4,atualizacoes=atualizacoes+1,atualizado_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING *",
                              tenant,atual['id'],json.dumps(juntos,ensure_ascii=False),tipo)
        feito='atualizada';atualizacao=True
        # Alerta de atualização: só com campo novo, e no máximo um a cada 30 min por solicitação.
        alertar=bool(novos) and await conn.fetchval("SELECT $1::timestamptz IS NULL OR $1::timestamptz<now()-make_interval(mins=>$2)",atual['alertado_em'],INTERVALO_DE_ATUALIZACAO_MIN)
    if alertar and destino:
        texto=texto_do_alerta(dict(s),nome,conv['telefone'] if conv else None,bool(cfg.get('transbordo_destino_externo')),painel_url,atualizacao)
        await avisos.enfileirar(conn,tenant,'solicitacao',f'{s["numero"]}:{s["atualizacoes"]}',texto,destino,conversa)
        await conn.execute('UPDATE solicitacoes SET alertado_em=now() WHERE tenant_id=$1 AND id=$2',tenant,s['id'])
    await auditar(conn,tenant,'agente','solicitacao.'+feito,s['numero'])
    await emitir(conn,tenant,'solicitacao.'+feito,{'id':s['id'],'numero':s['numero'],'conversation_id':conversa,'motivo':motivo,'tipo':s['tipo'],'status':s['status']})
    return feito,s['id']


async def abertas(db,tenant,conversa):
    """Solicitações abertas desta conversa, para o agente saber que a equipe já foi avisada (e só complementar)."""
    rows=await db.fetch("""SELECT numero,motivo,tipo,status,dados,greatest(0,extract(epoch FROM now()-atualizado_em))::int AS ha
        FROM solicitacoes WHERE tenant_id=$1 AND conversation_id=$2 AND status=ANY($3::text[]) ORDER BY id""",tenant,conversa,list(ABERTAS))
    return [{'numero':r['numero'],'motivo':r['motivo'],'tipo':r['tipo'],'status':r['status'],
             **{c:(r['dados'] if isinstance(r['dados'],dict) else json.loads(r['dados'] or '{}')).get(c) for c in CAMPOS},'ha_segundos':r['ha']} for r in rows]
