"""Carrinho e pré-pedido (Fase 4). Regras de negócio validadas aqui, nunca pelo agente:
preço sempre do catálogo, carrinho de uma unidade só, item só entra se for vendável,
bebida alcoólica só fecha com maioridade confirmada (SEG06: o que o agente pedir não muda isso)."""
import json,secrets
from decimal import Decimal
from zoneinfo import ZoneInfo
from nucleo.contatos import nome_da_conversa
from modulos.entrega import calcular_taxa,cep_limpo,nome_busca
from . import agendamento

MAX_LINHAS=50
MAX_QUANTIDADE=99
# Motivo de recusa → (campo, mensagem fixa para o agente). Nenhuma mensagem repete o que veio na chamada.
RECUSAS={
 'item_inexistente':('item_id','Item não encontrado no catálogo.'),
 'item_inativo':('item_id','Item indisponível.'),
 'sem_preco':('item_id','Item sem preço cadastrado: não entra no pedido. Transfira para a equipe confirmar (motivo pedido_para_confirmar).'),
 'sem_delivery':('item_id','Item não disponível para pedido.'),
 'unidade_diferente':('item_id','O carrinho já tem itens de outra unidade. Um pedido é de uma unidade só.'),
 'unidade_inexistente':('unidade','Unidade não encontrada.'),
 'carrinho_cheio':('item_id','O carrinho chegou ao limite de itens.'),
 'linha_inexistente':('id','Este item não está no carrinho.'),
 'carrinho_vazio':('itens','O carrinho está vazio.'),
 'falta_modalidade':('modalidade','Falta dizer se é entrega (delivery) ou retirada.'),
 'falta_endereco':('endereco','Falta o endereço de entrega.'),
 'falta_maioridade':('maioridade_confirmada','Há bebida alcoólica no carrinho: o cliente precisa confirmar que tem 18 anos ou mais.'),
 'retirada_indisponivel':('modalidade','Esta unidade não aceita retirada.'),
 'abaixo_do_minimo':('itens','O pedido está abaixo do valor mínimo da unidade.'),
 'preco_alterado':('itens','O preço de um item mudou. Confira o carrinho atualizado com o cliente antes de fechar.'),
 'pedido_inexistente':('numero','Pedido não encontrado nesta conversa.'),
 'pedido_nao_cancelavel':('numero','Este pedido já foi decidido pela unidade e não pode ser cancelado por aqui. Transfira para a equipe.'),
 'item_nao_buscado':('item_id','Este item_id não veio de uma busca desta conversa. Chame buscar_catalogo e use o item_id devolvido.'),
 'item_indisponivel':('item_id','Este item não é servido no dia e horário do pedido. Diga ao cliente quando ele é servido (veja resumo) e ofereça outra opção ou outro horário.'),
 'horario_passado':('horario','O horário marcado já passou. Pergunte ao cliente outro horário e chame definir_entrega de novo.'),
 'unidade_fechada':('unidade','A unidade está fechada agora: o pedido não pode ser fechado para já. Ofereça agendar para a próxima abertura (veja proxima_abertura) ou para outro horário em que ela funcione.'),
 'horario_fora_funcionamento':('horario','A unidade não funciona nesse dia e horário. Ofereça um horário dentro do funcionamento (veja proxima_abertura).'),
 'horario_invalido':('horario','Não deu para entender o horário. Envie agendado_para (AAAA-MM-DDTHH:MM) ou um texto com a hora ("hoje às 19h", "19:30", "o quanto antes").'),
 'horario_muito_distante':('horario','O horário marcado está longe demais. O pedido pode ser agendado para até 7 dias à frente.'),
 # CAT3-b: opções do item. Os detalhes trazem grupo_id e o grupo completo (como na busca) para o agente perguntar ao cliente.
 'opcao_obrigatoria':('opcoes','Este item pede uma escolha no grupo indicado. Pergunte ao cliente (pode oferecer as opções como botões ou lista) e chame de novo com opcoes [{grupo_id, opcao_id}].'),
 'opcoes_acima_do_maximo':('opcoes','Escolhas demais no grupo indicado (veja grupo.maximo). Pergunte ao cliente quais manter e chame de novo.'),
 'opcoes_abaixo_do_minimo':('opcoes','Faltam escolhas no grupo indicado (veja grupo.minimo). Pergunte ao cliente o que falta e chame de novo.'),
 'opcao_inexistente':('opcoes','Esta opção não existe neste item (ou saiu do cardápio). Use só grupo_id e opcao_id devolvidos por buscar_catalogo.'),
 'opcao_repetida':('opcoes','Esta opção não pode ser escolhida mais de uma vez neste grupo.'),
 # PAG1: forma de pagamento (da lista da unidade) e troco. Os detalhes trazem formas_aceitas [{codigo, rotulo, aceita_troco}].
 'pagamento_nao_informado':('forma_pagamento','Falta a forma de pagamento. Pergunte ao cliente uma das formas_aceitas (detalhes) e chame definir_entrega com forma_pagamento.'),
 'forma_pagamento_invalida':('forma_pagamento','Esta forma de pagamento não é aceita nesta unidade. Ofereça uma das formas_aceitas (detalhes). Lista vazia: a unidade não tem formas cadastradas; transfira para a equipe (motivo pedido_para_confirmar).'),
 'troco_so_em_dinheiro':('troco_para','Troco só existe no pagamento em dinheiro (com troco aceito pela unidade). Não envie troco_para em outra forma.'),
 'troco_menor_que_total':('troco_para','O valor para troco precisa ser maior que o total do pedido (veja total nos detalhes). Pergunte de novo ao cliente; se ele não precisa de troco, envie troco_para null.'),
 'delivery_indisponivel':('modalidade','Esta unidade não faz entrega. Ofereça retirada.'),
 # G4-b: taxa por área. Os detalhes trazem modo, aceita_retirada e pedir.
 'fora_area':('endereco','Este endereço está fora da área de entrega da unidade. Diga isso ao cliente e, se detalhes.aceita_retirada, ofereça retirada.'),
 'bairro_nao_reconhecido':('bairro','Este bairro não está na lista da unidade. Se detalhes.pedir for "cep", peça o CEP. Se for "confirmar_bairro", confirme a grafia uma vez e reenvie com bairro_confirmado: true: a taxa fica a confirmar pela unidade.'),
 'falta_cep':('cep','Esta unidade calcula a taxa pelo CEP. Peça o CEP ao cliente e chame definir_entrega com cep.'),
 'cep_invalido':('cep','CEP inválido. Peça os 8 dígitos ao cliente.'),
}


class Recusa(Exception):
    def __init__(self,motivo,status=422,detalhes=None):
        self.motivo=motivo;self.status=status;self.campo,self.mensagem=RECUSAS[motivo]
        # Dados a mais para o agente decidir o que oferecer (ex.: proxima_abertura numa recusa de horário).
        self.detalhes=detalhes or {}


def dinheiro(valor):
    return float(valor) if valor is not None else None


async def travar(conn,tenant,conversa):
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','carrinho:'+tenant+':'+conversa)


async def unidade_da_empresa(conn,tenant,codigo):
    if not codigo: return None
    return await conn.fetchrow('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2 AND ativo',tenant,codigo)


FALTA_DA_AREA={'fora_area':'fora_area','bairro_nao_reconhecido':'bairro','falta_cep':'cep'}


async def entrega_da_unidade(conn,tenant,u,bairro=None,cep=None,endereco=None,bairro_confirmado=False,subtotal=None):
    """Taxa e área da unidade para este endereço (modulos/entrega.py). Também serve à consulta, sem carrinho."""
    linhas=await conn.fetch('SELECT * FROM unidade_taxas WHERE tenant_id=$1 AND unidade_id=$2 ORDER BY ordem,id',tenant,u['id'])
    return calcular_taxa(u,linhas,bairro,cep,endereco,bairro_confirmado,subtotal)


async def entrega_do_carrinho(conn,tenant,u,c,subtotal):
    """Taxa do carrinho. Fora de entrega, ou sem unidade, não há taxa nem o que recusar."""
    if not u or c['modalidade']!='delivery':
        return {'atende':True,'motivo':None,'taxa':None,'origem':None,'pedir':None,'bairro':None,'cep':None}
    return await entrega_da_unidade(conn,tenant,u,c['bairro'],c['cep'],c['endereco'],c['bairro_confirmado'],subtotal)


def recusar_entrega(e,u):
    """Recusa com o que o agente precisa para decidir o que oferecer."""
    if not e['atende']:
        raise Recusa(e['motivo'],detalhes={'modo':u['taxa_modo'],'aceita_retirada':u['aceita_retirada'] is not False,'pedir':e['pedir']})


async def aberto(conn,tenant,conversa,criar=False):
    c=await conn.fetchrow("SELECT * FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2 AND status='aberto'",tenant,conversa)
    if c is None and criar:
        c=await conn.fetchrow('INSERT INTO carrinhos(tenant_id,conversation_id) VALUES ($1,$2) RETURNING *',tenant,conversa)
    return c


async def linhas(conn,tenant,carrinho_id):
    return await conn.fetch('''SELECT l.id,l.item_id,l.quantidade,l.preco_unitario,l.observacao,
        i.codigo,i.nome,i.categoria,i.unidade,i.alcoolico,i.ativo,i.preco AS preco_atual,i.delivery,i.tipo,i.disponibilidade,i.esgotado_ate,i.arquivado_em,
        l.preco_base,l.opcoes,l.assinatura
        FROM carrinho_itens l JOIN catalogo_itens i ON i.tenant_id=l.tenant_id AND i.id=l.item_id
        WHERE l.tenant_id=$1 AND l.carrinho_id=$2 ORDER BY l.id''',tenant,carrinho_id)


async def fuso_da_unidade(conn,tenant,u=None):
    """Fuso em que o horário do pedido é lido: o da unidade, ou o da empresa."""
    return (u['fuso'] if u and u['fuso'] else None) or await conn.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant)


async def retrato(conn,tenant,conversa):
    """Carrinho completo para o agente: itens, subtotal, taxa, total e o que falta para fechar."""
    c=await aberto(conn,tenant,conversa)
    if c is None:
        return {'conversation_id':conversa,'unidade':None,'modalidade':None,'endereco':None,'horario':None,
                'agendado_para':None,'horario_vencido':False,'horario_aviso':None,'observacao':None,
                'maioridade_confirmada':False,'itens':[],'numero_itens':0,'subtotal':None,'taxa_entrega':None,'total':None,
                'bairro':None,'cep':None,'taxa_origem':None,'frete_gratis':False,
                'pedido_minimo':None,'tem_alcoolico':False,'itens_indisponiveis':[],
                'forma_pagamento':None,'pagamento_rotulo':None,'troco_para':None,'troco':None,'formas_aceitas':[],
                'faltando':['itens','modalidade','pagamento'],'pode_fechar':False,'atualizado_em':None}
    ls=await linhas(conn,tenant,c['id'])
    fora=await indisponiveis(conn,tenant,conversa,ls);fora_por_linha={f['id']:f for f in fora}
    itens=[{'id':l['id'],'item_id':l['item_id'],'codigo':l['codigo'],'nome':l['nome'],'categoria':l['categoria'],
            'quantidade':l['quantidade'],'preco_unitario':dinheiro(l['preco_unitario']),
            # CAT3-b: preço base do item e as escolhas (preco_unitario = base + extras); sem opções, opcoes [] e base = unitário.
            'preco_base':dinheiro(l['preco_base'] or l['preco_unitario']),'opcoes':_opcoes_da_linha(l),
            'subtotal':dinheiro(l['preco_unitario']*l['quantidade']),'observacao':l['observacao'],'alcoolico':l['alcoolico'],
            # DISP1: o item é servido no dia e horário do pedido (agendado; sem agendamento, agora)?
            'disponivel':l['id'] not in fora_por_linha} for l in ls]
    subtotal=sum((l['preco_unitario']*l['quantidade'] for l in ls),Decimal('0')) if ls else None
    u=await unidade_da_empresa(conn,tenant,c['unidade'])
    # Taxa só entra quando é entrega e se sabe o valor; senão fica nula (a unidade confirma no retorno).
    area=await entrega_do_carrinho(conn,tenant,u,c,subtotal);taxa=area['taxa']
    tem_alcool=any(l['alcoolico'] for l in ls)
    faltando=[]
    if not ls: faltando.append('itens')
    if not c['modalidade']: faltando.append('modalidade')
    if c['modalidade']=='delivery' and not (c['endereco'] or '').strip(): faltando.append('endereco')
    elif area['motivo'] in FALTA_DA_AREA: faltando.append(FALTA_DA_AREA[area['motivo']])
    if tem_alcool and not c['maioridade_confirmada']: faltando.append('maioridade_confirmada')
    # PED2: horário marcado que já passou não fecha pedido; o agente pergunta outro.
    fuso=await fuso_da_unidade(conn,tenant,u);quando=agendamento.do_carrinho(c,fuso);passou=agendamento.vencido(quando)
    # HOR2: o horário guardado ainda vale para a unidade do carrinho? (já passou, ou a unidade não funciona nessa hora —
    # acontece quando a unidade muda depois de o horário ser marcado). Mesmo formato dos motivos de horário.
    aviso=await aviso_do_horario(conn,tenant,u,fuso,quando)
    if passou or aviso: faltando.append('horario')
    if fora: faltando.append('item_indisponivel')
    # PAG1: forma de pagamento da lista da unidade (a que saiu da lista volta a faltar) e troco acima do total.
    formas=await formas_aceitas(conn,tenant,c['unidade']);forma=next((f for f in formas if f['codigo']==c['forma_pagamento']),None)
    total=subtotal+(taxa or 0) if subtotal is not None else None
    if forma is None: faltando.append('pagamento')
    elif c['troco_para'] is not None and total is not None and c['troco_para']<=total: faltando.append('troco')
    return {'conversation_id':conversa,'unidade':c['unidade'],'modalidade':c['modalidade'],'endereco':c['endereco'],'horario':c['horario'],
            'agendado_para':agendamento.iso(quando,fuso),'horario_vencido':passou,'horario_aviso':aviso,
            'observacao':c['observacao'],'maioridade_confirmada':c['maioridade_confirmada'],'itens':itens,
            'bairro':c['bairro'],'cep':c['cep'],'taxa_origem':area['origem'],'frete_gratis':area['origem']=='frete_gratis',
            'numero_itens':sum(l['quantidade'] for l in ls),'subtotal':dinheiro(subtotal),'taxa_entrega':dinheiro(taxa),
            'total':dinheiro(subtotal+(taxa or 0)) if subtotal is not None else None,
            'pedido_minimo':dinheiro(u['pedido_minimo']) if u else None,'tem_alcoolico':tem_alcool,
            # Itens que não são servidos no dia e horário do pedido, com o motivo e quando são servidos (vazia = nenhum).
            'itens_indisponiveis':fora,
            'forma_pagamento':c['forma_pagamento'],'pagamento_rotulo':forma['rotulo'] if forma else None,'troco_para':dinheiro(c['troco_para']),
            'troco':dinheiro(c['troco_para']-total) if c['troco_para'] is not None and total is not None and c['troco_para']>total else None,
            'formas_aceitas':formas,
            'faltando':faltando,'pode_fechar':not faltando,
            # Última alteração do carrinho (item ou entrega): diz se ele é desta conversa ou antigo.
            'atualizado_em':c['atualizado_em'].isoformat(timespec='seconds')}


def _opcoes_da_linha(l):
    """Escolhas copiadas na linha, como o agente e a unidade veem: grupo, opção, quantidade e preço extra (com os ids)."""
    from modulos.catalogo.opcoes import ler
    return [{'grupo_id':o['grupo_id'],'grupo':o['grupo'],'opcao_id':o['opcao_id'],'opcao':o['opcao'],'quantidade':o['quantidade'],'preco_extra':o['preco_extra']}
            for o in ler(l['opcoes'])]


async def _escolhas(conn,tenant,item,opcoes,linha_id=None):
    """CAT3-b: confere as escolhas do agente contra os grupos do item. (escolhas copiadas, extras, assinatura) ou Recusa."""
    from modulos.catalogo.opcoes import escolher,OpcaoRecusada
    try: return await escolher(conn,tenant,item['id'],opcoes)
    except OpcaoRecusada as r:
        raise Recusa(r.motivo,detalhes={**({'linha_id':linha_id} if linha_id else {}),'item_id':item['id'],
                                         **({'grupo_id':r.grupo['grupo_id'],'grupo':r.grupo} if r.grupo else {})})


async def aviso_do_horario(conn,tenant,u,fuso,quando):
    """None, ou {"motivo": "horario_passado"|"horario_fora_funcionamento", + os detalhes da recusa} para o horário do carrinho."""
    if quando is None: return None
    motivo,detalhes=await agendamento.conferir(conn,tenant,u,fuso,quando)
    return {'motivo':motivo,**detalhes} if motivo in ('horario_passado','horario_fora_funcionamento') else None


async def indisponiveis(conn,tenant,conversa,ls):
    """Linhas do carrinho cujo item não é servido no dia e horário do pedido (DISP1). Lista vazia = todos servem."""
    if not any(l['disponibilidade'] or l['esgotado_ate'] for l in ls): return []
    from modulos.catalogo import disponibilidade as disp
    conf=await disp.da_conversa(conn,tenant,conversa);fora=[]
    for l in ls:
        if not l['disponibilidade'] and not l['esgotado_ate']: continue
        a=await conf.item(l)
        if not a['disponivel']:
            fora.append({'id':l['id'],'item_id':l['item_id'],'nome':l['nome'],'motivo':a['motivo'],'feriado':a['feriado'],
                         'resumo':a['resumo'],'data_referencia':a['data_referencia'],
                         **({'disponivel_a_partir':a['disponivel_a_partir']} if a.get('disponivel_a_partir') else {})})
    return fora


# adicionar_item só aceita item que apareceu numa busca desta conversa há pouco (constante em modulos/catalogo/rotas.py).
async def foi_buscado(conn,tenant,conversa,item_id,carrinho):
    from modulos.catalogo.rotas import ITEM_BUSCADO_MIN
    if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM catalogo_vistos WHERE tenant_id=$1 AND conversation_id=$2 AND item_id=$3
            AND visto_em>now()-make_interval(mins=>$4))''',tenant,conversa,item_id,ITEM_BUSCADO_MIN): return True
    # Somar mais um do que já está no carrinho aberto não exige buscar de novo.
    return bool(carrinho) and await conn.fetchval('SELECT EXISTS(SELECT 1 FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2 AND item_id=$3)',tenant,carrinho['id'],item_id)


def vendavel(item):
    """Regra 1: só entra em carrinho item ativo, com preço e com delivery diferente de "nao"."""
    if item is None: return 'item_inexistente'
    # CAT3-a: arquivado não pode ser pedido (mesma recusa do inativo).
    if not item['ativo'] or item['tipo'] not in ('item','adicional') or item.get('arquivado_em'): return 'item_inativo'
    if item['preco'] is None: return 'sem_preco'
    if item['delivery']=='nao': return 'sem_delivery'
    return None


async def tocar(conn,tenant,carrinho_id):
    await conn.execute('UPDATE carrinhos SET atualizado_em=now() WHERE tenant_id=$1 AND id=$2',tenant,carrinho_id)


# Skills que mudam carrinho ou pedido. As duas últimas têm efeito para fora (unidade e cliente avisados):
# turno que as chamou com sucesso não é refeito (UX2, P2).
SKILLS_DE_CARRINHO=('adicionar_item','alterar_item','remover_item','esvaziar_carrinho','definir_entrega')
SKILLS_COM_EFEITO=('fechar_pedido','cancelar_pedido')


async def registrar_skill(db,tenant,conversa,skill,dados,resultado):
    """Registro curto da chamada (sem endereço nem observação). Falha aqui nunca derruba a Skill."""
    try:
        await db.execute('INSERT INTO skill_chamadas(tenant_id,conversation_id,skill,dados,resultado) VALUES ($1,$2,$3,$4::jsonb,$5)',
                         tenant,conversa,skill,json.dumps({k:v for k,v in dados.items() if v is not None},ensure_ascii=False),str(resultado)[:60])
    except Exception:
        import logging;logging.getLogger('gateway.pedidos').warning('Chamada de Skill não registrada')


async def nome_do_item(db,tenant,item_id=None,linha_id=None):
    """Nome do item do catálogo (pelo id do item ou pela linha do carrinho), para o resumo da chamada."""
    if linha_id is not None:
        return await db.fetchval('SELECT i.nome FROM carrinho_itens l JOIN catalogo_itens i ON i.tenant_id=l.tenant_id AND i.id=l.item_id WHERE l.tenant_id=$1 AND l.id=$2',tenant,linha_id)
    return await db.fetchval('SELECT nome FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant,item_id)


async def acoes_do_turno(db,tenant,conversa,inicio,fim):
    """Chamadas de Skill feitas na conversa dentro da janela de um turno (agente_inicio..agente_fim)."""
    rows=await db.fetch("""SELECT skill,dados,resultado FROM skill_chamadas WHERE tenant_id=$1 AND conversation_id=$2
        AND criado_em>=$3 AND criado_em<=$4 ORDER BY id LIMIT 30""",tenant,conversa,inicio,fim)
    return [_acao(r) for r in rows]


def _acao(r):
    d=json.loads(r['dados']) if isinstance(r['dados'],str) else (r['dados'] or {})
    return {'skill':r['skill'],'item':d.get('item'),'quantidade':d.get('quantidade'),'resultado':r['resultado'],
            **({'detalhe':d['detalhe']} if d.get('detalhe') else {})}


MAX_ACOES_RECENTES=20


async def acoes_recentes(db,tenant,conversa,minutos):
    """Chamadas de Skill desta conversa nos últimos `minutos` (UX3.1): as 20 mais recentes, da mais antiga
    para a mais nova, com há quantos segundos foi cada uma. Mesmo resumo de acoes_ja_executadas.
    ENC1-a: só as do atendimento atual (Skill de um atendimento encerrado não vale como "acabei de fazer")."""
    if not minutos or minutos<=0: return []
    rows=await db.fetch("""SELECT skill,dados,resultado,greatest(0,extract(epoch FROM now()-criado_em))::int ha FROM skill_chamadas
        WHERE tenant_id=$1 AND conversation_id=$2 AND criado_em>now()-make_interval(mins=>$3)
        AND atendimento_id IS NOT DISTINCT FROM (SELECT atendimento_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2)
        ORDER BY id DESC LIMIT $4""",
        tenant,conversa,int(minutos),MAX_ACOES_RECENTES)
    return [{**_acao(r),'ha_segundos':r['ha']} for r in reversed(rows)]


async def adicionar(conn,tenant,conversa,item_id,quantidade,observacao,opcoes=None):
    await travar(conn,tenant,conversa)
    item=await conn.fetchrow('SELECT * FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant,item_id)
    # Ordem das conferências (DISP1): existe → foi buscado nesta conversa → é da unidade do carrinho → pode ser vendido → é servido no dia do pedido.
    if item is None: raise Recusa('item_inexistente',404)
    c=await aberto(conn,tenant,conversa)
    # A recusa não diz nada sobre o item enviado: ele não foi buscado e pode ser de outra unidade.
    if not await foi_buscado(conn,tenant,conversa,item_id,c):
        from modulos.catalogo.rotas import ITEM_BUSCADO_MIN
        raise Recusa('item_nao_buscado',detalhes={'janela_min':ITEM_BUSCADO_MIN})
    # Regra 2: carrinho de uma unidade só. O primeiro item define a unidade. Vem antes do preço: item de outra unidade
    # sem preço é "unidade diferente", não "sem preço".
    if c and c['unidade'] and c['unidade']!=item['unidade']:
        raise Recusa('unidade_diferente',detalhes={'unidade_do_carrinho':c['unidade'],'unidade_do_item':item['unidade']})
    motivo=vendavel(item)
    if motivo: raise Recusa(motivo)
    from modulos.catalogo import disponibilidade as disp
    a=await (await disp.da_conversa(conn,tenant,conversa)).item(item)
    if not a['disponivel']:
        raise Recusa('item_indisponivel',detalhes={'motivo_indisponivel':a['motivo'],'feriado':a['feriado'],'resumo':a['resumo'],
                                                    'disponibilidade':a['disponibilidade'],'data_referencia':a['data_referencia'],
                                                    **({'disponivel_a_partir':a['disponivel_a_partir']} if a.get('disponivel_a_partir') else {})})
    # CAT3-b: escolhas conferidas com os grupos do item (obrigatório, máximo, mínimo, repetição) antes de mexer no carrinho.
    escolhas,extras,assinatura=await _escolhas(conn,tenant,item,opcoes)
    preco=item['preco']+extras
    c=c or await aberto(conn,tenant,conversa,criar=True)
    if not c['unidade']: await conn.execute('UPDATE carrinhos SET unidade=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],item['unidade'])
    obs=(observacao or '').strip() or None
    igual=await conn.fetchrow('''SELECT id,quantidade FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2 AND item_id=$3
        AND observacao IS NOT DISTINCT FROM $4 AND assinatura=$5''',tenant,c['id'],item_id,obs,assinatura)
    if igual:
        # Mesmo item com a mesma observação e as mesmas escolhas soma; o preço volta a ser o do catálogo de agora.
        await conn.execute('UPDATE carrinho_itens SET quantidade=$3,preco_unitario=$4,preco_base=$5,opcoes=$6::jsonb WHERE tenant_id=$1 AND id=$2',
                           tenant,igual['id'],min(MAX_QUANTIDADE,igual['quantidade']+quantidade),preco,item['preco'],json.dumps(escolhas,ensure_ascii=False))
    else:
        if await conn.fetchval('SELECT count(*) FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2',tenant,c['id'])>=MAX_LINHAS:
            raise Recusa('carrinho_cheio')
        # Preço copiado do catálogo (base + extras das opções): o que o agente disser não entra.
        await conn.execute('''INSERT INTO carrinho_itens(tenant_id,carrinho_id,item_id,quantidade,preco_unitario,observacao,preco_base,opcoes,assinatura)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)''',tenant,c['id'],item_id,quantidade,preco,obs,item['preco'],json.dumps(escolhas,ensure_ascii=False),assinatura)
    await tocar(conn,tenant,c['id'])


async def _linha(conn,tenant,conversa,linha_id):
    c=await aberto(conn,tenant,conversa)
    l=c and await conn.fetchrow('SELECT * FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2 AND id=$3',tenant,c['id'],linha_id)
    if not l: raise Recusa('linha_inexistente',404)
    return c,l


async def _unidade_se_vazio(conn,tenant,c):
    # Sem itens, o carrinho deixa de estar preso à unidade e a maioridade volta a ser pedida.
    if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2)',tenant,c['id']):
        await conn.execute('UPDATE carrinhos SET unidade=NULL,maioridade_confirmada=false WHERE tenant_id=$1 AND id=$2',tenant,c['id'])
    await tocar(conn,tenant,c['id'])


async def alterar(conn,tenant,conversa,linha_id,quantidade=None,observacao=None,mudar_observacao=False,opcoes=None,mudar_opcoes=False):
    await travar(conn,tenant,conversa)
    c,l=await _linha(conn,tenant,conversa,linha_id)
    if quantidade==0:
        await conn.execute('DELETE FROM carrinho_itens WHERE tenant_id=$1 AND id=$2',tenant,l['id'])
    else:
        if mudar_opcoes:
            # CAT3-b: `opcoes` substitui todas as escolhas da linha; preço refeito com o catálogo de agora.
            item=await conn.fetchrow('SELECT * FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant,l['item_id'])
            escolhas,extras,assinatura=await _escolhas(conn,tenant,item,opcoes,l['id'])
            await conn.execute('UPDATE carrinho_itens SET opcoes=$3::jsonb,assinatura=$4,preco_base=$5,preco_unitario=$6 WHERE tenant_id=$1 AND id=$2',
                               tenant,l['id'],json.dumps(escolhas,ensure_ascii=False),assinatura,item['preco'],item['preco']+extras)
        await conn.execute('UPDATE carrinho_itens SET quantidade=coalesce($3,quantidade),observacao=CASE WHEN $5 THEN $4 ELSE observacao END WHERE tenant_id=$1 AND id=$2',
                           tenant,l['id'],quantidade,(observacao or '').strip() or None,mudar_observacao)
    await _unidade_se_vazio(conn,tenant,c)


async def remover(conn,tenant,conversa,linha_id):
    await travar(conn,tenant,conversa)
    c,l=await _linha(conn,tenant,conversa,linha_id)
    await conn.execute('DELETE FROM carrinho_itens WHERE tenant_id=$1 AND id=$2',tenant,l['id'])
    await _unidade_se_vazio(conn,tenant,c)


async def esvaziar(conn,tenant,conversa):
    await travar(conn,tenant,conversa)
    c=await aberto(conn,tenant,conversa)
    if c:
        await conn.execute('DELETE FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2',tenant,c['id'])
        # Volta ao estado inicial completo: nada do pedido anterior (retirada, endereço, horário…) fica para o agente presumir.
        await conn.execute("""UPDATE carrinhos SET unidade=NULL,modalidade=NULL,endereco=NULL,bairro=NULL,cep=NULL,bairro_confirmado=false,horario=NULL,agendado_para=NULL,observacao=NULL,
            maioridade_confirmada=false,forma_pagamento=NULL,troco_para=NULL,atualizado_em=now() WHERE tenant_id=$1 AND id=$2""",tenant,c['id'])


async def substituir_itens(conn,tenant,conversa):
    """Cardápio interativo: a página manda a lista inteira. Tira os itens e a unidade, mas guarda o que o cliente já disse na
    conversa (endereço, horário ainda válido). Horário que passou sai aqui: a página não tem como pedir outro."""
    await travar(conn,tenant,conversa)
    c=await aberto(conn,tenant,conversa)
    if not c: return
    await conn.execute('DELETE FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2',tenant,c['id'])
    u=await unidade_da_empresa(conn,tenant,c['unidade'])
    vencido=agendamento.vencido(agendamento.do_carrinho(c,await fuso_da_unidade(conn,tenant,u)))
    await conn.execute(f"""UPDATE carrinhos SET unidade=NULL,forma_pagamento=NULL,troco_para=NULL{',horario=NULL,agendado_para=NULL' if vencido else ''}
        WHERE tenant_id=$1 AND id=$2""",tenant,c['id'])


async def entrega(conn,tenant,conversa,dados):
    """Unidade, modalidade, endereço, horário, observação e maioridade. Só muda o que veio."""
    await travar(conn,tenant,conversa)
    c=await aberto(conn,tenant,conversa,criar=True)
    if 'unidade' in dados and dados['unidade'] and dados['unidade']!=c['unidade']:
        if not await unidade_da_empresa(conn,tenant,dados['unidade']): raise Recusa('unidade_inexistente',404)
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2)',tenant,c['id']):
            raise Recusa('unidade_diferente',detalhes={'unidade_do_carrinho':c['unidade'],'unidade_do_item':dados['unidade']})
        # PAG1: a forma de pagamento é da lista da unidade; com outra unidade, é escolhida de novo.
        await conn.execute('UPDATE carrinhos SET unidade=$3,forma_pagamento=NULL,troco_para=NULL WHERE tenant_id=$1 AND id=$2',tenant,c['id'],dados['unidade'])
        c=await aberto(conn,tenant,conversa)
    if dados.get('modalidade')=='retirada':
        u=await unidade_da_empresa(conn,tenant,dados.get('unidade') or c['unidade'])
        if u and u['aceita_retirada'] is False: raise Recusa('retirada_indisponivel')
    if dados.get('modalidade')=='delivery':
        u=await unidade_da_empresa(conn,tenant,dados.get('unidade') or c['unidade'])
        if u and u['delivery_ativo'] is False: raise Recusa('delivery_indisponivel')
    if 'horario' in dados or 'agendado_para' in dados:
        await _agendar(conn,tenant,c,dados)
    # Endereço novo sem bairro e CEP: os de antes eram do endereço antigo.
    if 'endereco' in dados and not {'bairro','cep'}&set(dados):
        await conn.execute('UPDATE carrinhos SET bairro=NULL,cep=NULL,bairro_confirmado=false WHERE tenant_id=$1 AND id=$2',tenant,c['id'])
    if dados.get('cep') and not cep_limpo(dados['cep']): raise Recusa('cep_invalido')
    if 'cep' in dados:
        await conn.execute('UPDATE carrinhos SET cep=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],cep_limpo(dados['cep'] or ''))
    if 'bairro' in dados:
        novo=(dados['bairro'] or '').strip() or None
        await conn.execute('UPDATE carrinhos SET bairro=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],novo)
        # A confirmação é daquele bairro: com outro, a grafia volta a ser conferida.
        if nome_busca(novo or '')!=nome_busca(c['bairro'] or ''):
            await conn.execute('UPDATE carrinhos SET bairro_confirmado=false WHERE tenant_id=$1 AND id=$2',tenant,c['id'])
    if 'bairro_confirmado' in dados:
        await conn.execute('UPDATE carrinhos SET bairro_confirmado=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],bool(dados['bairro_confirmado']))
    for campo in ('modalidade','endereco','observacao'):
        if campo in dados:
            valor=(dados[campo] or '').strip() or None if isinstance(dados[campo],str) or dados[campo] is None else dados[campo]
            await conn.execute(f'UPDATE carrinhos SET {campo}=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],valor)
    if dados.get('maioridade_confirmada') is not None:
        await conn.execute('UPDATE carrinhos SET maioridade_confirmada=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],bool(dados['maioridade_confirmada']))
    # Área conferida na hora, com o endereço já gravado: o cliente sabe antes de escolher mais nada. Recusa desfaz tudo.
    if {'endereco','bairro','cep'}&set(dados) or dados.get('modalidade')=='delivery':
        atual=await aberto(conn,tenant,conversa);u=await unidade_da_empresa(conn,tenant,atual['unidade'])
        if u and (atual['endereco'] or atual['bairro'] or atual['cep']): recusar_entrega(await entrega_do_carrinho(conn,tenant,u,atual,None),u)
    # Por último: o troco é comparado com o total já com a modalidade (e a taxa) desta mesma chamada. Recusa desfaz tudo.
    if 'forma_pagamento' in dados or 'troco_para' in dados:
        await _pagamento(conn,tenant,c,dados)
    await tocar(conn,tenant,c['id'])


async def formas_aceitas(conn,tenant,codigo_unidade):
    """PAG1: formas de pagamento ativas da unidade, na ordem dela (vazia = não cadastradas)."""
    if not codigo_unidade: return []
    return [dict(r) for r in await conn.fetch('''SELECT p.codigo,p.rotulo,p.aceita_troco FROM unidade_pagamentos p
        JOIN tenant_unidades u ON u.tenant_id=p.tenant_id AND u.id=p.unidade_id
        WHERE p.tenant_id=$1 AND u.codigo=$2 AND p.ativo ORDER BY p.ordem,p.id''',tenant,codigo_unidade)]


async def _total(conn,tenant,c):
    """Total do carrinho como no retrato (subtotal + taxa da entrega), ou None sem itens."""
    subtotal=await conn.fetchval('SELECT sum(preco_unitario*quantidade) FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2',tenant,c['id'])
    if subtotal is None: return None
    u=await unidade_da_empresa(conn,tenant,c['unidade'])
    return subtotal+((await entrega_do_carrinho(conn,tenant,u,c,subtotal))['taxa'] or 0)


async def _pagamento(conn,tenant,c,dados):
    """PAG1: forma de pagamento (código da lista da unidade; null tira) e troco (só dinheiro com troco aceito; maior que o total;
    null = não precisa). Trocar para outra forma tira o troco. Recusa sem mudar nada."""
    c=await aberto(conn,tenant,c['conversation_id'])
    formas=await formas_aceitas(conn,tenant,c['unidade'])
    forma=c['forma_pagamento']
    if 'forma_pagamento' in dados:
        forma=dados['forma_pagamento']
        if forma is not None and forma not in [f['codigo'] for f in formas]:
            raise Recusa('forma_pagamento_invalida',detalhes={'formas_aceitas':formas})
    troco=c['troco_para'] if forma==c['forma_pagamento'] else None
    if 'troco_para' in dados: troco=dados['troco_para']
    if troco is not None:
        troco=Decimal(str(troco)).quantize(Decimal('0.01'))
        if not any(f['codigo']==forma and f['aceita_troco'] for f in formas):
            raise Recusa('troco_so_em_dinheiro',detalhes={'formas_aceitas':formas})
        total=await _total(conn,tenant,c)
        if total is not None and troco<=total: raise Recusa('troco_menor_que_total',detalhes={'total':dinheiro(total)})
    await conn.execute('UPDATE carrinhos SET forma_pagamento=$3,troco_para=$4 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],forma,troco)


async def _agendar(conn,tenant,c,dados):
    """Horário do pedido (PED2): `agendado_para` (data e hora) vale quando vem; só com o texto `horario`, a hora é lida dele.
    Texto que não dá para ler, horário no passado, fora do funcionamento ou longe demais são recusados e nada muda."""
    u=await unidade_da_empresa(conn,tenant,dados.get('unidade') or c['unidade'])
    fuso=await fuso_da_unidade(conn,tenant,u);agora=agendamento.agora_utc()
    texto=(dados.get('horario') or '').strip() or None
    if dados.get('agendado_para'):
        quando=agendamento.de_iso(dados['agendado_para'],fuso)
        if quando is None: raise Recusa('horario_invalido')
    elif texto:
        situacao,quando=agendamento.interpretar(texto,agora.astimezone(ZoneInfo(fuso)))
        if situacao=='invalido': raise Recusa('horario_invalido')
    else: quando=None
    if quando is not None:
        motivo,detalhes=await agendamento.conferir(conn,tenant,u,fuso,quando,agora)
        if motivo: raise Recusa(motivo,detalhes=detalhes)
        # Sem texto, o gateway escreve o que o cliente vai ler ("hoje às 19h").
        texto=texto or expediente_em_palavras(quando,fuso,agora)
    await conn.execute('UPDATE carrinhos SET horario=$3,agendado_para=$4 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],texto,quando)


def expediente_em_palavras(quando,fuso,agora):
    from nucleo.expediente import em_palavras
    return em_palavras(quando.isoformat(),fuso,agora)


async def conferir_horario(conn,tenant,c,u):
    """No fechamento: o horário podia valer quando foi marcado e já ter passado. Devolve o horário agendado (ou None)."""
    fuso=await fuso_da_unidade(conn,tenant,u);quando=agendamento.do_carrinho(c,fuso)
    if quando is None: return None
    motivo,detalhes=await agendamento.conferir(conn,tenant,u,fuso,quando)
    # Longe demais só é barrado ao marcar; aqui valem o passado e o funcionamento.
    if motivo in ('horario_passado','horario_fora_funcionamento'): raise Recusa(motivo,detalhes=detalhes)
    return quando


async def _numero(conn,tenant):
    """Número curto, só dígitos (fácil de ditar e de digitar em "OK <numero>"), único na empresa."""
    for digitos in (5,5,5,6,6,7,8):
        n=str(secrets.randbelow(9*10**(digitos-1))+10**(digitos-1))
        if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pedidos WHERE tenant_id=$1 AND numero=$2)',tenant,n): return n
    raise RuntimeError('Não foi possível gerar o número do pedido')


def retrato_pedido(p):
    itens=json.loads(p['itens']) if isinstance(p['itens'],str) else p['itens']
    return {'numero':p['numero'],'status':p['status'],'conversation_id':p['conversation_id'],'unidade':p['unidade'],'modalidade':p['modalidade'],
            'endereco':p['endereco'],'horario':p['horario'],
            'bairro':p.get('bairro'),'cep':p.get('cep'),'taxa_origem':p.get('taxa_origem'),'frete_gratis':bool(p.get('frete_gratis')),
            'agendado_para':p['agendado_para'].isoformat(timespec='seconds') if p.get('agendado_para') else None,
            'observacao':p['observacao'],'itens':itens,
            'subtotal':dinheiro(p['subtotal']),'taxa_entrega':dinheiro(p['taxa_entrega']),'total':dinheiro(p['total']),
            'tem_alcoolico':p['tem_alcoolico'],'motivo':p['motivo'],'cliente_nome':p['cliente_nome'],
            'criado_em':p['criado_em'],'decidido_em':p['decidido_em'],
            'previsao_min':p.get('previsao_min'),'atualizado_em':p['atualizado_em'],
            # PAG1: null nos pedidos de antes do PAG1.
            'pagamento':_pagamento_do_pedido(p)}


def _pagamento_do_pedido(p):
    if not p.get('forma_pagamento'): return None
    troco_para=p.get('troco_para')
    return {'forma':p['forma_pagamento'],'rotulo':p.get('pagamento_rotulo') or p['forma_pagamento'],'troco_para':dinheiro(troco_para),
            'troco':dinheiro(troco_para-p['total']) if troco_para is not None and p['total'] is not None else None}


# Ciclo do pedido (PED1). Só anda para frente; pronto_retirada e saiu_entrega ocupam a mesma etapa, cada um na sua modalidade.
STATUS=('aguardando_loja','confirmado','em_preparo','pronto_retirada','saiu_entrega','concluido','recusado','cancelado')
FINAIS=('concluido','recusado','cancelado')
ETAPA={'aguardando_loja':0,'confirmado':1,'em_preparo':2,'pronto_retirada':3,'saiu_entrega':3,'concluido':4}
MODALIDADE_DO_STATUS={'pronto_retirada':'retirada','saiu_entrega':'delivery'}
DESCRICAO={'aguardando_loja':'Aguardando a confirmação da unidade','confirmado':'Confirmado pela unidade','em_preparo':'Em preparo',
           'pronto_retirada':'Pronto para retirada','saiu_entrega':'Saiu para entrega','concluido':'Concluído',
           'recusado':'Recusado pela unidade','cancelado':'Cancelado'}
ACEITA_PREVISAO=('confirmado','em_preparo')


class TransicaoInvalida(Exception):
    """Mudança de status que a máquina não permite: o pedido fica como está."""
    def __init__(self,pedido,motivo): self.pedido,self.motivo=pedido,motivo


def conferir(status,modalidade,novo):
    """Motivo pelo qual `novo` não vale a partir de `status`, ou None se vale."""
    if status in FINAIS: return 'status_final'
    if novo==status: return 'mesmo_status'
    if novo in MODALIDADE_DO_STATUS and MODALIDADE_DO_STATUS[novo]!=modalidade: return 'modalidade_incompativel'
    if novo=='recusado': return None if status=='aguardando_loja' else 'recusa_so_aguardando'
    if novo=='cancelado': return None
    if ETAPA[novo]<=ETAPA[status]: return 'nao_pode_voltar'
    return None


def proximos(p):
    """Status para onde este pedido pode ir agora (o painel só desenha estes)."""
    return [s for s in STATUS if conferir(p['status'],p['modalidade'],s) is None]


async def registrar_evento(conn,tenant,pedido_id,de,para,por,origem,motivo=None,previsao_min=None):
    return await conn.fetchval('''INSERT INTO pedido_eventos(tenant_id,pedido_id,de,para,por,origem,motivo,previsao_min)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (pedido_id,para) DO NOTHING RETURNING id''',tenant,pedido_id,de,para,por[:100],origem,motivo,previsao_min)


async def transicionar(conn,tenant,numero,novo,por,origem,motivo=None,previsao_min=None):
    """Muda o status do pedido (travado na transação) e grava a linha do tempo.
    Devolve (pedido atualizado, id do evento); None se o pedido não existe; TransicaoInvalida se a máquina não permite."""
    assert novo in STATUS and novo!='aguardando_loja'
    p=await conn.fetchrow('SELECT * FROM pedidos WHERE tenant_id=$1 AND numero=$2 FOR UPDATE',tenant,numero)
    if not p: return None
    erro=conferir(p['status'],p['modalidade'],novo)
    if erro: raise TransicaoInvalida(p,erro)
    antes=p['status'];motivo=(motivo or '').strip()[:300] or None
    # Quem tira o pedido de "aguardando a loja" é quem decidiu (confirmou, recusou ou cancelou).
    p=await conn.fetchrow("""UPDATE pedidos SET status=$3,atualizado_em=now(),
        motivo=CASE WHEN $3 IN ('recusado','cancelado') THEN $4 ELSE motivo END,
        previsao_min=coalesce($5,previsao_min),
        decidido_em=CASE WHEN $6 THEN now() ELSE decidido_em END,decidido_por=CASE WHEN $6 THEN $7 ELSE decidido_por END
        WHERE tenant_id=$1 AND id=$2 RETURNING *""",tenant,p['id'],novo,motivo,previsao_min,antes=='aguardando_loja',por[:100])
    if novo in ('recusado','cancelado'):
        # FUP1-a: pedido recusado ou cancelado pela unidade → o follow-up da conversa sai.
        from nucleo import follow_up
        await follow_up.cancelar(conn,tenant,p['conversation_id'],'pedido_fechado',sem_esperar=True)
    if novo=='recusado' and p['carrinho_id']:
        # Recusado: os itens voltam para o carrinho do cliente, se ele ainda não abriu outro.
        if not await aberto(conn,tenant,p['conversation_id']):
            await conn.execute("UPDATE carrinhos SET status='aberto',atualizado_em=now() WHERE tenant_id=$1 AND id=$2 AND status='fechado'",tenant,p['carrinho_id'])
    return p,await registrar_evento(conn,tenant,p['id'],antes,novo,por,origem,motivo,previsao_min)


async def linha_do_tempo(conn,tenant,p):
    """Eventos do pedido, do mais antigo ao mais recente, com a situação do aviso ao cliente de cada um."""
    rows=await conn.fetch('''SELECT e.de,e.para,e.por,e.origem,e.motivo,e.previsao_min,e.aviso,e.criado_em,
            a.status AS aviso_status,a.enviado_em AS aviso_em,a.erro AS aviso_erro
        FROM pedido_eventos e LEFT JOIN avisos_internos a ON a.tenant_id=e.tenant_id AND a.tipo='pedido_cliente'
            AND a.referencia=$3||':'||e.para AND e.aviso='enfileirado'
        WHERE e.tenant_id=$1 AND e.pedido_id=$2 ORDER BY e.id''',tenant,p['id'],p['numero'])
    from .avisos import incerto
    def aviso(r):
        if r['aviso'] is None: return None
        if r['aviso']=='desligado': return {'status':'desligado','em':None,'erro':None,'incerto':False,'pode_reenviar':False}
        falhou=r['aviso_status']=='falhou'
        # Só se reenvia aviso que falhou, e só enquanto o pedido ainda está naquele status. Incerto = pode ter chegado ao cliente.
        return {'status':r['aviso_status'] or 'pendente','em':r['aviso_em'],'erro':r['aviso_erro'],
                'incerto':falhou and incerto(r['aviso_erro']),'pode_reenviar':falhou and r['para']==p['status']}
    return [{'de':r['de'],'para':r['para'],'por':r['por'],'origem':r['origem'],'em':r['criado_em'],'motivo':r['motivo'],
             'previsao_min':r['previsao_min'],'aviso':aviso(r)} for r in rows]


async def para_o_agente(conn,tenant,p):
    """Pedido para a Skill consultar_pedido: situação em palavras, previsão e linha do tempo resumida (sem quem mexeu)."""
    from zoneinfo import ZoneInfo
    from datetime import timedelta
    fuso=ZoneInfo(await conn.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant))
    local=lambda d: d.astimezone(fuso).isoformat(timespec='seconds') if d else None
    eventos=await conn.fetch('SELECT para,criado_em FROM pedido_eventos WHERE tenant_id=$1 AND pedido_id=$2 ORDER BY id',tenant,p['id'])
    # A previsão conta de quando a unidade assumiu o pedido (confirmou ou já começou a preparar).
    base=next((e['criado_em'] for e in eventos if e['para'] in ACEITA_PREVISAO),None)
    r=retrato_pedido(p)
    r.update(status_descricao=DESCRICAO[p['status']],criado_em=local(p['criado_em']),decidido_em=local(p['decidido_em']),atualizado_em=local(p['atualizado_em']),
             previsao_ate=local(base+timedelta(minutes=p['previsao_min'])) if base and p['previsao_min'] else None,
             atualizado_ha_segundos=await conn.fetchval('SELECT greatest(0,extract(epoch FROM now()-$1::timestamptz))::int',p['atualizado_em']),
             linha_do_tempo=[{'status':e['para'],'em':local(e['criado_em'])} for e in eventos])
    return r


MAX_ULTIMOS_PEDIDOS=3


async def ultimos(conn,tenant,conversa,fuso=None):
    """Os últimos pedidos da conversa (mais recente primeiro), resumidos para o agente."""
    from zoneinfo import ZoneInfo
    rows=await conn.fetch('SELECT numero,status,unidade,total,atualizado_em FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT $3',
                          tenant,conversa,MAX_ULTIMOS_PEDIDOS)
    if not rows: return []
    z=ZoneInfo(fuso or await conn.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant))
    return [{'numero':r['numero'],'status':r['status'],'status_descricao':DESCRICAO[r['status']],'unidade':r['unidade'],'total':dinheiro(r['total']),
             'atualizado_em':r['atualizado_em'].astimezone(z).isoformat(timespec='seconds')} for r in rows]


ORIGENS=('real','piloto','simulado','teste')
# Canal do pedido a partir do tipo do canal da conversa (uazapi é o WhatsApp).
CANAL_DO_TIPO={'uazapi':'whatsapp'}


async def origem_e_canal(conn,tenant,conversa):
    """(origem, canal) do pedido que está sendo fechado (LGPD1/AN1). Na ordem: empresa de teste → 'teste'; conversa simulada,
    agente em mock ou envio de teste → 'simulado'; cliente da lista do piloto com o piloto ligado → 'piloto'; senão 'real'."""
    from nucleo.banco import Repositorio
    from nucleo.configuracao import Configuracoes
    v=await conn.fetchrow("""SELECT v.agente_id,v.telefone,v.simulada,v.canal,k.tipo,t.tenant_teste FROM conversas v JOIN tenants t ON t.id=v.tenant_id
        LEFT JOIN tenant_canais k ON k.tenant_id=v.tenant_id AND k.id=v.canal_id WHERE v.tenant_id=$1 AND v.conversation_id=$2""",tenant,conversa)
    canal=CANAL_DO_TIPO.get(v['tipo'] or v['canal'],v['tipo'] or v['canal'])
    if v['tenant_teste']: return 'teste',canal
    cfg=await Configuracoes(Repositorio(conn)).efetiva(tenant,v['agente_id'])
    if v['simulada'] or cfg.get('agente_mode')=='mock' or cfg.get('send_mode')!='live': return 'simulado',canal
    if cfg.get('modo_piloto') and v['telefone'] in (cfg.get('contatos_permitidos') or []): return 'piloto',canal
    return 'real',canal


async def fechar(conn,tenant,conversa):
    """Fecha o carrinho em pré-pedido (aguardando a unidade). Revalida tudo com o catálogo de agora."""
    await travar(conn,tenant,conversa)
    c=await aberto(conn,tenant,conversa)
    ls=await linhas(conn,tenant,c['id']) if c else []
    if not ls: raise Recusa('carrinho_vazio')
    mudou=False
    for l in ls:
        motivo=vendavel({'ativo':l['ativo'],'tipo':l['tipo'],'preco':l['preco_atual'],'delivery':l['delivery'],'arquivado_em':l['arquivado_em']})
        if motivo: raise Recusa(motivo)
        if l['unidade']!=c['unidade']: raise Recusa('unidade_diferente')
        # CAT3-b: as escolhas são conferidas de novo com os grupos de agora (grupo novo obrigatório, opção que saiu) e o preço refeito.
        from modulos.catalogo.opcoes import ler
        guardadas=[{'grupo_id':o['grupo_id'],'opcao_id':o['opcao_id'],'quantidade':o['quantidade']} for o in ler(l['opcoes'])]
        escolhas,extras,_=await _escolhas(conn,tenant,{'id':l['item_id']},guardadas,l['id'])
        if l['preco_atual']+extras!=l['preco_unitario']:
            # Preço sempre do banco: atualiza o carrinho e devolve para o agente confirmar o novo total.
            await conn.execute('UPDATE carrinho_itens SET preco_unitario=$3,preco_base=$4,opcoes=$5::jsonb WHERE tenant_id=$1 AND id=$2',
                               tenant,l['id'],l['preco_atual']+extras,l['preco_atual'],json.dumps(escolhas,ensure_ascii=False));mudou=True
    if mudou: return None
    if not c['modalidade']: raise Recusa('falta_modalidade')
    if c['modalidade']=='delivery' and not (c['endereco'] or '').strip(): raise Recusa('falta_endereco')
    tem_alcool=any(l['alcoolico'] for l in ls)
    # Regra 3: bebida alcoólica só fecha com maioridade confirmada.
    if tem_alcool and not c['maioridade_confirmada']: raise Recusa('falta_maioridade')
    u=await unidade_da_empresa(conn,tenant,c['unidade'])
    if c['modalidade']=='retirada' and u and u['aceita_retirada'] is False: raise Recusa('retirada_indisponivel')
    if c['modalidade']=='delivery' and u and u['delivery_ativo'] is False: raise Recusa('delivery_indisponivel')
    subtotal=sum((l['preco_unitario']*l['quantidade'] for l in ls),Decimal('0'))
    area=await entrega_do_carrinho(conn,tenant,u,c,subtotal)
    if u: recusar_entrega(area,u)
    if u and u['pedido_minimo'] is not None and subtotal<u['pedido_minimo']: raise Recusa('abaixo_do_minimo')
    agendado=await conferir_horario(conn,tenant,c,u)
    # Análise de comportamento (26/10): pedido para já com a unidade fechada (horário, exceção ou pausa) não fecha, qualquer que seja o
    # modo de fora do horário. Sem horário cadastrado não dá para afirmar que está fechada: segue (a unidade confirma).
    if agendado is None and u:
        from nucleo.horarios import situacao_da_unidade
        s=await situacao_da_unidade(conn,tenant,u,await conn.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant),agendamento.agora_utc())
        if s['aberta_agora'] is False:
            raise Recusa('unidade_fechada',detalhes={'motivo_unidade':s['motivo'],'proxima_abertura':s.get('proxima_abertura'),'pausada_ate':s.get('pausada_ate')})
    # DISP1: confere de novo se cada item é servido no dia e horário do pedido.
    fora=await indisponiveis(conn,tenant,conversa,ls)
    if fora: raise Recusa('item_indisponivel',detalhes={'itens':fora,'resumo':fora[0]['resumo'],'motivo_indisponivel':fora[0]['motivo'],'data_referencia':fora[0]['data_referencia']})
    taxa=area['taxa']
    # PAG1: forma de pagamento obrigatória, ainda aceita pela unidade; troco acima do total final.
    formas=await formas_aceitas(conn,tenant,c['unidade'])
    if not c['forma_pagamento']: raise Recusa('pagamento_nao_informado',detalhes={'formas_aceitas':formas})
    forma=next((f for f in formas if f['codigo']==c['forma_pagamento']),None)
    if forma is None: raise Recusa('forma_pagamento_invalida',detalhes={'formas_aceitas':formas})
    if c['troco_para'] is not None:
        if not forma['aceita_troco']: raise Recusa('troco_so_em_dinheiro',detalhes={'formas_aceitas':formas})
        if c['troco_para']<=subtotal+(taxa or 0): raise Recusa('troco_menor_que_total',detalhes={'total':dinheiro(subtotal+(taxa or 0))})
    itens=[{'item_id':l['item_id'],'codigo':l['codigo'],'nome':l['nome'],'categoria':l['categoria'],'quantidade':l['quantidade'],
            'preco_unitario':dinheiro(l['preco_unitario']),'subtotal':dinheiro(l['preco_unitario']*l['quantidade']),
            'observacao':l['observacao'],'alcoolico':l['alcoolico'],
            # CAT3-b: o pedido guarda a cópia (o pedido não muda se a opção mudar depois).
            'preco_base':dinheiro(l['preco_base'] or l['preco_unitario']),'opcoes':_opcoes_da_linha(l)} for l in ls]
    conv=await conn.fetchrow('SELECT telefone FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
    nome,_=await nome_da_conversa(conn,tenant,conversa)
    origem,canal=await origem_e_canal(conn,tenant,conversa)
    p=await conn.fetchrow('''INSERT INTO pedidos(tenant_id,numero,conversation_id,carrinho_id,unidade,modalidade,endereco,horario,observacao,
        itens,subtotal,taxa_entrega,total,cliente_nome,cliente_telefone,tem_alcoolico,maioridade_confirmada,agendado_para,origem,canal,
        forma_pagamento,pagamento_rotulo,troco_para,bairro,cep,taxa_origem,frete_gratis)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27) RETURNING *''',
        tenant,await _numero(conn,tenant),conversa,c['id'],c['unidade'],c['modalidade'],c['endereco'] if c['modalidade']=='delivery' else None,
        c['horario'],c['observacao'],json.dumps(itens,ensure_ascii=False),subtotal,taxa,subtotal+(taxa or 0),nome,conv['telefone'],
        tem_alcool,bool(c['maioridade_confirmada']),agendado,origem,canal,forma['codigo'],forma['rotulo'],c['troco_para'],
        area['bairro'],area['cep'],area['origem'],area['origem']=='frete_gratis')
    await conn.execute("UPDATE carrinhos SET status='fechado',atualizado_em=now() WHERE tenant_id=$1 AND id=$2",tenant,c['id'])
    await registrar_evento(conn,tenant,p['id'],None,'aguardando_loja','agente','agente')
    return p


async def da_conversa(conn,tenant,conversa,numero):
    """Pedido só é visto e mexido pela conversa dona dele (SEG06)."""
    p=await conn.fetchrow('SELECT * FROM pedidos WHERE tenant_id=$1 AND numero=$2 AND conversation_id=$3',tenant,numero,conversa)
    if not p: raise Recusa('pedido_inexistente',404)
    return p


async def cancelar(conn,tenant,conversa,numero,motivo):
    """Cancelamento pedido pelo cliente: só o dono, e só enquanto a unidade não decidiu (SEG06)."""
    await travar(conn,tenant,conversa)
    p=await conn.fetchrow('SELECT * FROM pedidos WHERE tenant_id=$1 AND numero=$2 AND conversation_id=$3 FOR UPDATE',tenant,numero,conversa)
    if not p: raise Recusa('pedido_inexistente',404)
    if p['status']=='cancelado': return p
    if p['status']!='aguardando_loja': raise Recusa('pedido_nao_cancelavel',409)
    motivo=(motivo or '').strip()[:300] or None
    p=await conn.fetchrow("""UPDATE pedidos SET status='cancelado',motivo=$3,decidido_em=now(),decidido_por='cliente',atualizado_em=now()
        WHERE tenant_id=$1 AND id=$2 RETURNING *""",tenant,p['id'],motivo)
    # Cancelamento pedido pelo próprio cliente: entra na linha do tempo, sem aviso (o agente responde na hora).
    await registrar_evento(conn,tenant,p['id'],'aguardando_loja','cancelado','cliente','cliente',motivo)
    return p


class JaDecidido(Exception):
    """A unidade (ou o cliente) já decidiu este pedido: quem chega depois não muda nada."""
    def __init__(self,pedido): self.pedido=pedido


async def decidir(conn,tenant,numero,novo,motivo,por,origem='painel',previsao_min=None):
    """Confirmação ou recusa pela unidade (painel ou WhatsApp). O primeiro que decide vale.
    Devolve (pedido, id do evento); JaDecidido se não estava mais aguardando; None se não existe."""
    assert novo in ('confirmado','recusado')
    try: return await transicionar(conn,tenant,numero,novo,por,origem,motivo,previsao_min)
    except TransicaoInvalida as t: raise JaDecidido(t.pedido)


# "OK 48213" confirma; "NÃO 48213 motivo" recusa. Só vale vindo do WhatsApp de pedidos da unidade do pedido.
import re as _re
_OK=_re.compile(r'^\s*(?:ok|okay|sim|confirm[ao](?:do)?)\s*[#nº°]*\s*(\d{4,12})\s*[.!]*\s*$',_re.I)
_NAO=_re.compile(r'^\s*(?:n[aã]o|nao|recus[ao](?:do)?)\s*[#nº°]*\s*(\d{4,12})\b[\s:,.\-–—]*(.*)$',_re.I|_re.S)


def comando_da_unidade(texto):
    """('confirmado'|'recusado', numero, motivo) ou None se o texto não é um comando."""
    t=str(texto or '')
    m=_OK.match(t)
    if m: return 'confirmado',m.group(1),None
    m=_NAO.match(t)
    if m: return 'recusado',m.group(1),' '.join(m.group(2).split())[:300] or None
    return None


async def numero_interno(conn,tenant,telefone):
    """O telefone é WhatsApp de pedidos de alguma unidade ou o número de alerta da empresa?
    Esses números nunca são tratados como cliente. Devolve os códigos das unidades (lista vazia = só alerta) ou None."""
    if not telefone or not str(telefone).isdigit(): return None
    unidades=[r[0] for r in await conn.fetch('SELECT codigo FROM tenant_unidades WHERE tenant_id=$1 AND ativo AND whatsapp_pedidos=$2',tenant,telefone)]
    if unidades: return unidades
    return [] if await conn.fetchval('SELECT whatsapp_alerta_transbordo=$2 FROM tenants WHERE id=$1',tenant,telefone) else None


# Por quanto tempo o pacote continua avisando que o carrinho expirou (até o cliente montar outro).
AVISO_DE_EXPIRADO_H=24


async def expirar(conn,tenant,conversa,horas):
    """Arquiva o carrinho aberto da conversa se está sem alteração há mais de `horas`. Devolve o id arquivado ou None.
    Os itens ficam guardados (o pacote os informa em itens_expirados). Pedido já fechado não é carrinho aberto: não é afetado."""
    if not horas: return None
    await travar(conn,tenant,conversa)
    return await conn.fetchval("""UPDATE carrinhos SET status='expirado',expirado_em=now() WHERE tenant_id=$1 AND conversation_id=$2
        AND status='aberto' AND atualizado_em<now()-make_interval(hours=>$3) RETURNING id""",tenant,conversa,int(horas))


async def expirado(conn,tenant,conversa):
    """Itens do carrinho que expirou, enquanto ele for o carrinho mais recente da conversa (nada novo montado) e o aviso valer."""
    c=await conn.fetchrow("SELECT id,status,expirado_em FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1",tenant,conversa)
    if not c or c['status']!='expirado' or not c['expirado_em']: return None
    if await conn.fetchval("SELECT $1::timestamptz<now()-make_interval(hours=>$2)",c['expirado_em'],AVISO_DE_EXPIRADO_H): return None
    return [{'nome':l['nome'],'quantidade':l['quantidade']} for l in await linhas(conn,tenant,c['id'])]


async def contexto(repo,tenant,conversa):
    """Bloco contexto_modulos.pedidos do pacote ao agente: carrinho aberto, carrinho que expirou e o último pedido da conversa."""
    conn=repo.db   # só leituras: serve conexão ou pool
    carrinho=await retrato(conn,tenant,conversa)
    # ENC1-a: carrinho guardado de um atendimento anterior (o agente não oferece sozinho; usa se o cliente quiser continuar).
    carrinho['de_atendimento_anterior']=bool(carrinho['itens']) and bool(await conn.fetchval("""SELECT k.atualizado_em<a.aberto_em FROM carrinhos k
        JOIN conversas c ON c.tenant_id=k.tenant_id AND c.conversation_id=k.conversation_id
        JOIN atendimentos a ON a.tenant_id=c.tenant_id AND a.id=c.atendimento_id
        WHERE k.tenant_id=$1 AND k.conversation_id=$2 AND k.status='aberto'""",tenant,conversa))
    if carrinho['atualizado_em']:
        # No fuso da empresa, como as demais datas do pacote.
        from zoneinfo import ZoneInfo
        from datetime import datetime
        fuso=await conn.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant)
        carrinho['atualizado_em']=datetime.fromisoformat(carrinho['atualizado_em']).astimezone(ZoneInfo(fuso)).isoformat(timespec='seconds')
    antigos=await expirado(conn,tenant,conversa)
    p=await conn.fetchrow('SELECT numero,status,total,criado_em FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1',tenant,conversa)
    recentes=await ultimos(conn,tenant,conversa) if p else []
    # Cardápio interativo: link pronto para o agente mandar quando o cliente pedir o cardápio (nulo sem catálogo ou sem endereço público).
    from .cardapio import garantir_link
    link=None
    if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM catalogo_itens WHERE tenant_id=$1 AND ativo LIMIT 1)',tenant):
        link,_=await garantir_link(conn,tenant,conversa)
    return {'carrinho':carrinho,'link_cardapio':link,
            # true = o carrinho antigo foi arquivado por tempo; itens_expirados diz o que havia, para o agente oferecer refazer.
            'carrinho_expirado':antigos is not None,'itens_expirados':antigos or [],
            'ultimo_pedido':{'numero':p['numero'],'status':p['status'],'status_descricao':DESCRICAO[p['status']],'total':dinheiro(p['total']),
            'criado_em':p['criado_em'].isoformat(),'atualizado_em':recentes[0]['atualizado_em']} if p else None,
            # Até 3, do mais recente para o mais antigo: para responder "cadê meu pedido?" sem pedir o número (PED1).
            'ultimos_pedidos':recentes}
