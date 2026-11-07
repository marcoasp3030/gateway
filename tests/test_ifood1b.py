"""IFOOD1-b1 (docs/ESPEC_IFOOD1.md): pedidos do iFood por webhook assinado, só leitura; detalhe buscado depois da resposta;
sem dado pessoal além de nome e bairro; status não volta; pedidos entram na Visão de vendas. iFood simulado."""
import base64
import hashlib
import hmac
import json
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_ifood1a import RedisSimples,IfoodFalso,SEGREDO,GESTOR,SUP

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
T='bykoji'
DOC,EMAIL,FONE='00000000000','cliente@exemplo.test','0800000000'


def detalhe(order_id,teste=False,total=50.0):
    return {'id':order_id,'displayId':'A1B2','createdAt':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'category':'FOOD',
            'orderTiming':'IMMEDIATE','orderType':'DELIVERY','isTest':teste,'salesChannel':'IFOOD',
            'merchant':{'id':'loja-a-1','name':'Loja Teste A'},
            'customer':{'id':'cli-ifood-1','name':'Cliente Teste iFood','documentNumber':DOC,'phone':{'number':FONE,'localizer':'12345678'}},
            'items':[{'name':'Temaki Teste','externalCode':'GUE-0001','quantity':2,'unitPrice':20.0,'totalPrice':40.0,'options':[]}],
            'total':{'subTotal':40.0,'deliveryFee':10,'benefits':0,'additionalFees':0,'orderAmount':total},
            'payments':{'prepaid':total,'methods':[{'value':total,'currency':'BRL','method':'CREDIT','prepaid':True,'type':'ONLINE','card':{'brand':'VISA'}}]},
            'delivery':{'deliveryAddress':{'streetName':'Rua Teste','streetNumber':'1','neighborhood':'Centro','city':'Cidade Teste'}},
            'additionalInfo':{'metadata':{'customerEmail':EMAIL}}}


class IfoodComPedidos(IfoodFalso):
    def __init__(self):
        super().__init__();self.pedidos={}
    def __call__(self,req):
        p=req.url.path
        if p.startswith('/order/v1.0/orders/') and req.headers.get('authorization','').removeprefix('Bearer ') in self.validos:
            if p in self.forcar: return httpx.Response(self.forcar[p])
            o=p.rsplit('/',1)[1]
            return httpx.Response(200,json=self.pedidos[o]) if o in self.pedidos else httpx.Response(404)
        return super().__call__(req)


def evento(i,codigo,pedido,merchant='loja-a-1',quando=None):
    return {'id':f'ev-{i}','code':codigo[:3],'fullCode':codigo,'orderId':pedido,'merchantId':merchant,
            'createdAt':(quando or datetime.now(timezone.utc)).isoformat().replace('+00:00','Z'),'salesChannel':'IFOOD'}


def assinado(corpo,segredo=SEGREDO,b64=False):
    d=hmac.new(segredo.encode(),corpo,hashlib.sha256).digest()
    return {'X-IFood-Signature':base64.b64encode(d).decode() if b64 else d.hex(),'Content-Type':'application/json'}


def test_pedidos_do_ifood_por_webhook(monkeypatch):
    monkeypatch.delenv('IFOOD_CLIENT_ID',raising=False);monkeypatch.delenv('IFOOD_CLIENT_SECRET',raising=False)
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);falso=IfoodComPedidos()
        app.state.redis=RedisSimples();app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(falso))
        await conn.execute("INSERT INTO tenant_unidades(id,tenant_id,nome,codigo) VALUES ('u1',$1,'Pátio','patio_guedala')",T)
        async with client:
            async def avisar(eventos,**kw):
                corpo=json.dumps(eventos).encode()
                return await client.post('/webhooks/ifood',content=corpo,headers={**assinado(corpo,**kw),'Authorization':''})
            # Sem credenciais: 503 (o iFood tenta de novo); nada gravado.
            assert (await avisar(evento(0,'PLACED','ped-0'))).status_code==503
            monkeypatch.setenv('IFOOD_CLIENT_ID','app-teste');monkeypatch.setenv('IFOOD_CLIENT_SECRET',SEGREDO)
            assert (await client.put(f'/admin/v1/tenants/{T}/ifood/lojas/loja-a-1',json={'unidade':'patio_guedala'})).status_code==200
            # Assinatura errada ou ausente: 401, nada gravado.
            corpo=json.dumps(evento(1,'PLACED','ped-1')).encode()
            assert (await client.post('/webhooks/ifood',content=corpo,headers={'Content-Type':'application/json'})).status_code==401
            assert (await avisar(evento(1,'PLACED','ped-1'),segredo='outro-segredo-qualquer')).status_code==401
            assert await conn.fetchval('SELECT count(*) FROM ifood_eventos')==0
            # Pedido novo: 202, evento e pedido gravados, detalhe buscado depois da resposta.
            falso.pedidos['ped-1']=detalhe('ped-1')
            assert (await avisar(evento(1,'PLACED','ped-1'))).status_code==202
            p=await conn.fetchrow("SELECT * FROM ifood_pedidos WHERE order_id='ped-1'")
            assert (p['tenant_id'],p['status'],p['numero'],float(p['total']),p['cliente_nome'],p['bairro'],p['teste'])==(T,'PLACED','A1B2',50.0,'Cliente Teste iFood','Centro',False)
            assert p['detalhado_em'] is not None and json.loads(p['pagamentos'])[0]=={'forma':'CREDIT','tipo':'ONLINE','valor':50.0,'bandeira':'VISA'}
            # Nada de documento, telefone, e-mail ou rua.
            linha=json.dumps({k:str(v) for k,v in dict(p).items()})
            assert DOC not in linha and FONE not in linha and EMAIL not in linha and 'Rua Teste' not in linha
            # Assinatura em base64 também vale; evento repetido não duplica; status não volta; cancelamento vale sempre.
            assert (await avisar([evento(2,'CONFIRMED','ped-1'),evento(3,'CONCLUDED','ped-1')],b64=True)).status_code==202
            assert (await avisar(evento(2,'CONFIRMED','ped-1'))).status_code==202
            assert await conn.fetchval("SELECT count(*) FROM ifood_eventos WHERE order_id='ped-1'")==3
            assert await conn.fetchval("SELECT status FROM ifood_pedidos WHERE order_id='ped-1'")=='CONCLUDED'
            # Loja que não está ligada a nenhuma empresa: ignorado.
            assert (await avisar(evento(9,'PLACED','ped-9',merchant='loja-b-2'))).status_code==202
            assert await conn.fetchval("SELECT count(*) FROM ifood_pedidos WHERE order_id='ped-9'")==0
            # iFood fora do ar no detalhe: o pedido fica pendente e "Atualizar" busca depois.
            falso.pedidos['ped-2']=detalhe('ped-2',total=30.0);falso.forcar['/order/v1.0/orders/ped-2']=503
            assert (await avisar([evento(4,'PLACED','ped-2'),evento(5,'CONFIRMED','ped-2')])).status_code==202
            assert await conn.fetchval("SELECT tentativas FROM ifood_pedidos WHERE order_id='ped-2'")==1
            del falso.forcar['/order/v1.0/orders/ped-2']
            q=f'/admin/v1/tenants/{T}/ifood/pedidos'
            assert (await client.post(q+'/atualizar',headers=SUP)).status_code==403
            assert (await client.post(q+'/atualizar',headers=GESTOR)).json()=={'detalhados':1,'pendentes':0}
            # Pedido de teste do iFood.
            falso.pedidos['ped-3']=detalhe('ped-3',teste=True,total=99.0)
            await avisar([evento(6,'PLACED','ped-3'),evento(7,'CONFIRMED','ped-3')])
            # Painel: lista e detalhe só para gestor.
            assert (await client.get(q,headers=SUP)).status_code==403
            lista=(await client.get(q,headers=GESTOR)).json()['itens']
            assert {i['order_id'] for i in lista}=={'ped-1','ped-2','ped-3'} and all(i['detalhado'] for i in lista)
            assert [i['order_id'] for i in (await client.get(q+'?teste=false&status=CONCLUDED',headers=GESTOR)).json()['itens']]==['ped-1']
            d=(await client.get(q+'/ped-1',headers=GESTOR)).json()
            assert d['itens'][0]['nome']=='Temaki Teste' and [e['codigo'] for e in d['eventos']]==['PLACED','CONFIRMED','CONCLUDED']
            assert (await client.get('/admin/v1/tenants/outra/ifood/pedidos/ped-1',headers=GESTOR)).status_code==404
            # Visão de vendas: iFood junto, sem o pedido de teste (só com origem=teste).
            v=(await client.get(f'/admin/v1/tenants/{T}/pedidos/indicadores',headers=GESTOR)).json()['ifood']
            assert (v['resumo']['pedidos'],v['resumo']['faturamento'],v['resumo']['ticket_medio'])==(2,80.0,40.0)
            assert v['produtos'][0]=={'nome':'Temaki Teste','quantidade':4,'faturamento':80.0} and sum(e['pedidos'] for e in v['evolucao'])==2
            v=(await client.get(f'/admin/v1/tenants/{T}/pedidos/indicadores?origem=real,teste',headers=GESTOR)).json()['ifood']
            assert v['resumo']['pedidos']==3
            v=(await client.get(f'/admin/v1/tenants/{T}/pedidos/indicadores?unidade=outra',headers=GESTOR)).json()['ifood']
            assert v['resumo']['pedidos']==0
            await app.state.http.aclose()
    banco(cenario)


class IfoodFinanceiro(IfoodComPedidos):
    """Eventos financeiros por data; páginas de 2 para exercitar hasNextPage. Repasses por data de pagamento (404 sem nenhum)."""
    def __init__(self):
        super().__init__();self.fin=[];self.consultas=0;self.titulos=[]
    def __call__(self,req):
        p=req.url.path
        if p.endswith('/settlements') and req.headers.get('authorization','').removeprefix('Bearer ') in self.validos:
            q=req.url.params;sel=[t for t in self.titulos if q['beginPaymentDate']<=t['paymentDate']<=q['endPaymentDate']]
            if not sel: return httpx.Response(404,json={'message':'Nenhuma liquidação'})
            return httpx.Response(200,json={'beginDate':q['beginPaymentDate'],'endDate':q['endPaymentDate'],'balance':sum(t['amount'] for t in sel),
                                            'merchantId':'loja-a-1','settlements':[{'startDateCalculation':'2026-01-05','endDateCalculation':'2026-01-11','closingItems':sel}]})
        if p.endswith('/financial-events') and req.headers.get('authorization','').removeprefix('Bearer ') in self.validos:
            if p in self.forcar: return httpx.Response(self.forcar[p])
            self.consultas+=1;q=req.url.params
            sel=[e for e in self.fin if q['beginDate']<=e['dateTime'][:10]<=q['endDate']]
            if not sel: return httpx.Response(204)
            pg=int(q['page']);fatia=sel[(pg-1)*2:pg*2]
            return httpx.Response(200,json={'page':pg,'size':2,'hasNextPage':len(sel)>pg*2,'financialEvents':fatia})
        return super().__call__(req)


def fin(dia,nome,valor,repasse,pedido=None,previsto=None,forma='CREDIT',taxa=None):
    return {'name':nome,'description':nome,'product':'IFOOD','trigger':'ORDER_CONCLUDED','dateTime':f'{dia.isoformat()}T00:00:00Z',
            'competence':dia.isoformat()[:7],'reference':{'type':'ORDER','id':pedido,'date':dia.isoformat()} if pedido else {},
            'hasTransferImpact':repasse,'amount':{'value':valor},'billing':{'baseValue':100,'feePercentage':taxa} if taxa else {},
            'settlement':{'expectedDate':previsto.isoformat()} if previsto else {},'receiver':{'merchantId':'loja-a-1','merchantDocument':'00000000000000'},
            'payment':{'method':forma,'brand':'VISA','liability':'INTERNAL'}}


def test_financeiro_do_ifood(monkeypatch):
    from zoneinfo import ZoneInfo
    monkeypatch.setenv('IFOOD_CLIENT_ID','app-teste');monkeypatch.setenv('IFOOD_CLIENT_SECRET',SEGREDO)
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);falso=IfoodFinanceiro()
        app.state.redis=RedisSimples();app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(falso))
        hoje=datetime.now(ZoneInfo('America/Sao_Paulo')).date();d1=hoje-timedelta(days=1);d2=hoje-timedelta(days=2);velho=hoje-timedelta(days=60)
        falso.fin=[fin(d1,'SALE_CONCLUDED',100,True,'ped-1',hoje+timedelta(days=3)),fin(d1,'ORDER_COMMISSION',-12,True,'ped-1',hoje+timedelta(days=3),taxa=12),
                   fin(d2,'SALE_CONCLUDED',50,False,'ped-2',forma='PIX'),fin(d2,'ORDER_COMMISSION',-6,True,'ped-2',hoje+timedelta(days=3),taxa=12),
                   fin(velho,'SALE_CONCLUDED',30,True,'ped-0',velho+timedelta(days=7))]
        banco_ficticio={'bankName':'BANCO TESTE','bankNumber':'999','branchCode':'0001','accountNumber':'44416444','accountDigit':'4','documentNumber':'24222444222444'}
        falso.titulos=[{'id':'t-1','type':'REPASSE','product':'IFOOD','amount':500.0,'status':'SUCCEED','transactionId':'x','accountDetails':banco_ficticio,'paymentDate':d2.isoformat()},
                       {'id':'t-2','type':'REGISTRO_RECEBIVEIS','product':'IFOOD','amount':120.0,'status':'SUCCEED','accountDetails':{},'paymentDate':d2.isoformat()},
                       {'id':'t-3','type':'REPASSE','product':'IFOOD','amount':80.0,'status':'PENDING','accountDetails':banco_ficticio,'paymentDate':d1.isoformat()}]
        async with client:
            assert (await client.put(f'/admin/v1/tenants/{T}/ifood/lojas/loja-a-1',json={})).status_code==200
            await conn.execute("INSERT INTO ifood_pedidos(order_id,tenant_id,merchant_id,criado_em,status) VALUES ('ped-1',$1,'loja-a-1',now(),'CONCLUDED')",T)
            q=f'/admin/v1/tenants/{T}/ifood/financeiro'
            assert (await client.get(q,headers=SUP)).status_code==403
            # Primeira vez: responde com o que há (nada) e coleta 90 dias depois da resposta.
            r=(await client.get(q,headers=GESTOR)).json();assert r['resumo']['eventos']==0
            assert await conn.fetchval('SELECT count(*) FROM ifood_fin_eventos')==5
            assert DOC not in str([dict(x) for x in await conn.fetch('SELECT * FROM ifood_fin_eventos')]) and '00000000000000' not in str([dict(x) for x in await conn.fetch('SELECT * FROM ifood_fin_eventos')])
            r=(await client.get(q,headers=GESTOR)).json()
            assert r['resumo']=={'creditos':150.0,'debitos':-18.0,'repasse_liquido':82.0,'recebido_direto':50.0,'pedidos':2,'eventos':4}
            assert r['a_receber']=={'total':82.0,'por_data':[{'data':(hoje+timedelta(days=3)).isoformat(),'valor':82.0}]}
            assert {(x['forma'],x['valor']) for x in r['por_forma']}=={('CREDIT',100.0),('PIX',50.0)} and len(r['por_dia'])==2
            assert r['coletas'][0]['erro'] is None and r['coletas'][0]['coletado_desde']==(hoje-timedelta(days=89)).isoformat()
            rep=r['repasses']
            assert (rep['pago'],rep['pendente'],rep['retido_recebiveis'],rep['boletos'])==(500.0,80.0,120.0,0.0) and [x['titulo'] for x in rep['itens']]==['t-3','t-1','t-2']
            # Dados bancários não são gravados.
            tudo=str([dict(x) for x in await conn.fetch('SELECT * FROM ifood_repasses')])
            assert '44416444' not in tudo and '24222444222444' not in tudo and 'BANCO TESTE' not in tudo
            # Detalhe do pedido mostra o que o iFood creditou e descontou nele.
            d=(await client.get(f'/admin/v1/tenants/{T}/ifood/pedidos/ped-1',headers=GESTOR)).json()['financeiro']
            assert [(x['nome'],x['valor'],x['taxa_pct']) for x in d]==[('SALE_CONCLUDED',100.0,None),('ORDER_COMMISSION',-12.0,12.0)]
            # Atualizar há menos de 10 min: não consulta de novo. Depois disso: refaz só os últimos 8 dias, corrigindo sem duplicar.
            antes=falso.consultas
            assert (await client.post(q+'/atualizar',headers=GESTOR)).json()=={'coletando':1} and falso.consultas==antes
            await conn.execute("UPDATE ifood_coletas SET sucesso_em=now()-interval '1 hour'")
            falso.fin[1]=fin(d1,'ORDER_COMMISSION',-15,True,'ped-1',hoje+timedelta(days=3),taxa=15)
            falso.titulos[2]={**falso.titulos[2],'status':'SUCCEED'}
            await client.post(q+'/atualizar',headers=GESTOR)
            rep=(await client.get(q,headers=GESTOR)).json()['repasses']
            assert (rep['pago'],rep['pendente'])==(580.0,0.0) and await conn.fetchval('SELECT count(*) FROM ifood_repasses')==3
            assert falso.consultas-antes<=4 and await conn.fetchval('SELECT count(*) FROM ifood_fin_eventos')==5
            assert (await client.get(q,headers=GESTOR)).json()['resumo']['repasse_liquido']==79.0
            # Período antigo continua lá; filtro de datas.
            assert (await client.get(q+f'?de={velho.isoformat()}&ate={velho.isoformat()}',headers=GESTOR)).json()['resumo']['creditos']==30.0
            # Loja sem permissão no financeiro: erro gravado, dados anteriores ficam.
            await conn.execute("UPDATE ifood_coletas SET sucesso_em=now()-interval '1 hour'")
            falso.forcar['/financial/v3.0/merchants/loja-a-1/financial-events']=403
            await client.post(q+'/atualizar',headers=GESTOR)
            r=(await client.get(q,headers=GESTOR)).json()
            assert r['coletas'][0]['erro']=='sem_permissao' and r['resumo']['eventos']==4
            await app.state.http.aclose()
    banco(cenario)
