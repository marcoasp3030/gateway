import hashlib,json
from test_admin import banco,migrar,preparar,criar,pytestmark
from test_pipeline_multi import RedisFila,ativo

def test_LGPD03_INV08_exportar_anonimizar_preserva_metricas_e_outro_tenant():
 async def caso(c):
  await migrar.aplicar(c);app,h=preparar(c);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
  async with h:
   a,ca=await criar(h);b,cb=await criar(h,'bravo')
   telefone='5511987654321'
   for t,ag,can in [('alpha',a,ca),('bravo',b,cb)]:
    await ativo(h,t,ag,can)
    r=await h.post(f'/admin/v1/tenants/{t}/agentes/{ag}/simular',json={'canal_id':can,'identificador':telefone,'texto':'Nome e telefone '+telefone,'message_id':telefone})
    await app.state.pipeline.processar(t,r.json()['conversation_id'])
   cid=(await h.get('/admin/v1/tenants/alpha/contatos')).json()['itens'][0]['id']
   url='/admin/v1/tenants/alpha/contatos/'+cid
   assert (await h.get(url.replace('alpha','bravo')+'/exportar')).status_code==404
   assert (await h.request('DELETE',url.replace('alpha','bravo'),json={'confirmacao':telefone})).status_code==404
   assert (await h.get(url+'/exportar',headers={'X-Actor':''})).status_code==400
   export=(await h.get(url+'/exportar')).json()
   assert export['contato']['telefone']==telefone and len(export['mensagens'])==2
   conv=export['conversas'][0]['conversation_id']
   await c.execute("INSERT INTO webhook_raw(tenant_id,fonte,payload) VALUES ('alpha','api',$1::jsonb)",json.dumps({'telefone':telefone,'nome':'Nome'}))
   wh=await h.post('/admin/v1/tenants/alpha/webhooks-saida',json={'url':'https://exemplo.example/receber','ativo':True,'eventos':['x']})
   await c.execute("INSERT INTO webhook_entregas(tenant_id,webhook_id,evento,corpo) VALUES ('alpha',$1,'x',$2)",wh.json()['id'],json.dumps({'conversation_id':conv}))
   assert (await h.request('DELETE',url,json={'confirmacao':'errado'})).status_code==400
   # Recusa durante processamento: nada parcialmente apagado.
   lock=app.state.pipeline.prefixo+':lock:'+hashlib.sha256(app.state.pipeline.chave('alpha',conv).encode()).hexdigest()
   fila.dados[lock]='ocupado'
   assert (await h.request('DELETE',url,json={'confirmacao':telefone})).status_code==409
   assert (await h.get(url+'/exportar')).json()['contato']['telefone']==telefone
   fila.dados.pop(lock)
   response=await h.request('DELETE',url,json={'confirmacao':telefone});assert response.status_code==200,response.text
   for tabela in ('contatos','conversas','mensagens','turnos','webhook_raw','webhook_entregas','audit_log'):
    rows=await c.fetch(f'SELECT row_to_json(t)::text AS dado FROM {tabela} t WHERE tenant_id=$1','alpha')
    assert all(telefone not in r['dado'] for r in rows),tabela
   assert await c.fetchval("SELECT count(*) FROM turnos WHERE tenant_id='alpha'")==1
   assert await c.fetchval("SELECT telefone FROM contatos WHERE tenant_id='bravo'")==telefone
   assert await c.fetchval("SELECT status FROM webhook_entregas WHERE tenant_id='alpha'")=='cancelada'
   assert (await h.get(url+'/exportar')).json()['contato']['anonimizado_em']
   assert all(conv not in key for key in fila.due)
 banco(caso)


def test_LGPD03_identificador_formatado_sem_juntar_campos_alheios():
 from admin.contatos import contem
 assert contem('{"telefone":"+55 (11) 98765-4321"}',['5511987654321'])
 assert not contem('{"pedido":"5511", "numero":"987654321"}',['5511987654321'])
