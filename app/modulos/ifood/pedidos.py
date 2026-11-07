"""IFOOD1-b1 (docs/ESPEC_IFOOD1.md): pedidos do iFood por webhook, só leitura.

O iFood avisa cada evento (pedido feito, confirmado, pronto, despachado, concluído, cancelado) em POST /webhooks/ifood, assinado com
HMAC-SHA256 do corpo pela chave secreta do aplicativo (X-IFood-Signature). O gateway grava o evento, atualiza o status e busca o
detalhe do pedido (GET /order/v1.0/orders/{id}) depois de responder. Nada é aceito, recusado ou despachado daqui, e não há consulta
periódica de eventos: ela é que mantém a loja "online" no iFood.

Do cliente fica só o nome, o id do iFood e o bairro (sem telefone, documento, e-mail nem endereço)."""
import base64
import hashlib
import hmac
import json
from datetime import date,datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter,BackgroundTasks,Depends,Request
from fastapi.responses import JSONResponse
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from .cliente import Ifood,ErroIfood
from .rotas import _falha,_prefixo

publico=APIRouter()
router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
FUSO=ZoneInfo('America/Sao_Paulo')
# Ordem do ciclo: evento atrasado não faz o pedido voltar; cancelamento vale sempre.
ORDEM={'PLACED':0,'CONFIRMED':1,'SEPARATION_STARTED':2,'SEPARATION_ENDED':2,'READY_TO_PICKUP':3,'DISPATCHED':3,'CONCLUDED':4,'CANCELLED':9}
CURTO={'PLC':'PLACED','CFM':'CONFIRMED','SPS':'SEPARATION_STARTED','SPE':'SEPARATION_ENDED','RTP':'READY_TO_PICKUP','DSP':'DISPATCHED',
       'CON':'CONCLUDED','CAN':'CANCELLED'}
VENDA=('CONFIRMED','SEPARATION_STARTED','SEPARATION_ENDED','READY_TO_PICKUP','DISPATCHED','CONCLUDED')
MAX_TENTATIVAS=10
POR_VEZ=5


def assinatura_confere(segredo,corpo,recebida):
    """HMAC-SHA256 do corpo bruto com a chave secreta do aplicativo; aceita a assinatura em hexadecimal ou base64."""
    if not segredo or not recebida: return False
    d=hmac.new(segredo.encode(),corpo,hashlib.sha256).digest()
    recebida=recebida.strip()
    return hmac.compare_digest(recebida.lower(),d.hex()) or hmac.compare_digest(recebida,base64.b64encode(d).decode())


def _quando(v):
    if not isinstance(v,str) or not v: return None
    try:
        d=datetime.fromisoformat(v.replace('Z','+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError: return None


def _num(v):
    return round(float(v),2) if isinstance(v,(int,float)) and not isinstance(v,bool) else None


def _texto(v,n): return v[:n] if isinstance(v,str) and v else None


def retrato_detalhe(d):
    """Só o que o painel usa, sem dado pessoal além do nome e do bairro."""
    t=d.get('total') if isinstance(d.get('total'),dict) else {}
    itens=[]
    for i in d.get('items') or []:
        if not isinstance(i,dict): continue
        itens.append({'nome':_texto(i.get('name'),200),'codigo':_texto(i.get('externalCode'),80),'quantidade':i.get('quantity') if isinstance(i.get('quantity'),(int,float)) else None,
                      'preco_unitario':_num(i.get('unitPrice')),'total':_num(i.get('totalPrice')),
                      'opcoes':[{'nome':_texto(o.get('name'),200),'quantidade':o.get('quantity'),'preco':_num(o.get('price'))}
                                for o in (i.get('options') or []) if isinstance(o,dict)]})
    pagamentos=[{'forma':_texto(m.get('method'),40),'tipo':_texto(m.get('type'),40),'valor':_num(m.get('value')),
                 'bandeira':_texto((m.get('card') or {}).get('brand') if isinstance(m.get('card'),dict) else None,40)}
                for m in ((d.get('payments') or {}).get('methods') or []) if isinstance(m,dict)]
    entrega=d.get('delivery') if isinstance(d.get('delivery'),dict) else {}
    endereco=entrega.get('deliveryAddress') if isinstance(entrega.get('deliveryAddress'),dict) else {}
    cliente=d.get('customer') if isinstance(d.get('customer'),dict) else {}
    return {'numero':_texto(d.get('displayId'),40),'criado_em':_quando(d.get('createdAt')),'teste':d.get('isTest') is True,
            'tipo':_texto(d.get('orderType'),40),'momento':_texto(d.get('orderTiming'),40),'canal_venda':_texto(d.get('salesChannel'),40),
            'categoria':_texto(d.get('category'),40),'subtotal':_num(t.get('subTotal')),'taxa_entrega':_num(t.get('deliveryFee')),
            'descontos':_num(t.get('benefits')),'taxas_adicionais':_num(t.get('additionalFees')),'total':_num(t.get('orderAmount')),
            'pagamentos':pagamentos,'itens':itens,'cliente_id':_texto(cliente.get('id'),100),'cliente_nome':_texto(cliente.get('name'),200),
            'bairro':_texto(endereco.get('neighborhood'),120)}


async def detalhar(db,cli,tenant,order_id):
    """Busca e grava o detalhe. Falha conta tentativa (até MAX_TENTATIVAS) e fica para a próxima vez."""
    try: d=await cli.chamar('GET',f'/order/v1.0/orders/{order_id}')
    except ErroIfood as e:
        await db.execute('UPDATE ifood_pedidos SET tentativas=tentativas+1,atualizado_em=now() WHERE tenant_id=$1 AND order_id=$2',tenant,order_id)
        return e.motivo
    if not isinstance(d,dict):
        await db.execute('UPDATE ifood_pedidos SET tentativas=tentativas+1 WHERE tenant_id=$1 AND order_id=$2',tenant,order_id)
        return 'formato_inesperado'
    r=retrato_detalhe(d)
    await db.execute("""UPDATE ifood_pedidos SET numero=$3,teste=$4,criado_em=coalesce($5,criado_em),tipo=$6,momento=$7,canal_venda=$8,categoria=$9,
        subtotal=$10,taxa_entrega=$11,descontos=$12,taxas_adicionais=$13,total=$14,pagamentos=$15::jsonb,itens=$16::jsonb,cliente_id=$17,
        cliente_nome=$18,bairro=$19,detalhado_em=now(),atualizado_em=now() WHERE tenant_id=$1 AND order_id=$2""",
        tenant,order_id,r['numero'],r['teste'],r['criado_em'],r['tipo'],r['momento'],r['canal_venda'],r['categoria'],r['subtotal'],r['taxa_entrega'],
        r['descontos'],r['taxas_adicionais'],r['total'],json.dumps(r['pagamentos'],ensure_ascii=False),json.dumps(r['itens'],ensure_ascii=False),
        r['cliente_id'],r['cliente_nome'],r['bairro'])
    return None


async def detalhar_pendentes(estado,prefixo,tenant=None,limite=POR_VEZ):
    """Pedidos ainda sem detalhe (o mais antigo primeiro). Usado depois de cada webhook e pelo botão "Atualizar" do painel."""
    db=estado.db
    try: cli=await Ifood(estado.http,estado.redis,estado.cofre,prefixo).carregar(db)
    except ErroIfood: return 0
    if not cli.configurado: return 0
    rows=await db.fetch("""SELECT tenant_id,order_id FROM ifood_pedidos WHERE detalhado_em IS NULL AND tentativas<$1 AND ($2::text IS NULL OR tenant_id=$2)
        ORDER BY criado_em LIMIT $3""",MAX_TENTATIVAS,tenant,limite)
    feitos=0
    for r in rows:
        if await detalhar(db,cli,r['tenant_id'],r['order_id']) is None: feitos+=1
    return feitos


@publico.post('/webhooks/ifood')
async def webhook(request:Request,tarefas:BackgroundTasks):
    estado=request.app.state
    corpo=await request.body()
    try: cli=await Ifood(estado.http,estado.redis,estado.cofre,_prefixo(request)).carregar(estado.db)
    except ErroIfood: cli=None
    if not cli or not cli.configurado: return JSONResponse({'ok':False},status_code=503)
    if not assinatura_confere(cli._segredo,corpo,request.headers.get('x-ifood-signature')): return JSONResponse({'ok':False},status_code=401)
    try: bruto=json.loads(corpo)
    except ValueError: return JSONResponse({'ok':False},status_code=400)
    eventos=bruto if isinstance(bruto,list) else [bruto]
    lojas={}
    novos=0
    for e in eventos[:200]:
        if not isinstance(e,dict) or not isinstance(e.get('id'),str) or not isinstance(e.get('merchantId'),str): continue
        m=e['merchantId']
        if m not in lojas: lojas[m]=await estado.db.fetchval('SELECT tenant_id FROM ifood_lojas WHERE merchant_id=$1 AND ativo',m)
        tenant=lojas[m]
        if not tenant: continue
        codigo=e.get('fullCode') if isinstance(e.get('fullCode'),str) else CURTO.get(e.get('code'),e.get('code') if isinstance(e.get('code'),str) else '?')
        pedido=e.get('orderId') if isinstance(e.get('orderId'),str) and len(e['orderId'])<=100 else None
        quando=_quando(e.get('createdAt'))
        async with estado.db.acquire() as c,c.transaction():
            novo=await c.fetchval("""INSERT INTO ifood_eventos(id,tenant_id,merchant_id,order_id,codigo,criado_em) VALUES ($1,$2,$3,$4,$5,$6)
                ON CONFLICT (id) DO NOTHING RETURNING id""",e['id'][:100],tenant,m,pedido,codigo[:60],quando)
            if not novo or not pedido: continue
            novos+=1
            status=codigo if codigo in ORDEM else None
            await c.execute("""INSERT INTO ifood_pedidos(order_id,tenant_id,merchant_id,status,criado_em) VALUES ($1,$2,$3,coalesce($4,'PLACED'),coalesce($5,now()))
                ON CONFLICT (order_id) DO NOTHING""",pedido,tenant,m,status,quando)
            if status:
                atual=await c.fetchval('SELECT status FROM ifood_pedidos WHERE order_id=$1 FOR UPDATE',pedido)
                if status=='CANCELLED' or ORDEM.get(status,0)>ORDEM.get(atual,0):
                    await c.execute('UPDATE ifood_pedidos SET status=$2,atualizado_em=now() WHERE order_id=$1',pedido,status)
    # Detalhe depois da resposta: o iFood não espera a consulta.
    if novos: tarefas.add_task(detalhar_pendentes,estado,_prefixo(request))
    return JSONResponse({'ok':True},status_code=202)


def _pedido(r,completo=False):
    b={'order_id':r['order_id'],'numero':r['numero'],'loja':r['merchant_id'],'status':r['status'],'teste':r['teste'],'criado_em':r['criado_em'],
       'tipo':r['tipo'],'canal_venda':r['canal_venda'],'total':float(r['total']) if r['total'] is not None else None,
       'cliente_nome':r['cliente_nome'],'bairro':r['bairro'],'detalhado':r['detalhado_em'] is not None,
       'numero_itens':sum(int(i.get('quantidade') or 0) for i in _lista(r['itens']))}
    if completo:
        b.update({'momento':r['momento'],'categoria':r['categoria'],
                  **{k:(float(r[k]) if r[k] is not None else None) for k in ('subtotal','taxa_entrega','descontos','taxas_adicionais')},
                  'itens':_lista(r['itens']),'pagamentos':_lista(r['pagamentos'])})
    return b


def _lista(v): return json.loads(v) if isinstance(v,str) else (v or [])


def _dia(d,fim=False): return datetime.combine(d+timedelta(days=1) if fim else d,time(),FUSO)


@router.get('/tenants/{tenant_id}/ifood/pedidos')
async def lista(request:Request,tenant_id:str,de:date|None=None,ate:date|None=None,status:str|None=None,loja:str|None=None,
                teste:bool|None=None,limite:int=50,antes:datetime|None=None):
    """Pedidos do iFood da empresa, do mais novo para o mais antigo. `antes`: o criado_em do último da página anterior."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): return _falha(404,'NOT_FOUND')
    if not 1<=limite<=100 or (antes is not None and antes.tzinfo is None): return _falha(400,'formato','limite')
    varios=[s for s in (status or '').split(',') if s] or None
    rows=await db.fetch("""SELECT * FROM ifood_pedidos WHERE tenant_id=$1 AND ($2::timestamptz IS NULL OR criado_em>=$2) AND ($3::timestamptz IS NULL OR criado_em<$3)
        AND ($4::text[] IS NULL OR status=ANY($4)) AND ($5::text IS NULL OR merchant_id=$5) AND ($6::boolean IS NULL OR teste=$6)
        AND ($7::timestamptz IS NULL OR criado_em<$7) ORDER BY criado_em DESC,order_id LIMIT $8""",
        tenant_id,_dia(de) if de else None,_dia(ate,True) if ate else None,varios,loja,teste,antes,limite+1)
    return {'itens':[_pedido(r) for r in rows[:limite]],'proximo_antes':rows[limite-1]['criado_em'] if len(rows)>limite else None}


@router.get('/tenants/{tenant_id}/ifood/pedidos/{order_id}')
async def detalhe(request:Request,tenant_id:str,order_id:str):
    db=request.app.state.db
    r=await db.fetchrow('SELECT * FROM ifood_pedidos WHERE tenant_id=$1 AND order_id=$2',tenant_id,order_id)
    if not r: return _falha(404,'NOT_FOUND')
    eventos=[dict(e) for e in await db.fetch('SELECT codigo,criado_em,recebido_em FROM ifood_eventos WHERE tenant_id=$1 AND order_id=$2 ORDER BY coalesce(criado_em,recebido_em),id',
                                             tenant_id,order_id)]
    return {**_pedido(r,True),'eventos':eventos}


@router.post('/tenants/{tenant_id}/ifood/pedidos/atualizar')
async def atualizar(request:Request,tenant_id:str):
    """Busca agora o detalhe dos pedidos que ainda não têm (até 20)."""
    if not await Repositorio(request.app.state.db).tenant(tenant_id): return _falha(404,'NOT_FOUND')
    feitos=await detalhar_pendentes(request.app.state,_prefixo(request),tenant_id,20)
    pendentes=await request.app.state.db.fetchval('SELECT count(*) FROM ifood_pedidos WHERE tenant_id=$1 AND detalhado_em IS NULL',tenant_id)
    return {'detalhados':feitos,'pendentes':pendentes}
