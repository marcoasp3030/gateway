"""Pedido anunciado sem fechar (PED2, docs/ESPEC_PED2.md).

O agente às vezes responde "acabei de fechar seu pedido" sem ter chamado fechar_pedido com sucesso: o cliente acha que
pediu e a unidade não recebe nada. Aqui se decide se a resposta de um turno é esse caso. Quem age é o pipeline:
refaz o turno uma vez com um aviso e, se repetir, segura o texto do agente."""
import re,unicodedata

AVISO_AO_AGENTE='Você disse que o pedido foi fechado, mas fechar_pedido não foi chamado com sucesso. Chame fechar_pedido ou explique o que falta.'
TEXTO_AO_CLIENTE='Opa, tive um probleminha para registrar seu pedido. Pode tocar em Confirmar de novo?'
BOTAO={'id':'confirmar_pedido','titulo':'Confirmar pedido'}
# Pedido real recente: a resposta pode estar falando dele ("seu pedido já foi confirmado").
PEDIDO_RECENTE_H=12

_FEITO=r'(feito|fechado|finalizado|registrado|confirmado|enviado|realizado|concluido)'
_EU_FIZ=re.compile(r'\b(fechei|finalizei|registrei|conclui|enviei|confirmei)\b[^.?!\n]{0,40}\bpedido\b')
_ACABEI=re.compile(r'\bacabei de (fechar|finalizar|registrar|enviar|confirmar|concluir)\b[^.?!\n]{0,40}\bpedido\b')
_PEDIDO_FEITO=re.compile(r'\bpedido\b([^.?!\n]{0,30}?)\b'+_FEITO+r'\b')
_NUMERO=re.compile(r'\bpedido\s*(?:de\s+)?(?:numero|nro|no|n|#)?\s*[:#]?\s*(\d{4,8})\b|\bnumero do (?:seu |teu )?pedido\s*(?:e|:)?\s*#?\s*(\d{4,8})\b')
# Antes do trecho: a frase fala do que ainda vai acontecer ou do que falta ("vou fechar", "para confirmar", "assim que").
_ANTES=re.compile(r'\b(vou|vamos|irei|posso|podemos|quer|queria|deseja|gostaria|assim que|quando|antes de|depois de|se|caso|falta|faltam|preciso|precisa|nao|para|pra)\b')
# Entre "pedido" e "confirmado": "será confirmado", "ainda não foi fechado", "precisa ser registrado".
_NO_MEIO=re.compile(r'\b(sera|serao|vai|ser|seja|for|nao|ainda|quando|assim que)\b')
JANELA_ANTES=40


def _normal(texto):
    sem=''.join(c for c in unicodedata.normalize('NFKD',str(texto or '')) if not unicodedata.combining(c))
    return re.sub(r'[ \t]+',' ',sem.lower().replace('º','').replace('°',''))


def numeros_citados(textos):
    return sorted({n for t in textos for m in _NUMERO.finditer(_normal(t)) for n in m.groups() if n})


def texto_anuncia(textos):
    """Algum balão afirma que o pedido já foi fechado? Pergunta e frase no futuro não contam."""
    for texto in textos:
        for frase in re.findall(r'[^.?!\n]+[.?!]*',_normal(texto)):
            if '?' in frase: continue
            for padrao in (_ACABEI,_EU_FIZ,_PEDIDO_FEITO):
                for m in padrao.finditer(frase):
                    if _ANTES.search(frase[max(0,m.start()-JANELA_ANTES):m.start()]): continue
                    if padrao is _PEDIDO_FEITO and _NO_MEIO.search(m.group(1)): continue
                    return True
    return False


def sinal(resultado):
    """Por onde o agente anunciou o fechamento: 'acao' | 'desfecho' | 'texto' | None."""
    if not isinstance(resultado,dict): return None
    if resultado.get('acao')=='pedido_fechado': return 'acao'
    if resultado.get('desfecho')=='pedido_feito': return 'desfecho'
    # Transferência ou aviso à equipe: "seu pedido foi enviado para a equipe confirmar" não é anúncio de fechamento.
    if resultado.get('acao') in ('transferir_humano','notificar_equipe','silencio'): return None
    textos=[t for t in resultado.get('resposta') or [] if isinstance(t,str)]
    return 'texto' if texto_anuncia(textos) or numeros_citados(textos) else None


async def avaliar(db,tenant,conversa,resultado,desde):
    """None = nada a segurar. Senão, por onde veio o anúncio de um pedido que não existe.
    `desde` é o início do turno (do primeiro, quando o turno foi refeito)."""
    por=sinal(resultado)
    if not por: return None
    if await db.fetchval("""SELECT EXISTS(SELECT 1 FROM skill_chamadas WHERE tenant_id=$1 AND conversation_id=$2 AND skill='fechar_pedido'
            AND resultado='ok' AND criado_em>=$3)""",tenant,conversa,desde): return None
    citados=numeros_citados([t for t in resultado.get('resposta') or [] if isinstance(t,str)])
    if citados:
        # Citou o número de um pedido que existe nesta conversa: está falando dele. Número que não existe é anúncio falso.
        existe=await db.fetchval('SELECT EXISTS(SELECT 1 FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 AND numero=ANY($3::text[]))',tenant,conversa,citados)
        return None if existe else 'numero_inexistente'
    # Carrinho aberto com itens = havia o que fechar e não foi fechado.
    if await db.fetchval("""SELECT EXISTS(SELECT 1 FROM carrinhos c JOIN carrinho_itens l ON l.tenant_id=c.tenant_id AND l.carrinho_id=c.id
            WHERE c.tenant_id=$1 AND c.conversation_id=$2 AND c.status='aberto')""",tenant,conversa): return por
    # Sem carrinho pendente e com pedido recente: é conversa sobre o pedido que existe ("obrigado" depois de fechar).
    if await db.fetchval('SELECT EXISTS(SELECT 1 FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 AND criado_em>now()-make_interval(hours=>$3))',
                         tenant,conversa,PEDIDO_RECENTE_H): return None
    return por
