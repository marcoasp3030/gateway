"""IFOOD1-b4 (docs/ESPEC_IFOOD1.md): indicadores do próprio iFood (Analytics v1, POST /merchants/{id}/orders/kpis), só leitura.

Consulta na hora (com cópia de 10 min no Redis), sem gravar: o iFood já guarda o histórico. Formato confirmado em 08/11 com a
especificação (Schemas) enviada pelo Marco e uma consulta de leitura na loja de teste: `filter.referenceDate` (gte/lt) e pelo menos uma
agregação; `dateIntervals` pede 2 ou mais (usamos o período e o anterior, para comparar). Cada item de `data` traz a chave do grupo
(`groupByKey` ou `dateRangeKey`, com `count` e `value`) e as métricas pedidas ({avg,min,max,sum})."""
import hashlib
import json
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,Request
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from .cliente import Ifood,ErroIfood
from .rotas import _falha,_prefixo

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
FUSO=ZoneInfo('America/Sao_Paulo')
CACHE_S=600
METRICAS={'gmv':['sum','avg'],'paymentTotalValue':['sum'],'refundedTotalValue':['sum'],'netDeliveryFee':['sum'],'freightChargedFromMerchant':['sum'],
          'benefitByTypePartnerTotalValue':['sum'],'preparationTimeInMin':['avg'],'readyForDeliveryInMin':['avg'],'timeToDeliveryInMin':['avg'],
          'timeDelayedInMin':['avg'],'timeAtOriginInMin':['avg'],'distanceKm':['avg']}
GRUPOS={'dia_semana':'dayOfWeek','hora':'orderCreationHour','status':'orderStatus','pagamento':'paymentMethod','primeiro_pedido':'customerFirstOrderInMerchant',
        'motivo_cancelamento':'cancellationReason','origem_cancelamento':'cancellationOrigin','entrega_por':'deliveredBy','canal':'salesChannel'}


def _linha(item):
    """Item de `data`: grupo (valor e contagem) e métricas numéricas, sem supor mais nada do formato."""
    if not isinstance(item,dict): return None
    grupo=item.get('groupByKey') if isinstance(item.get('groupByKey'),dict) else item.get('dateRangeKey') if isinstance(item.get('dateRangeKey'),dict) else {}
    metricas={k:{f:v[f] for f in ('avg','min','max','sum') if isinstance(v.get(f),(int,float))} for k,v in item.items()
              if k in METRICAS and isinstance(v,dict)}
    return {'grupo':grupo.get('value'),'pedidos':grupo.get('count') if isinstance(grupo.get('count'),int) else None,'metricas':metricas}


async def _consulta(cli,merchant,corpo):
    d=await cli.chamar('POST',f'/analytics/v1.0/merchants/{merchant}/orders/kpis',json={'page':1,'size':100,**corpo})
    if d is None: return []
    if not isinstance(d,dict) or not isinstance(d.get('data'),list): raise ErroIfood('formato_inesperado')
    return [x for x in (_linha(i) for i in d['data']) if x]


@router.get('/tenants/{tenant_id}/ifood/indicadores')
async def indicadores(request:Request,tenant_id:str,loja:str,de:date|None=None,ate:date|None=None):
    """Indicadores do iFood de uma loja: o período e o anterior (pedidos, GMV, valores, tempos) e a divisão por dia da semana, hora,
    status, pagamento, cliente novo, cancelamento, quem entrega e canal. Padrão: 30 dias até ontem."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): return _falha(404,'NOT_FOUND')
    if not await db.fetchval('SELECT EXISTS(SELECT 1 FROM ifood_lojas WHERE tenant_id=$1 AND merchant_id=$2 AND ativo)',tenant_id,loja): return _falha(404,'NOT_FOUND')
    hoje=datetime.now(FUSO).date();ate=ate or hoje-timedelta(days=1);de=de or ate-timedelta(days=29)
    if de>ate or (ate-de).days>366: return _falha(400,'formato','periodo')
    dias=(ate-de).days+1;ant_ate=de-timedelta(days=1);ant_de=ant_ate-timedelta(days=dias-1)
    chave=f'{_prefixo(request)}:ifood:kpis:'+hashlib.sha256(f'{tenant_id}|{loja}|{de}|{ate}'.encode()).hexdigest()[:24]
    redis=request.app.state.redis
    try: guardado=await redis.get(chave)
    except Exception: guardado=None
    if guardado: return json.loads(guardado)
    fim=(ate+timedelta(days=1)).isoformat()
    filtro={'referenceDate':{'gte':ant_de.isoformat(),'lt':fim}}
    so_periodo={'referenceDate':{'gte':de.isoformat(),'lt':fim}}
    try:
        cli=await Ifood(request.app.state.http,redis,request.app.state.cofre,_prefixo(request)).carregar(db)
        comparacao=await _consulta(cli,loja,{'filter':filtro,'agg':{'dateIntervals':[{'from':ant_de.isoformat(),'to':de.isoformat()},{'from':de.isoformat(),'to':fim}],
                                                                       'metrics':METRICAS}})
        grupos={}
        for nome,campo in GRUPOS.items():
            grupos[nome]=await _consulta(cli,loja,{'filter':so_periodo,'agg':{'groupBy':{'fields':[campo]},'metrics':{'gmv':['sum'],'paymentTotalValue':['sum']}}})
    except ErroIfood as e:
        return _falha(503 if e.motivo=='nao_configurado' else 502,e.motivo)
    # dateRangeKey vem com from/to: o intervalo que começa em `de` é o período; o outro, o anterior.
    periodo=next((c for c in comparacao if isinstance(c['grupo'],dict) and str(c['grupo'].get('from','')).startswith(de.isoformat())),None)
    anterior=next((c for c in comparacao if c is not periodo),None)
    resposta={'loja':loja,'periodo':{'de':de.isoformat(),'ate':ate.isoformat(),'anterior':{'de':ant_de.isoformat(),'ate':ant_ate.isoformat()}},
              'resumo':periodo,'anterior':anterior,'grupos':grupos,'consultado_em':datetime.now(FUSO).isoformat(timespec='seconds')}
    try: await redis.set(chave,json.dumps(resposta,ensure_ascii=False),ex=CACHE_S)
    except Exception: pass
    return resposta
