"""Carrinho e pré-pedido (Fase 4). Regras de negócio validadas aqui, nunca pelo agente:
preço sempre do catálogo, carrinho de uma unidade só, item só entra se for vendável,
bebida alcoólica só fecha com maioridade confirmada (SEG06: o que o agente pedir não muda isso)."""
import json,secrets
from decimal import Decimal

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
}


class Recusa(Exception):
    def __init__(self,motivo,status=422):
        self.motivo=motivo;self.status=status;self.campo,self.mensagem=RECUSAS[motivo]


def dinheiro(valor):
    return float(valor) if valor is not None else None


async def travar(conn,tenant,conversa):
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','carrinho:'+tenant+':'+conversa)


async def unidade_da_empresa(conn,tenant,codigo):
    if not codigo: return None
    return await conn.fetchrow('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2 AND ativo',tenant,codigo)


async def aberto(conn,tenant,conversa,criar=False):
    c=await conn.fetchrow("SELECT * FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2 AND status='aberto'",tenant,conversa)
    if c is None and criar:
        c=await conn.fetchrow('INSERT INTO carrinhos(tenant_id,conversation_id) VALUES ($1,$2) RETURNING *',tenant,conversa)
    return c


async def linhas(conn,tenant,carrinho_id):
    return await conn.fetch('''SELECT l.id,l.item_id,l.quantidade,l.preco_unitario,l.observacao,
        i.codigo,i.nome,i.categoria,i.unidade,i.alcoolico,i.ativo,i.preco AS preco_atual,i.delivery,i.tipo
        FROM carrinho_itens l JOIN catalogo_itens i ON i.tenant_id=l.tenant_id AND i.id=l.item_id
        WHERE l.tenant_id=$1 AND l.carrinho_id=$2 ORDER BY l.id''',tenant,carrinho_id)


async def retrato(conn,tenant,conversa):
    """Carrinho completo para o agente: itens, subtotal, taxa, total e o que falta para fechar."""
    c=await aberto(conn,tenant,conversa)
    if c is None:
        return {'conversation_id':conversa,'unidade':None,'modalidade':None,'endereco':None,'horario':None,'observacao':None,
                'maioridade_confirmada':False,'itens':[],'numero_itens':0,'subtotal':None,'taxa_entrega':None,'total':None,
                'pedido_minimo':None,'tem_alcoolico':False,'faltando':['itens','modalidade'],'pode_fechar':False}
    ls=await linhas(conn,tenant,c['id'])
    itens=[{'id':l['id'],'item_id':l['item_id'],'codigo':l['codigo'],'nome':l['nome'],'categoria':l['categoria'],
            'quantidade':l['quantidade'],'preco_unitario':dinheiro(l['preco_unitario']),
            'subtotal':dinheiro(l['preco_unitario']*l['quantidade']),'observacao':l['observacao'],'alcoolico':l['alcoolico']} for l in ls]
    subtotal=sum((l['preco_unitario']*l['quantidade'] for l in ls),Decimal('0')) if ls else None
    u=await unidade_da_empresa(conn,tenant,c['unidade'])
    # Taxa só entra quando é entrega e a unidade tem taxa cadastrada; senão fica nula (a unidade confirma no retorno).
    taxa=u['taxa_entrega'] if u and c['modalidade']=='delivery' else None
    tem_alcool=any(l['alcoolico'] for l in ls)
    faltando=[]
    if not ls: faltando.append('itens')
    if not c['modalidade']: faltando.append('modalidade')
    if c['modalidade']=='delivery' and not (c['endereco'] or '').strip(): faltando.append('endereco')
    if tem_alcool and not c['maioridade_confirmada']: faltando.append('maioridade_confirmada')
    return {'conversation_id':conversa,'unidade':c['unidade'],'modalidade':c['modalidade'],'endereco':c['endereco'],'horario':c['horario'],
            'observacao':c['observacao'],'maioridade_confirmada':c['maioridade_confirmada'],'itens':itens,
            'numero_itens':sum(l['quantidade'] for l in ls),'subtotal':dinheiro(subtotal),'taxa_entrega':dinheiro(taxa),
            'total':dinheiro(subtotal+(taxa or 0)) if subtotal is not None else None,
            'pedido_minimo':dinheiro(u['pedido_minimo']) if u else None,'tem_alcoolico':tem_alcool,
            'faltando':faltando,'pode_fechar':not faltando}


def vendavel(item):
    """Regra 1: só entra em carrinho item ativo, com preço e com delivery diferente de "nao"."""
    if item is None: return 'item_inexistente'
    if not item['ativo'] or item['tipo'] not in ('item','adicional'): return 'item_inativo'
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
    saida=[]
    for r in rows:
        d=json.loads(r['dados']) if isinstance(r['dados'],str) else (r['dados'] or {})
        saida.append({'skill':r['skill'],'item':d.get('item'),'quantidade':d.get('quantidade'),'resultado':r['resultado'],
                      **({'detalhe':d['detalhe']} if d.get('detalhe') else {})})
    return saida


async def adicionar(conn,tenant,conversa,item_id,quantidade,observacao):
    await travar(conn,tenant,conversa)
    item=await conn.fetchrow('SELECT * FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant,item_id)
    motivo=vendavel(item)
    if motivo: raise Recusa(motivo,404 if motivo=='item_inexistente' else 422)
    c=await aberto(conn,tenant,conversa,criar=True)
    # Regra 2: carrinho de uma unidade só. O primeiro item define a unidade.
    if c['unidade'] and c['unidade']!=item['unidade']: raise Recusa('unidade_diferente')
    if not c['unidade']: await conn.execute('UPDATE carrinhos SET unidade=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],item['unidade'])
    obs=(observacao or '').strip() or None
    igual=await conn.fetchrow('''SELECT id,quantidade FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2 AND item_id=$3
        AND observacao IS NOT DISTINCT FROM $4''',tenant,c['id'],item_id,obs)
    if igual:
        # Mesmo item com a mesma observação soma; o preço volta a ser o do catálogo de agora.
        await conn.execute('UPDATE carrinho_itens SET quantidade=$3,preco_unitario=$4 WHERE tenant_id=$1 AND id=$2',
                           tenant,igual['id'],min(MAX_QUANTIDADE,igual['quantidade']+quantidade),item['preco'])
    else:
        if await conn.fetchval('SELECT count(*) FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2',tenant,c['id'])>=MAX_LINHAS:
            raise Recusa('carrinho_cheio')
        # Preço copiado do catálogo: o que o agente disser não entra.
        await conn.execute('INSERT INTO carrinho_itens(tenant_id,carrinho_id,item_id,quantidade,preco_unitario,observacao) VALUES ($1,$2,$3,$4,$5,$6)',
                           tenant,c['id'],item_id,quantidade,item['preco'],obs)
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


async def alterar(conn,tenant,conversa,linha_id,quantidade=None,observacao=None,mudar_observacao=False):
    await travar(conn,tenant,conversa)
    c,l=await _linha(conn,tenant,conversa,linha_id)
    if quantidade==0:
        await conn.execute('DELETE FROM carrinho_itens WHERE tenant_id=$1 AND id=$2',tenant,l['id'])
    else:
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
        await _unidade_se_vazio(conn,tenant,c)


async def entrega(conn,tenant,conversa,dados):
    """Unidade, modalidade, endereço, horário, observação e maioridade. Só muda o que veio."""
    await travar(conn,tenant,conversa)
    c=await aberto(conn,tenant,conversa,criar=True)
    if 'unidade' in dados and dados['unidade'] and dados['unidade']!=c['unidade']:
        if not await unidade_da_empresa(conn,tenant,dados['unidade']): raise Recusa('unidade_inexistente',404)
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM carrinho_itens WHERE tenant_id=$1 AND carrinho_id=$2)',tenant,c['id']): raise Recusa('unidade_diferente')
        await conn.execute('UPDATE carrinhos SET unidade=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],dados['unidade'])
    if dados.get('modalidade')=='retirada':
        u=await unidade_da_empresa(conn,tenant,dados.get('unidade') or c['unidade'])
        if u and u['aceita_retirada'] is False: raise Recusa('retirada_indisponivel')
    for campo in ('modalidade','endereco','horario','observacao'):
        if campo in dados:
            valor=(dados[campo] or '').strip() or None if isinstance(dados[campo],str) or dados[campo] is None else dados[campo]
            await conn.execute(f'UPDATE carrinhos SET {campo}=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],valor)
    if dados.get('maioridade_confirmada') is not None:
        await conn.execute('UPDATE carrinhos SET maioridade_confirmada=$3 WHERE tenant_id=$1 AND id=$2',tenant,c['id'],bool(dados['maioridade_confirmada']))
    await tocar(conn,tenant,c['id'])


async def _numero(conn,tenant):
    """Número curto, só dígitos (fácil de ditar e de digitar em "OK <numero>"), único na empresa."""
    for digitos in (5,5,5,6,6,7,8):
        n=str(secrets.randbelow(9*10**(digitos-1))+10**(digitos-1))
        if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pedidos WHERE tenant_id=$1 AND numero=$2)',tenant,n): return n
    raise RuntimeError('Não foi possível gerar o número do pedido')


def retrato_pedido(p):
    itens=json.loads(p['itens']) if isinstance(p['itens'],str) else p['itens']
    return {'numero':p['numero'],'status':p['status'],'conversation_id':p['conversation_id'],'unidade':p['unidade'],'modalidade':p['modalidade'],
            'endereco':p['endereco'],'horario':p['horario'],'observacao':p['observacao'],'itens':itens,
            'subtotal':dinheiro(p['subtotal']),'taxa_entrega':dinheiro(p['taxa_entrega']),'total':dinheiro(p['total']),
            'tem_alcoolico':p['tem_alcoolico'],'motivo':p['motivo'],'cliente_nome':p['cliente_nome'],
            'criado_em':p['criado_em'],'decidido_em':p['decidido_em']}


async def fechar(conn,tenant,conversa):
    """Fecha o carrinho em pré-pedido (aguardando a unidade). Revalida tudo com o catálogo de agora."""
    await travar(conn,tenant,conversa)
    c=await aberto(conn,tenant,conversa)
    ls=await linhas(conn,tenant,c['id']) if c else []
    if not ls: raise Recusa('carrinho_vazio')
    mudou=False
    for l in ls:
        motivo=vendavel({'ativo':l['ativo'],'tipo':l['tipo'],'preco':l['preco_atual'],'delivery':l['delivery']})
        if motivo: raise Recusa(motivo)
        if l['unidade']!=c['unidade']: raise Recusa('unidade_diferente')
        if l['preco_atual']!=l['preco_unitario']:
            # Preço sempre do banco: atualiza o carrinho e devolve para o agente confirmar o novo total.
            await conn.execute('UPDATE carrinho_itens SET preco_unitario=$3 WHERE tenant_id=$1 AND id=$2',tenant,l['id'],l['preco_atual']);mudou=True
    if mudou: return None
    if not c['modalidade']: raise Recusa('falta_modalidade')
    if c['modalidade']=='delivery' and not (c['endereco'] or '').strip(): raise Recusa('falta_endereco')
    tem_alcool=any(l['alcoolico'] for l in ls)
    # Regra 3: bebida alcoólica só fecha com maioridade confirmada.
    if tem_alcool and not c['maioridade_confirmada']: raise Recusa('falta_maioridade')
    u=await unidade_da_empresa(conn,tenant,c['unidade'])
    if c['modalidade']=='retirada' and u and u['aceita_retirada'] is False: raise Recusa('retirada_indisponivel')
    subtotal=sum((l['preco_unitario']*l['quantidade'] for l in ls),Decimal('0'))
    if u and u['pedido_minimo'] is not None and subtotal<u['pedido_minimo']: raise Recusa('abaixo_do_minimo')
    taxa=u['taxa_entrega'] if u and c['modalidade']=='delivery' else None
    itens=[{'item_id':l['item_id'],'codigo':l['codigo'],'nome':l['nome'],'categoria':l['categoria'],'quantidade':l['quantidade'],
            'preco_unitario':dinheiro(l['preco_unitario']),'subtotal':dinheiro(l['preco_unitario']*l['quantidade']),
            'observacao':l['observacao'],'alcoolico':l['alcoolico']} for l in ls]
    conv=await conn.fetchrow('SELECT telefone,nome,contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',tenant,conversa)
    nome=conv['nome'] or (await conn.fetchval('SELECT nome FROM contatos WHERE tenant_id=$1 AND id=$2',tenant,conv['contato_id']) if conv['contato_id'] else None)
    p=await conn.fetchrow('''INSERT INTO pedidos(tenant_id,numero,conversation_id,carrinho_id,unidade,modalidade,endereco,horario,observacao,
        itens,subtotal,taxa_entrega,total,cliente_nome,cliente_telefone,tem_alcoolico,maioridade_confirmada)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13,$14,$15,$16,$17) RETURNING *''',
        tenant,await _numero(conn,tenant),conversa,c['id'],c['unidade'],c['modalidade'],c['endereco'] if c['modalidade']=='delivery' else None,
        c['horario'],c['observacao'],json.dumps(itens,ensure_ascii=False),subtotal,taxa,subtotal+(taxa or 0),nome,conv['telefone'],
        tem_alcool,bool(c['maioridade_confirmada']))
    await conn.execute("UPDATE carrinhos SET status='fechado',atualizado_em=now() WHERE tenant_id=$1 AND id=$2",tenant,c['id'])
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
    return await conn.fetchrow("""UPDATE pedidos SET status='cancelado',motivo=$3,decidido_em=now(),decidido_por='cliente',atualizado_em=now()
        WHERE tenant_id=$1 AND id=$2 RETURNING *""",tenant,p['id'],(motivo or '').strip()[:300] or None)


class JaDecidido(Exception):
    """A unidade (ou o cliente) já decidiu este pedido: quem chega depois não muda nada."""
    def __init__(self,pedido): self.pedido=pedido


async def decidir(conn,tenant,numero,novo,motivo,por):
    """Confirmação ou recusa pela unidade (painel ou WhatsApp). O primeiro que decide vale.
    Devolve o pedido atualizado; JaDecidido se não estava mais aguardando; None se não existe."""
    assert novo in ('confirmado','recusado')
    p=await conn.fetchrow('SELECT * FROM pedidos WHERE tenant_id=$1 AND numero=$2 FOR UPDATE',tenant,numero)
    if not p: return None
    if p['status']!='aguardando_loja': raise JaDecidido(p)
    p=await conn.fetchrow("""UPDATE pedidos SET status=$3,motivo=$4,decidido_em=now(),decidido_por=$5,atualizado_em=now()
        WHERE tenant_id=$1 AND id=$2 RETURNING *""",tenant,p['id'],novo,(motivo or '').strip()[:300] or None,por[:100])
    if novo=='recusado' and p['carrinho_id']:
        # Recusado: os itens voltam para o carrinho do cliente, se ele ainda não abriu outro.
        if not await aberto(conn,tenant,p['conversation_id']):
            await conn.execute("UPDATE carrinhos SET status='aberto',atualizado_em=now() WHERE tenant_id=$1 AND id=$2 AND status='fechado'",tenant,p['carrinho_id'])
    return p


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


async def contexto(repo,tenant,conversa):
    """Bloco contexto_modulos.pedidos do pacote ao agente: carrinho aberto e o último pedido da conversa."""
    conn=repo.db   # só leituras: serve conexão ou pool
    carrinho=await retrato(conn,tenant,conversa)
    p=await conn.fetchrow('SELECT numero,status,total,criado_em FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1',tenant,conversa)
    return {'carrinho':carrinho,'ultimo_pedido':{'numero':p['numero'],'status':p['status'],'total':dinheiro(p['total']),
            'criado_em':p['criado_em'].isoformat()} if p else None}
