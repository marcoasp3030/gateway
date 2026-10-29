"""Avisos do módulo de pedidos: pedido novo para a unidade e status do pedido ao cliente (Fase 4, PED1).

A caixa de saída, o alerta de transbordo e o despacho são do núcleo (nucleo/avisos.py, MULTI1-b); os nomes antigos
continuam valendo daqui."""
import re
from nucleo.contatos import nome_da_conversa
from nucleo.avisos import (MAX_TENTATIVAS,MOTIVOS,RECUSA_CERTA,incerto,mascarar_numero,numero_para_o_alerta,enfileirar,  # noqa: F401
                           destino_do_alerta,avisar_transbordo,despachar)
from nucleo import avisos as _nucleo


def reais(valor):
    if valor is None: return 'a confirmar'
    inteiro,centavos=f'{float(valor):.2f}'.split('.')
    return 'R$ '+re.sub(r'(?<=\d)(?=(\d{3})+$)','.',inteiro)+','+centavos


def texto_pedido_unidade(p,itens,nome_unidade):
    linhas=[f'🍱 NOVO PEDIDO {p["numero"]}','',f'Unidade: {nome_unidade}',f'Cliente: {p["cliente_nome"] or "(sem nome)"}',
            f'Telefone: {p["cliente_telefone"] or "(não informado)"}','']
    for i in itens:
        linhas.append(f'{i["quantidade"]}× {i["nome"]} ({reais(i["preco_unitario"])}) = {reais(i["subtotal"])}')
        # CAT3-b: escolhas do cliente, uma por linha, com o extra quando há.
        for o in i.get('opcoes') or []:
            linhas.append(f'   + {(str(o["quantidade"])+"× ") if o["quantidade"]>1 else ""}{o["opcao"]} ({o["grupo"]})'+(f' +{reais(o["preco_extra"])}' if o['preco_extra'] else ''))
        if i.get('observacao'): linhas.append(f'   obs.: {i["observacao"]}')
    linhas+=['─────────────────',f'Subtotal: {reais(p["subtotal"])}',f'Taxa de entrega: {reais(p["taxa_entrega"]) if p["modalidade"]=="delivery" else "não se aplica"}',
             f'Total: {reais(p["total"])}']
    # PAG1: forma de pagamento e troco (pedidos de antes do PAG1 não têm).
    if p.get('forma_pagamento'):
        pagamento=f'Pagamento: {p.get("pagamento_rotulo") or p["forma_pagamento"]}'
        if p.get('troco_para') is not None:
            pagamento+=f' — troco para {reais(p["troco_para"])} (levar {reais(p["troco_para"]-p["total"])} de troco)'
        linhas.append(pagamento)
    linhas.append('')
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


def texto_transbordo(telefone,nome,motivo,detalhe,pedido,painel_url,mascarar=True,equipe_ausente=False):
    """Forma antiga (com o pedido): o texto é o do núcleo com a linha do pedido."""
    return _nucleo.texto_transbordo(telefone,nome,motivo,detalhe,[f'Pedido: {pedido["numero"]} ({pedido["status"]})'] if pedido else [],
                                    painel_url,mascarar,equipe_ausente)


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
