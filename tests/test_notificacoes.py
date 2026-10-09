from datetime import datetime,timezone,timedelta
from test_admin import banco,migrar,preparar,criar,pytestmark
from test_pipeline_multi import RedisFila,ativo

def test_SEG11_INV08_INV17_notificar_respeita_pausa_e_isola_evento():
 async def caso(c):
  await migrar.aplicar(c);app,h=preparar(c);fila=RedisFila();app.state.pipeline.redis=fila
  chamadas=[]
  async def agente(http,repo,cofre,t,cfg,payload):
   chamadas.append(payload);return {'resposta':['Aviso'],'acao':'nenhuma','uso':{'tokens_saida':3}}
  app.state.pipeline.chamar=agente
  async with h:
   a,ca=await criar(h);b,cb=await criar(h,'bravo');await ativo(h,'alpha',a,ca)
   conv=f'alpha:{ca}:cliente'
   await c.execute('INSERT INTO conversas(tenant_id,conversation_id,canal,canal_id,agente_id) VALUES ($1,$2,$3,$4,$5)','alpha',conv,'api',ca,a)
   async def chave(t,escopos):
    return (await h.post(f'/admin/v1/tenants/{t}/api-keys',json={'nome':'N','escopos':escopos})).json()['chave']
   url='/api/v1/conversas/'+conv+'/notificar';body={'evento':'pedido_saiu','dados':{'token':'nao-enviar','pedido':'42'},'instrucao':'Avise'}
   assert (await h.post(url,json=body,headers={'X-API-Key':await chave('alpha',['ferramentas:catalogo'])})).status_code==403
   assert (await h.post(url,json=body,headers={'X-API-Key':await chave('bravo',['notificar'])})).status_code==404
   k=await chave('alpha',['notificar'])
   assert (await h.post(url,json=body,headers={'X-API-Key':k})).status_code==202
   await app.state.pipeline.processar('alpha',conv)
   assert chamadas[0]['tarefa']=='notificar' and chamadas[0]['evento']['dados']['token']=='***'
   assert await c.fetchval("SELECT origem FROM turnos WHERE tenant_id='alpha'")=='sistema'
   await c.execute('UPDATE conversas SET bot_pausado_ate=$3 WHERE tenant_id=$1 AND conversation_id=$2','alpha',conv,datetime.now(timezone.utc)+timedelta(hours=1))
   await h.post(url,json=body,headers={'X-API-Key':k});await app.state.pipeline.processar('alpha',conv)
   assert len(chamadas)==1
   assert await c.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id='alpha' AND origem='bot'")==1
   assert await c.fetchval("SELECT count(*) FROM turnos WHERE tenant_id='alpha' AND status='ignorado'")==1
 banco(caso)
