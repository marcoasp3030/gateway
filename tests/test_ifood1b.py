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
