"""Reações do bot a mensagens do cliente (R1, docs/ESPEC_CFG1C.md).

O agente pede uma reação (campo `reacao` do retorno); aqui estão as travas, na ordem combinada, e o alvo.
A primeira trava que barra define o motivo. Nada aqui pode derrubar o turno."""
import json
from zoneinfo import ZoneInfo
from datetime import datetime,timezone

MSGS_ENTRE_REACOES=3
# Seletor de variação (U+FE0F) não muda o emoji: "❤️" e "❤" são o mesmo para a comparação.
def _base(emoji): return (emoji or '').replace('️','')


def do_retorno(valor):
    """Campo `reacao` do retorno: um emoji (texto curto, sem espaço). Qualquer outra coisa → None, sem erro."""
    if not isinstance(valor,str): return None
    limpo=valor.strip()
    if not limpo or len(limpo)>8 or any(c.isspace() or c.isalnum() for c in limpo): return None
    return limpo


async def avaliar(db,tenant,conversa,cfg,resultado,mensagem_ids,fuso,agora=None):
    """(motivo do bloqueio ou None, mensagem-alvo ou None). Travas na ordem do contrato."""
    emoji=resultado.get('reacao')
    if cfg.get('reacoes')!='on': return 'desligado',None
    if _base(emoji) not in {_base(e) for e in cfg.get('reacoes_emojis') or []}: return 'emoji_fora_da_lista',None
    agora=agora or datetime.now(timezone.utc)
    inicio_do_dia=agora.astimezone(ZoneInfo(fuso)).replace(hour=0,minute=0,second=0,microsecond=0)
    feitas=await db.fetchval("""SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' AND origem='bot'
        AND tipo='reacao' AND criado_em>=$3""",tenant,conversa,inicio_do_dia)
    if feitas>=int(cfg.get('reacoes_max_dia') or 0): return 'limite_diario',None
    ultima=await db.fetchval("SELECT max(id) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' AND origem='bot' AND tipo='reacao'",tenant,conversa)
    if ultima is not None and await db.fetchval("""SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='entrada'
            AND origem='cliente' AND id>$3""",tenant,conversa,ultima)<MSGS_ENTRE_REACOES: return 'intervalo',None
    if resultado.get('acao')=='transferir_humano' or (resultado.get('motivo') or '').strip().lower()=='reclamacao': return 'acao_ou_motivo',None
    # Alvo: a última mensagem de TEXTO do cliente no lote (clique em botão ou lista, áudio, imagem e localização não contam).
    alvo=None
    for m in await db.fetch("""SELECT id,tipo,meta,message_id FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND id=ANY($3::bigint[])
            AND direcao='entrada' AND origem='cliente' ORDER BY id""",tenant,conversa,list(mensagem_ids or [])):
        meta=json.loads(m['meta']) if isinstance(m['meta'],str) else (m['meta'] or {})
        if (m['tipo'] or 'texto')=='texto' and not meta.get('interacao') and m['message_id']: alvo=m
    if alvo is None: return 'sem_texto',None
    return None,alvo
