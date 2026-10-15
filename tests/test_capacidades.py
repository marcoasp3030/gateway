from test_admin import banco,migrar,preparar,criar,pytestmark

def test_INV08_SEG06_modulo_desabilitado_e_catalogo_isolado():
 async def caso(c):
  await migrar.aplicar(c);app,h=preparar(c)
  async with h:
   a,ca=await criar(h);b,cb=await criar(h,'bravo')
   h.headers['X-Role']='plataforma'  # chaves de módulo da empresa e troca de modelo: só a plataforma (MULTI1-a)
   assert (await h.get('/admin/v1/modulos')).status_code==200
   for t in ('alpha','bravo'):
    for m in ('catalogo','pedidos'):
     assert (await h.put(f'/admin/v1/tenants/{t}/modulos/{m}',json={'ativo':True})).status_code==200
   assert (await h.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'modelo_id':'delivery_restaurante'})).status_code==200
   conv=f'alpha:{ca}:cliente'
   await c.execute('INSERT INTO conversas(conversation_id,canal,tenant_id,agente_id,canal_id) VALUES ($1,$2,$3,$4,$5)',conv,'api','alpha',a,ca)
   for t in ('alpha','bravo'):
    await c.execute("INSERT INTO catalogo_itens(tenant_id,unidade,categoria,nome,preco) VALUES ($1,'TODAS','C',$1,12)",t)
   k=(await h.post('/admin/v1/tenants/alpha/api-keys',json={'nome':'K','escopos':['ferramentas:catalogo']})).json()['chave']
   url='/api/v1/catalogo/busca?conversation_id='+conv
   r=await h.get(url,headers={'X-API-Key':k});assert r.status_code==200,r.text
   assert [x['nome'] for x in r.json()['itens']]==['alpha']
   assert (await h.get('/api/v1/catalogo/busca?conversation_id=bravo:outro:cliente',headers={'X-API-Key':k})).status_code==404
   await h.put('/admin/v1/tenants/alpha/modulos/catalogo',json={'ativo':False})
   assert (await h.get(url,headers={'X-API-Key':k})).status_code==404
   assert (await h.get('/admin/v1/tenants/bravo/modulos')).json()['itens'][0]['ativo']
   assert (await h.put('/admin/v1/tenants/inexistente/modulos/catalogo',json={'ativo':True})).status_code==404
 banco(caso)

def test_INV08_modelos_versionados_globais_privados_e_auditoria():
 async def caso(c):
  await migrar.aplicar(c);app,h=preparar(c)
  async with h:
   a,ca=await criar(h);await criar(h,'bravo')
   h.headers['X-Role']='plataforma'  # criar e alterar modelo: só a plataforma (MULTI1-a)
   for mid,t in [('global_model',None),('privado','alpha')]:
    r=await h.post('/admin/v1/modelos-agente',json={'id':mid,'nome':'M','tenant_id':t,'comportamento_padrao':{'history_turns':2}})
    assert r.status_code==201,r.text
   assert (await h.get('/admin/v1/modelos-agente/privado?tenant=bravo')).status_code==404
   assert (await h.patch('/admin/v1/modelos-agente/privado?tenant=bravo',json={'nome':'invasao'})).status_code==404
   assert (await h.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'modelo_id':'privado'})).status_code==200
   assert (await h.patch('/admin/v1/modelos-agente/privado?tenant=alpha',json={'comportamento_padrao':{'history_turns':7}})).json()['versao']==2
   assert (await h.get(f'/admin/v1/tenants/alpha/agentes/{a}')).json()['configuracao_efetiva']['history_turns']==2
   assert (await h.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'modelo_versao':2})).status_code==200
   assert (await h.get(f'/admin/v1/tenants/alpha/agentes/{a}')).json()['configuracao_efetiva']['history_turns']==7
   assert (await h.get('/admin/v1/auditoria/plataforma')).json()['itens'][0]['alvo']=='global_model:1'
 banco(caso)
