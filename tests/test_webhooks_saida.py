import hashlib,hmac,json
import httpx
from test_admin import banco,migrar,preparar,criar,pytestmark
from nucleo import webhooks


def test_INV08_HMAC_receptor_retentativas_e_ssrf(monkeypatch):
 async def caso(c):
  await migrar.aplicar(c);app,h=preparar(c)
  async with h:
   await criar(h);await criar(h,'bravo')
   root='/admin/v1/tenants/alpha/webhooks-saida'
   assert (await h.post(root,json={'url':'http://127.0.0.1','eventos':[]})).status_code==400
   r=await h.post(root,json={'url':'https://receptor.example/eventos','eventos':['conversa.transferida'],'ativo':True});assert r.status_code==201,r.text
   w=r.json();url=root+'/'+w['id']
   assert w['segredo'] not in (await h.get(url)).text
   for method,suffix,body in [('GET','',None),('PATCH','',{'ativo':False}),('DELETE','',None),('POST','/testar',None),('GET','/entregas',None)]:
    r=await h.request(method,url.replace('alpha','bravo')+suffix,json=body);assert r.status_code==404
   assert (await h.post(url+'/testar')).status_code==202
   dns=[];recebidas=[]
   async def validar(url): dns.append(url)
   monkeypatch.setattr(webhooks,'validar_url_saida',validar)
   async def receptor(req):
    body=await req.aread();ts=req.headers['X-Gateway-Timestamp']
    sig='sha256='+hmac.new(w['segredo'].encode(),ts.encode()+b'.'+body,hashlib.sha256).hexdigest()
    assert hmac.compare_digest(sig,req.headers['X-Gateway-Signature'])
    assert not hmac.compare_digest(sig,'sha256='+hmac.new(w['segredo'].encode(),ts.encode()+b'.'+body+b'alterado',hashlib.sha256).hexdigest())
    assert json.loads(body)['tenant_id']=='alpha'
    recebidas.append(req.headers['X-Gateway-Delivery'])
    return httpx.Response(503 if len(recebidas)<3 else 204)
   async with httpx.AsyncClient(transport=httpx.MockTransport(receptor)) as client:
    for i in range(3):
     await c.execute("UPDATE webhook_entregas SET proxima_em=now() WHERE tenant_id='alpha'")
     await webhooks.despachar(app.state.db,client,app.state.cofre)
   entregas=(await h.get(url+'/entregas')).json()['itens']
   assert entregas[0]['status']=='entregue' and entregas[0]['tentativas']==3
   assert len(set(recebidas))==1 and len(dns)==3
   # DNS privado é recusado antes de tocar no receptor; três tentativas encerram.
   async def privado(url): raise ValueError('privado')
   monkeypatch.setattr(webhooks,'validar_url_saida',privado)
   await h.post(url+'/testar')
   async with httpx.AsyncClient(transport=httpx.MockTransport(receptor)) as client:
    for i in range(4):
     await c.execute("UPDATE webhook_entregas SET proxima_em=now() WHERE tenant_id='alpha'")
     await webhooks.despachar(app.state.db,client,app.state.cofre)
   e=(await h.get(url+'/entregas')).json()['itens'][0]
   assert e['status']=='erro' and e['tentativas']==3 and len(recebidas)==3
   assert (await h.delete(url)).json()['ativo'] is False
 banco(caso)
