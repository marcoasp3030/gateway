"""PED-BI (docs/ESPEC_PED_BI.md): visão de vendas da empresa para gestor e acima.

Venda é pedido aceito pela loja (confirmado em diante); aguardando, recusado e cancelado aparecem à parte. Só origem real por
padrão (teste, piloto e simulado ficam de fora). Datas no fuso de São Paulo. Pedido anonimizado (canal excluído) entra nos
totais e fica fora do ranking de clientes. O ranking traz nome e telefone completo: decisão do Marco (04/11/2026), só gestor."""
from datetime import date,datetime,time,timedelta
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from modulos import habilitado
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from . import servico

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
FUSO=ZoneInfo('America/Sao_Paulo')
MAX_DIAS=366
VENDA=('confirmado','em_preparo','pronto_retirada','saiu_entrega','concluido')
FORA=('aguardando_loja','recusado','cancelado')
# Cliente: o telefone gravado no pedido; sem ele (canal api), o contato da conversa. Pedido anonimizado não tem nenhum dos dois.
CLIENTE="coalesce(nullif(p.cliente_telefone,''),'c:'||v.contato_id)"
def de_pedidos(juntar=''):
    """FROM e filtro comum: empresa ($1), unidade ($4) e origens ($5). O período ($2, $3) vem à parte."""
    return f"""FROM pedidos p LEFT JOIN conversas v ON v.tenant_id=p.tenant_id AND v.conversation_id=p.conversation_id {juntar}
    WHERE p.tenant_id=$1 AND ($4::text IS NULL OR p.unidade=$4) AND p.origem=ANY($5::text[])"""


DE_PEDIDOS=de_pedidos()
NO_PERIODO=' AND p.criado_em>=$2 AND p.criado_em<$3'
E_VENDA=f" AND p.status IN {VENDA}"
LIMITE_CLIENTES=20
LIMITE_PRODUTOS=500


def _limites(de,ate):
    return datetime.combine(de,time(),FUSO),datetime.combine(ate+timedelta(days=1),time(),FUSO)


def _d(v): return round(float(v or 0),2)


def _variacao(atual,anterior):
    return None if not anterior else round((float(atual)-float(anterior))/float(anterior)*100,1)


def _minutos(v): return None if v is None else round(float(v),1)


def _e164(t):
    d=''.join(c for c in str(t or '') if c.isdigit())
    return '+'+d if 10<=len(d)<=15 else None


def _baldes(de,ate,agrupar):
    """Início de cada balde da evolução, do primeiro ao último, para o gráfico não ter buracos."""
    if agrupar=='dia': inicio=de
    elif agrupar=='semana': inicio=de-timedelta(days=de.weekday())
    else: inicio=de.replace(day=1)
    baldes=[]
    while inicio<=ate:
        baldes.append(inicio)
        if agrupar=='dia': inicio+=timedelta(days=1)
        elif agrupar=='semana': inicio+=timedelta(days=7)
        else: inicio=(inicio.replace(day=28)+timedelta(days=4)).replace(day=1)
    return baldes


async def _resumo(db,args):
    r=await db.fetchrow(f"""WITH base AS (SELECT p.*,{CLIENTE} AS cliente {DE_PEDIDOS}{NO_PERIODO}),
        primeira AS (SELECT {CLIENTE} AS cliente,min(p.criado_em) AS em {DE_PEDIDOS}{E_VENDA} AND p.criado_em<$3 GROUP BY 1)
        SELECT count(*) FILTER (WHERE status IN {VENDA}) AS pedidos,
               sum(total) FILTER (WHERE status IN {VENDA}) AS faturamento,
               sum(coalesce(subtotal,total)) FILTER (WHERE status IN {VENDA}) AS produtos,
               sum(taxa_entrega) FILTER (WHERE status IN {VENDA}) AS taxas,
               count(*) FILTER (WHERE status IN {VENDA} AND frete_gratis) AS frete_gratis,
               count(*) FILTER (WHERE status IN {VENDA} AND modalidade='delivery') AS delivery,
               sum(total) FILTER (WHERE status IN {VENDA} AND modalidade='delivery') AS delivery_valor,
               count(*) FILTER (WHERE status IN {VENDA} AND modalidade='retirada') AS retirada,
               sum(total) FILTER (WHERE status IN {VENDA} AND modalidade='retirada') AS retirada_valor,
               count(*) FILTER (WHERE status IN {VENDA} AND agendado_para IS NOT NULL) AS agendados,
               count(DISTINCT cliente) FILTER (WHERE status IN {VENDA}) AS clientes,
               (SELECT count(*) FROM primeira f WHERE f.em>=$2 AND f.cliente IS NOT NULL) AS clientes_novos,
               (SELECT coalesce(sum((i->>'quantidade')::int),0) FROM base b,jsonb_array_elements(b.itens) i WHERE b.status IN {VENDA}) AS itens,
               count(*) AS recebidos,
               count(*) FILTER (WHERE status='aguardando_loja') AS aguardando_loja,sum(total) FILTER (WHERE status='aguardando_loja') AS aguardando_loja_valor,
               count(*) FILTER (WHERE status='recusado') AS recusado,sum(total) FILTER (WHERE status='recusado') AS recusado_valor,
               count(*) FILTER (WHERE status='cancelado') AS cancelado,sum(total) FILTER (WHERE status='cancelado') AS cancelado_valor
        FROM base""",*args)
    n=r['pedidos'];identificados=r['clientes']
    return {'faturamento':_d(r['faturamento']),'pedidos':n,'ticket_medio':_d(r['faturamento']/n) if n else 0.0,
            'produtos':_d(r['produtos']),'taxas_entrega':_d(r['taxas']),'frete_gratis':r['frete_gratis'],'itens_vendidos':r['itens'],
            'clientes':identificados,'clientes_novos':r['clientes_novos'],'clientes_recorrentes':max(identificados-r['clientes_novos'],0),
            'agendados':r['agendados'],
            'modalidade':{'delivery':{'pedidos':r['delivery'],'faturamento':_d(r['delivery_valor'])},
                          'retirada':{'pedidos':r['retirada'],'faturamento':_d(r['retirada_valor'])}},
            'recebidos':r['recebidos'],
            'fora_da_venda':{s:{'pedidos':r[s],'valor':_d(r[s+'_valor'])} for s in FORA},
            # Recusados e cancelados sobre tudo o que chegou e já foi decidido (aguardando ainda não conta).
            'perda_pct':round((r['recusado']+r['cancelado'])/(r['recebidos']-r['aguardando_loja'])*100,1) if r['recebidos']-r['aguardando_loja'] else None}


async def _produtos(db,args):
    linhas=await db.fetch(f"""SELECT coalesce(i->>'item_id','n:'||(i->>'nome')) AS chave,
            (array_agg(i->>'item_id' ORDER BY p.id DESC))[1] AS item_id,(array_agg(i->>'codigo' ORDER BY p.id DESC))[1] AS codigo,
            (array_agg(i->>'nome' ORDER BY p.id DESC))[1] AS nome,(array_agg(i->>'categoria' ORDER BY p.id DESC))[1] AS categoria,
            sum((i->>'quantidade')::int) AS quantidade,sum((i->>'subtotal')::numeric) AS faturamento,count(DISTINCT p.id) AS pedidos
        {de_pedidos("CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(p.itens)='array' THEN p.itens ELSE '[]'::jsonb END) i")}{NO_PERIODO}{E_VENDA}
        GROUP BY 1 ORDER BY 7 DESC NULLS LAST,6 DESC""",*args)
    return linhas


async def _clientes(db,args,limite):
    return await db.fetch(f"""WITH agg AS (SELECT {CLIENTE} AS chave,count(*) AS pedidos,sum(p.total) AS faturamento,
            min(p.criado_em) AS primeiro_pedido_em,max(p.criado_em) AS ultimo_pedido_em,
            (array_agg(p.cliente_nome ORDER BY p.id DESC) FILTER (WHERE nullif(p.cliente_nome,'') IS NOT NULL))[1] AS nome,
            (array_agg(p.cliente_telefone ORDER BY p.id DESC) FILTER (WHERE nullif(p.cliente_telefone,'') IS NOT NULL))[1] AS telefone
        {DE_PEDIDOS}{NO_PERIODO}{E_VENDA} GROUP BY 1),
        r AS (SELECT *,row_number() OVER (ORDER BY faturamento DESC,pedidos DESC,chave) AS por_valor,
                       row_number() OVER (ORDER BY pedidos DESC,faturamento DESC,chave) AS por_frequencia FROM agg WHERE chave IS NOT NULL)
        SELECT * FROM r WHERE por_valor<=$6 OR por_frequencia<=$6""",*args,limite)


def _cliente(r,extra=None):
    return {'nome':r['nome'],'telefone':_e164(r['telefone']),'pedidos':r['pedidos'],'faturamento':_d(r['faturamento']),
            'ticket_medio':_d(r['faturamento']/r['pedidos']),'primeiro_pedido_em':r['primeiro_pedido_em'],'ultimo_pedido_em':r['ultimo_pedido_em'],**(extra or {})}


IFOOD_VENDA=('CONFIRMED','SEPARATION_STARTED','SEPARATION_ENDED','READY_TO_PICKUP','DISPATCHED','CONCLUDED')
DE_IFOOD="""FROM ifood_pedidos f LEFT JOIN ifood_lojas l ON l.merchant_id=f.merchant_id
    WHERE f.tenant_id=$1 AND f.criado_em>=$2 AND f.criado_em<$3 AND ($4::text IS NULL OR l.unidade=$4) AND ($5 OR NOT f.teste)"""


async def _ifood(db,tenant_id,unidade,origens,inicio,fim,ant_inicio,ant_fim,de,ate,agrupar):
    """IFOOD1-b1 (decisão 4 do Marco): o iFood na Visão de vendas, para comparar os canais. Pedido de teste do iFood só entra com
    origem=teste. None quando a empresa não tem loja do iFood ligada."""
    if not await db.fetchval('SELECT EXISTS(SELECT 1 FROM ifood_lojas WHERE tenant_id=$1)',tenant_id): return None
    teste='teste' in origens
    async def resumo(a,b):
        r=await db.fetchrow(f"""SELECT count(*) FILTER (WHERE f.status IN {IFOOD_VENDA}) AS pedidos,sum(f.total) FILTER (WHERE f.status IN {IFOOD_VENDA}) AS faturamento,
            count(*) FILTER (WHERE f.status='CANCELLED') AS cancelados,count(*) FILTER (WHERE f.status='PLACED') AS aguardando,count(*) AS recebidos,
            count(DISTINCT f.cliente_id) FILTER (WHERE f.status IN {IFOOD_VENDA}) AS clientes {DE_IFOOD}""",tenant_id,a,b,unidade,teste)
        n=r['pedidos']
        return {'pedidos':n,'faturamento':_d(r['faturamento']),'ticket_medio':_d(r['faturamento']/n) if n else 0.0,'clientes':r['clientes'],
                'cancelados':r['cancelados'],'aguardando':r['aguardando'],'recebidos':r['recebidos']}
    atual=await resumo(inicio,fim);anterior=await resumo(ant_inicio,ant_fim)
    serie={r['balde']:r for r in await db.fetch(f"""SELECT date_trunc('{ {'dia':'day','semana':'week','mes':'month'}[agrupar] }',f.criado_em AT TIME ZONE 'America/Sao_Paulo')::date AS balde,
            count(*) AS pedidos,sum(f.total) AS faturamento {DE_IFOOD} AND f.status IN {IFOOD_VENDA} GROUP BY 1""",tenant_id,inicio,fim,unidade,teste)}
    produtos=[{'nome':r['nome'],'quantidade':int(r['quantidade'] or 0),'faturamento':_d(r['faturamento'])} for r in await db.fetch(f"""
        SELECT i->>'nome' AS nome,sum((i->>'quantidade')::numeric) AS quantidade,sum((i->>'total')::numeric) AS faturamento
        {DE_IFOOD.replace('WHERE f.tenant_id','CROSS JOIN LATERAL jsonb_array_elements(f.itens) i WHERE f.tenant_id')} AND f.status IN {IFOOD_VENDA}
        AND jsonb_typeof(i->'quantidade')='number' GROUP BY 1 ORDER BY 3 DESC NULLS LAST,1 LIMIT 20""",tenant_id,inicio,fim,unidade,teste)]
    return {'resumo':atual,'anterior':anterior,
            'variacao_pct':{k:_variacao(atual[k],anterior[k]) for k in ('faturamento','pedidos','ticket_medio')},
            'evolucao':[{'inicio':b,'pedidos':serie[b]['pedidos'] if b in serie else 0,'faturamento':_d(serie[b]['faturamento']) if b in serie else 0.0}
                        for b in _baldes(de,ate,agrupar)],
            'produtos':produtos}


@router.get('/tenants/{tenant_id}/pedidos/indicadores')
async def indicadores(request:Request,tenant_id:str,de:date|None=None,ate:date|None=None,unidade:str|None=None,origem:str|None=None,
                      agrupar:Literal['dia','semana','mes']|None=None,sumido_dias:int=Query(30,ge=7,le=365)):
    """Resumo, evolução, horários, produtos, clientes, pagamento, bairros, operação e unidades do período (datas locais, inclusivas).
    Padrão: os últimos 30 dias até hoje, comparados com os 30 dias anteriores."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    await habilitado(Repositorio(db),tenant_id,'pedidos')
    hoje=datetime.now(FUSO).date()
    ate=ate or hoje;de=de or ate-timedelta(days=29)
    if de>ate: raise HTTPException(400,{'campo':'de','motivo':'depois_do_fim'})
    dias=(ate-de).days+1
    if dias>MAX_DIAS: raise HTTPException(400,{'campo':'periodo','motivo':'maximo_366_dias'})
    origens=[o for o in (origem or 'real').split(',') if o]
    if not origens or any(o not in servico.ORIGENS for o in origens): raise HTTPException(400,{'campo':'origem','motivo':'opcao'})
    unidade=unidade or None
    agrupar=agrupar or ('dia' if dias<=62 else 'semana' if dias<=190 else 'mes')
    inicio,fim=_limites(de,ate)
    ant_ate=de-timedelta(days=1);ant_de=ant_ate-timedelta(days=dias-1)
    ant_inicio,ant_fim=_limites(ant_de,ant_ate)
    args=(tenant_id,inicio,fim,unidade,origens);ant=(tenant_id,ant_inicio,ant_fim,unidade,origens)

    resumo=await _resumo(db,args);anterior=await _resumo(db,ant)
    variacao={k:_variacao(resumo[k],anterior[k]) for k in ('faturamento','pedidos','ticket_medio','itens_vendidos','clientes','clientes_novos')}

    serie={r['balde']:r for r in await db.fetch(f"""SELECT date_trunc('{ {'dia':'day','semana':'week','mes':'month'}[agrupar] }',p.criado_em AT TIME ZONE 'America/Sao_Paulo')::date AS balde,
            count(*) AS pedidos,sum(p.total) AS faturamento {DE_PEDIDOS}{NO_PERIODO}{E_VENDA} GROUP BY 1""",*args)}
    evolucao=[{'inicio':b,'pedidos':serie[b]['pedidos'] if b in serie else 0,'faturamento':_d(serie[b]['faturamento']) if b in serie else 0.0}
              for b in _baldes(de,ate,agrupar)]

    horarios=[{'dia_semana':int(r['dow']),'hora':int(r['hora']),'pedidos':r['pedidos'],'faturamento':_d(r['faturamento'])} for r in await db.fetch(f"""
        SELECT extract(dow FROM p.criado_em AT TIME ZONE 'America/Sao_Paulo') AS dow,extract(hour FROM p.criado_em AT TIME ZONE 'America/Sao_Paulo') AS hora,
            count(*) AS pedidos,sum(p.total) AS faturamento {DE_PEDIDOS}{NO_PERIODO}{E_VENDA} GROUP BY 1,2 ORDER BY 1,2""",*args)]

    # Produtos: todos os vendidos, com a curva ABC (A: até 80% do faturamento acumulado; B: até 95%; C: o resto).
    vendidos=await _produtos(db,args)
    antes={r['chave']:r['quantidade'] for r in await _produtos(db,ant)}
    total_produtos=sum(float(r['faturamento'] or 0) for r in vendidos);acumulado=0.0;produtos=[];categorias={}
    for r in vendidos[:LIMITE_PRODUTOS]:
        fat=float(r['faturamento'] or 0);acumulado+=fat
        fracao=acumulado/total_produtos if total_produtos else 1
        q_ant=antes.get(r['chave'],0)
        produtos.append({'item_id':int(r['item_id']) if r['item_id'] and r['item_id'].isdigit() else None,'codigo':r['codigo'],'nome':r['nome'],
                         'categoria':r['categoria'],'quantidade':r['quantidade'],'faturamento':_d(fat),'pedidos':r['pedidos'],
                         'participacao_pct':round(fat/total_produtos*100,1) if total_produtos else 0.0,
                         'curva':'A' if fracao<=0.8 or not produtos else 'B' if fracao<=0.95 else 'C',
                         'quantidade_anterior':q_ant,'variacao_quantidade':_variacao(r['quantidade'],q_ant)})
        c=categorias.setdefault(r['categoria'] or 'Sem categoria',{'categoria':r['categoria'] or 'Sem categoria','quantidade':0,'faturamento':0.0})
        c['quantidade']+=r['quantidade'];c['faturamento']=_d(c['faturamento']+fat)
    ids=[p['item_id'] for p in produtos if p['item_id']]
    sem_venda_q=f"""FROM catalogo_itens WHERE tenant_id=$1 AND ativo AND arquivado_em IS NULL AND tipo='item'
        AND ($2::text IS NULL OR unidade=$2) AND id<>ALL($3::int[])"""
    sem_venda=[dict(r) for r in await db.fetch(f'SELECT id AS item_id,codigo,nome,categoria,unidade {sem_venda_q} ORDER BY categoria,nome,id LIMIT 100',
                                                tenant_id,unidade,ids)]
    sem_venda_total=await db.fetchval(f'SELECT count(*) {sem_venda_q}',tenant_id,unidade,ids)

    linhas=await _clientes(db,args,LIMITE_CLIENTES)
    por_valor=[_cliente(r) for r in sorted((r for r in linhas if r['por_valor']<=LIMITE_CLIENTES),key=lambda r:r['por_valor'])]
    por_frequencia=[_cliente(r) for r in sorted((r for r in linhas if r['por_frequencia']<=LIMITE_CLIENTES),key=lambda r:r['por_frequencia'])]
    # Sumidos: compraram 2 vezes ou mais (em qualquer data até o fim do período) e não compram há sumido_dias.
    corte=fim-timedelta(days=sumido_dias)
    sumidos_q=f"""WITH agg AS (SELECT {CLIENTE} AS chave,count(*) AS pedidos,sum(p.total) AS faturamento,
            min(p.criado_em) AS primeiro_pedido_em,max(p.criado_em) AS ultimo_pedido_em,
            (array_agg(p.cliente_nome ORDER BY p.id DESC) FILTER (WHERE nullif(p.cliente_nome,'') IS NOT NULL))[1] AS nome,
            (array_agg(p.cliente_telefone ORDER BY p.id DESC) FILTER (WHERE nullif(p.cliente_telefone,'') IS NOT NULL))[1] AS telefone
        {DE_PEDIDOS}{E_VENDA} AND p.criado_em<$3 AND $2::timestamptz IS NOT NULL GROUP BY 1)
        SELECT * FROM agg WHERE chave IS NOT NULL AND pedidos>=2 AND ultimo_pedido_em<$6"""
    sumidos=[_cliente(r,{'dias_sem_comprar':(fim-r['ultimo_pedido_em']).days}) for r in
             await db.fetch(sumidos_q+' ORDER BY faturamento DESC,chave LIMIT $7',*args,corte,LIMITE_CLIENTES)]
    sumidos_total=await db.fetchval(f'SELECT count(*) FROM ({sumidos_q}) s',*args,corte)

    pagamentos=[{'forma':r['forma'],'rotulo':r['rotulo'],'pedidos':r['pedidos'],'faturamento':_d(r['faturamento']),
                 'participacao_pct':round(r['pedidos']/resumo['pedidos']*100,1) if resumo['pedidos'] else 0.0} for r in await db.fetch(f"""
        SELECT coalesce(p.forma_pagamento,'nao_informado') AS forma,coalesce(max(p.pagamento_rotulo),CASE WHEN p.forma_pagamento IS NULL THEN 'Não informado' END) AS rotulo,
            count(*) AS pedidos,sum(p.total) AS faturamento {DE_PEDIDOS}{NO_PERIODO}{E_VENDA} GROUP BY p.forma_pagamento ORDER BY 3 DESC,1""",*args)]

    bairros=[{'bairro':r['bairro'],'pedidos':r['pedidos'],'faturamento':_d(r['faturamento']),'taxa_media':_d(r['taxa'])} for r in await db.fetch(f"""
        SELECT mode() WITHIN GROUP (ORDER BY trim(p.bairro)) AS bairro,count(*) AS pedidos,sum(p.total) AS faturamento,avg(p.taxa_entrega) AS taxa
        {DE_PEDIDOS}{NO_PERIODO}{E_VENDA} AND p.modalidade='delivery' AND nullif(trim(p.bairro),'') IS NOT NULL
        GROUP BY lower(trim(p.bairro)) ORDER BY 2 DESC,3 DESC LIMIT 20""",*args)]

    # Operação: minutos de cada etapa (mediana e 90% dos pedidos); agendados ficam fora de preparo e total.
    o=await db.fetchrow(f"""WITH ped AS (SELECT p.id,p.criado_em,p.agendado_para {DE_PEDIDOS}{NO_PERIODO}{E_VENDA}),
        ev AS (SELECT e.pedido_id,min(e.criado_em) FILTER (WHERE e.para='confirmado') AS confirmado,
                      min(e.criado_em) FILTER (WHERE e.para IN ('pronto_retirada','saiu_entrega')) AS pronto,
                      min(e.criado_em) FILTER (WHERE e.para='concluido') AS concluido
               FROM pedido_eventos e JOIN ped ON ped.id=e.pedido_id WHERE e.tenant_id=$1 GROUP BY 1),
        m AS (SELECT extract(epoch FROM ev.confirmado-ped.criado_em)/60 AS aceite,
                     CASE WHEN ped.agendado_para IS NULL THEN extract(epoch FROM ev.pronto-ev.confirmado)/60 END AS preparo,
                     CASE WHEN ped.agendado_para IS NULL THEN extract(epoch FROM ev.concluido-ped.criado_em)/60 END AS total
              FROM ped JOIN ev ON ev.pedido_id=ped.id)
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY aceite) AS aceite_med,percentile_cont(0.9) WITHIN GROUP (ORDER BY aceite) AS aceite_p90,count(aceite) AS aceite_n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY preparo) AS preparo_med,percentile_cont(0.9) WITHIN GROUP (ORDER BY preparo) AS preparo_p90,count(preparo) AS preparo_n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY total) AS total_med,percentile_cont(0.9) WITHIN GROUP (ORDER BY total) AS total_p90,count(total) AS total_n
        FROM m""",*args)
    motivos=[dict(r) for r in await db.fetch(f"""SELECT p.status,coalesce(nullif(trim(p.motivo),''),'(sem motivo)') AS motivo,count(*) AS pedidos
        {DE_PEDIDOS}{NO_PERIODO} AND p.status IN ('recusado','cancelado') GROUP BY 1,2
        ORDER BY 3 DESC,1,2 LIMIT 10""",*args)]
    operacao={etapa:{'mediana_min':_minutos(o[etapa+'_med']),'p90_min':_minutos(o[etapa+'_p90']),'pedidos':o[etapa+'_n']}
              for etapa in ('aceite','preparo','total')}

    unidades=[{'unidade':r['unidade'],'nome':r['nome'],'pedidos':r['pedidos'],'faturamento':_d(r['faturamento']),
               'ticket_medio':_d(r['faturamento']/r['pedidos']) if r['pedidos'] else 0.0,'recebidos':r['recebidos']} for r in await db.fetch(f"""
        SELECT p.unidade,coalesce(max(u.nome),p.unidade) AS nome,count(*) FILTER (WHERE p.status IN {VENDA}) AS pedidos,
            coalesce(sum(p.total) FILTER (WHERE p.status IN {VENDA}),0) AS faturamento,count(*) AS recebidos
        {de_pedidos('LEFT JOIN tenant_unidades u ON u.tenant_id=p.tenant_id AND u.codigo=p.unidade')}{NO_PERIODO}
        GROUP BY p.unidade ORDER BY 4 DESC,1""",*args)]

    return {'periodo':{'de':de,'ate':ate,'dias':dias,'agrupar':agrupar,'fuso':'America/Sao_Paulo','anterior':{'de':ant_de,'ate':ant_ate}},
            'filtros':{'unidade':unidade,'origem':origens,'sumido_dias':sumido_dias},
            'resumo':resumo,'anterior':anterior,'variacao_pct':variacao,
            'evolucao':evolucao,'horarios':horarios,
            'produtos':{'itens':produtos,'total':len(vendidos),'curva':{c:sum(1 for p in produtos if p['curva']==c) for c in 'ABC'},
                        'categorias':sorted(categorias.values(),key=lambda c:-c['faturamento']),
                        'sem_venda':{'itens':sem_venda,'total':sem_venda_total}},
            'clientes':{'por_valor':por_valor,'por_frequencia':por_frequencia,'sumidos':{'itens':sumidos,'total':sumidos_total,'dias':sumido_dias}},
            'pagamentos':pagamentos,'bairros':bairros,'operacao':{**operacao,'motivos_perda':motivos},'unidades':unidades,
            'ifood':await _ifood(db,tenant_id,unidade,origens,inicio,fim,ant_inicio,ant_fim,de,ate,agrupar)}
