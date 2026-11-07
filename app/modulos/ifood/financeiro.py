"""IFOOD1-b2 (docs/ESPEC_IFOOD1.md): financeiro do iFood pela API Financial Events (v3), só leitura.

Cada evento traz o valor (positivo = crédito, negativo = débito), se entra no repasse (hasTransferImpact), a data prevista de pagamento e
o pedido. Repasse líquido = soma dos valores com hasTransferImpact. A API não dá id ao evento: cada coleta substitui os eventos da loja
nas datas coletadas (janelas de 7 dias; o iFood aceita até 33). Primeira coleta: 90 dias; depois, os últimos 8 dias, porque eventos
chegam atrasados. Coleta sozinha quando o painel abre e a última passou de 6 h, ou pelo botão "Atualizar" (no máximo a cada 10 min)."""
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter,BackgroundTasks,Depends,Request
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from .cliente import Ifood,ErroIfood
from .rotas import _falha,_prefixo

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
FUSO=ZoneInfo('America/Sao_Paulo')
HISTORICO_DIAS=90
REFAZER_DIAS=8
JANELA_DIAS=7
MAX_PAGINAS=50
VELHA_H=6
INTERVALO_MIN=10


def _data(v):
    if not isinstance(v,str) or len(v)<10: return None
    try: return date.fromisoformat(v[:10])
    except ValueError: return None


def _num(v): return round(float(v),4) if isinstance(v,(int,float)) and not isinstance(v,bool) else None


def _t(v,n): return v[:n] if isinstance(v,str) and v else None


def _g(d,*caminho):
    for c in caminho:
        d=d.get(c) if isinstance(d,dict) else None
    return d


def evento(e):
    """Linha gravada a partir de um evento do iFood; None se faltar data ou valor."""
    if not isinstance(e,dict): return None
    valor=_num(_g(e,'amount','value'));quando=_data(e.get('dateTime'))
    if valor is None or quando is None or not isinstance(e.get('name'),str): return None
    return {'data':quando,'nome':e['name'][:80],'descricao':_t(e.get('description'),200),'gatilho':_t(e.get('trigger'),80),'produto':_t(e.get('product'),40),
            'competencia':_t(e.get('competence'),7),'ref_tipo':_t(_g(e,'reference','type'),40),'ref_id':_t(_g(e,'reference','id'),100),
            'ref_data':_data(_g(e,'reference','date')),'com_repasse':e.get('hasTransferImpact') is True,'valor':round(valor,2),
            'base':_num(_g(e,'billing','baseValue')),'taxa_pct':_num(_g(e,'billing','feePercentage')),
            'parcela':_t(_g(e,'billing','installments','reference'),20),'pagamento_previsto':_data(_g(e,'settlement','expectedDate')),
            'forma_pagamento':_t(_g(e,'payment','method'),40),'bandeira':_t(_g(e,'payment','brand'),40),'responsabilidade':_t(_g(e,'payment','liability'),20)}


def _pagina(d):
    """(itens, tem_proxima). A resposta é uma lista ou um objeto com a lista e hasNextPage; 204 (None) é vazio."""
    if d is None: return [],False
    if isinstance(d,list): return d,False
    if isinstance(d,dict):
        lista=next((v for v in d.values() if isinstance(v,list)),[])
        return lista,d.get('hasNextPage') is True
    raise ErroIfood('formato_inesperado')


async def _janela(cli,merchant,a,b):
    itens=[]
    for pagina in range(1,MAX_PAGINAS+1):
        d=await cli.chamar('GET',f'/financial/v3.0/merchants/{merchant}/financial-events',
                           params={'beginDate':a.isoformat(),'endDate':b.isoformat(),'page':pagina,'size':100})
        lista,mais=_pagina(d);itens.extend(lista)
        if not mais or not lista: break
    return [x for x in (evento(e) for e in itens) if x]


COLUNAS=('data','nome','descricao','gatilho','produto','competencia','ref_tipo','ref_id','ref_data','com_repasse','valor','base','taxa_pct',
         'parcela','pagamento_previsto','forma_pagamento','bandeira','responsabilidade')


async def coletar(estado,prefixo,tenant,merchant,forcar=False):
    """Coleta o financeiro de uma loja. None se deu certo; senão o motivo ('em_andamento', 'recente' ou o erro do iFood)."""
    db=estado.db
    await db.execute("INSERT INTO ifood_coletas(merchant_id,area,tenant_id) VALUES ($1,'financeiro',$2) ON CONFLICT DO NOTHING",merchant,tenant)
    # Uma coleta por loja de cada vez (a trava vence em 10 min, caso o processo caia no meio).
    c=await db.fetchrow(f"""UPDATE ifood_coletas SET iniciada_em=now() WHERE merchant_id=$1 AND area='financeiro'
        AND (iniciada_em IS NULL OR iniciada_em<now()-interval '{INTERVALO_MIN} minutes')
        AND ($2 OR sucesso_em IS NULL OR sucesso_em<now()-interval '{INTERVALO_MIN} minutes') RETURNING coletado_desde""",merchant,forcar)
    if not c: return 'em_andamento'
    hoje=datetime.now(FUSO).date()
    desde=hoje-timedelta(days=(REFAZER_DIAS if c['coletado_desde'] else HISTORICO_DIAS)-1)
    try:
        cli=await Ifood(estado.http,estado.redis,estado.cofre,prefixo).carregar(db)
        if not cli.configurado: raise ErroIfood('nao_configurado')
        a=desde
        while a<=hoje:
            b=min(a+timedelta(days=JANELA_DIAS-1),hoje)
            linhas=await _janela(cli,merchant,a,b)
            async with db.acquire() as conn,conn.transaction():
                await conn.execute('DELETE FROM ifood_fin_eventos WHERE merchant_id=$1 AND data BETWEEN $2 AND $3',merchant,a,b)
                if linhas:
                    await conn.executemany(f"""INSERT INTO ifood_fin_eventos(tenant_id,merchant_id,{','.join(COLUNAS)})
                        VALUES ($1,$2,{','.join(f'${i+3}' for i in range(len(COLUNAS)))})""",[(tenant,merchant,*(l[k] for k in COLUNAS)) for l in linhas])
            a=b+timedelta(days=1)
    except ErroIfood as e:
        await db.execute("UPDATE ifood_coletas SET iniciada_em=NULL,erro=$2 WHERE merchant_id=$1 AND area='financeiro'",merchant,e.motivo)
        return e.motivo
    await db.execute("""UPDATE ifood_coletas SET iniciada_em=NULL,erro=NULL,sucesso_em=now(),coletado_desde=LEAST(coalesce(coletado_desde,$2),$2)
        WHERE merchant_id=$1 AND area='financeiro'""",merchant,desde)
    return None


async def coletar_empresa(estado,prefixo,tenant,forcar=False):
    for m in await estado.db.fetch('SELECT merchant_id FROM ifood_lojas WHERE tenant_id=$1 AND ativo',tenant):
        await coletar(estado,prefixo,tenant,m['merchant_id'],forcar)


def _d(v): return round(float(v or 0),2)


@router.get('/tenants/{tenant_id}/ifood/financeiro')
async def financeiro(request:Request,tenant_id:str,tarefas:BackgroundTasks,de:date|None=None,ate:date|None=None,loja:str|None=None):
    """Resumo do período (padrão: 30 dias): créditos, débitos, repasse líquido, recebido direto pela loja, por tipo de evento, por dia,
    por forma de pagamento; e o que ainda vai ser pago (por data prevista). Coleta sozinha se a última passou de 6 h."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): return _falha(404,'NOT_FOUND')
    hoje=datetime.now(FUSO).date();ate=ate or hoje;de=de or ate-timedelta(days=29)
    if de>ate or (ate-de).days>366: return _falha(400,'formato','periodo')
    coletas=[dict(r) for r in await db.fetch("""SELECT l.merchant_id,l.nome,c.sucesso_em,c.erro,c.iniciada_em IS NOT NULL AS coletando,c.coletado_desde
        FROM ifood_lojas l LEFT JOIN ifood_coletas c ON c.merchant_id=l.merchant_id AND c.area='financeiro'
        WHERE l.tenant_id=$1 AND l.ativo ORDER BY l.merchant_id""",tenant_id)]
    agora=datetime.now(FUSO)
    if any(not c['coletando'] and (c['sucesso_em'] is None or agora-c['sucesso_em']>timedelta(hours=VELHA_H)) for c in coletas):
        tarefas.add_task(coletar_empresa,request.app.state,_prefixo(request),tenant_id)
    args=(tenant_id,de,ate,loja)
    filtro='WHERE tenant_id=$1 AND data BETWEEN $2 AND $3 AND ($4::text IS NULL OR merchant_id=$4)'
    r=await db.fetchrow(f"""SELECT sum(valor) FILTER (WHERE valor>0) AS creditos,sum(valor) FILTER (WHERE valor<0) AS debitos,
        sum(valor) FILTER (WHERE com_repasse) AS repasse,sum(valor) FILTER (WHERE NOT com_repasse AND valor>0) AS direto,
        count(DISTINCT ref_id) FILTER (WHERE ref_tipo='ORDER') AS pedidos,count(*) AS eventos FROM ifood_fin_eventos {filtro}""",*args)
    por_tipo=[{'nome':x['nome'],'descricao':x['descricao'],'eventos':x['n'],'valor':_d(x['valor']),'com_repasse':x['com_repasse']} for x in await db.fetch(f"""
        SELECT nome,descricao,com_repasse,count(*) AS n,sum(valor) AS valor FROM ifood_fin_eventos {filtro}
        GROUP BY 1,2,3 ORDER BY abs(sum(valor)) DESC,1,2""",*args)]
    por_dia=[{'data':x['data'],'creditos':_d(x['c']),'debitos':_d(x['d']),'repasse':_d(x['r'])} for x in await db.fetch(f"""
        SELECT data,sum(valor) FILTER (WHERE valor>0) AS c,sum(valor) FILTER (WHERE valor<0) AS d,sum(valor) FILTER (WHERE com_repasse) AS r
        FROM ifood_fin_eventos {filtro} GROUP BY 1 ORDER BY 1""",*args)]
    por_forma=[{'forma':x['forma'],'bandeira':x['bandeira'],'valor':_d(x['v']),'eventos':x['n']} for x in await db.fetch(f"""
        SELECT coalesce(forma_pagamento,'nao_informado') AS forma,bandeira,sum(valor) AS v,count(*) AS n FROM ifood_fin_eventos {filtro} AND valor>0
        GROUP BY 1,2 ORDER BY 3 DESC""",*args)]
    a_receber=[{'data':x['pagamento_previsto'],'valor':_d(x['v'])} for x in await db.fetch("""
        SELECT pagamento_previsto,sum(valor) AS v FROM ifood_fin_eventos WHERE tenant_id=$1 AND com_repasse AND pagamento_previsto>=$2
        AND ($3::text IS NULL OR merchant_id=$3) GROUP BY 1 ORDER BY 1""",tenant_id,hoje,loja)]
    return {'periodo':{'de':de,'ate':ate},
            'resumo':{'creditos':_d(r['creditos']),'debitos':_d(r['debitos']),'repasse_liquido':_d(r['repasse']),'recebido_direto':_d(r['direto']),
                      'pedidos':r['pedidos'],'eventos':r['eventos']},
            'por_tipo':por_tipo,'por_dia':por_dia,'por_forma':por_forma,
            'a_receber':{'total':_d(sum(x['valor'] for x in a_receber)),'por_data':a_receber},
            'coletas':[{'loja':c['merchant_id'],'nome':c['nome'],'sucesso_em':c['sucesso_em'],'erro':c['erro'],'coletando':c['coletando'],
                        'coletado_desde':c['coletado_desde']} for c in coletas]}


@router.post('/tenants/{tenant_id}/ifood/financeiro/atualizar')
async def atualizar(request:Request,tenant_id:str,tarefas:BackgroundTasks):
    """Coleta agora (depois da resposta). Uma loja coletada há menos de 10 min fica de fora."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): return _falha(404,'NOT_FOUND')
    n=await db.fetchval('SELECT count(*) FROM ifood_lojas WHERE tenant_id=$1 AND ativo',tenant_id)
    if n: tarefas.add_task(coletar_empresa,request.app.state,_prefixo(request),tenant_id)
    return {'coletando':n}
