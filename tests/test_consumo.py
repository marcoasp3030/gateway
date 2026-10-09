from test_admin import banco,migrar,preparar,criar,pytestmark
from test_pipeline_multi import RedisFila,ativo
from nucleo.consumo import intervalo
from datetime import datetime,timezone

def test_INV08_teto_avisa_uma_vez_sem_cortar_e_mes_local():
 async def caso(c):
  await migrar.aplicar(c);app,h=preparar(c);app.state.pipeline.redis=RedisFila()
  async with h:
   a,ca=await criar(h);await criar(h,'bravo');await ativo(h,'alpha',a,ca)
   await h.patch('/admin/v1/tenants/alpha',json={'limite_turnos_mes':5})
   for i in range(7):
    r=await h.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':ca,'identificador':'u','texto':str(i)})
    await app.state.pipeline.processar('alpha',r.json()['conversation_id'])
   r=await h.get('/admin/v1/tenants/alpha/consumo');assert r.status_code==200,r.text
   d=r.json();assert d['turnos']==7 and d['agentes'][0]['turnos']==7
   assert [(x['percentual'],x['turnos']) for x in d['avisos']]==[(80,4),(100,5)]
   assert (await h.get('/admin/v1/tenants/bravo/consumo')).json()['turnos']==0
   assert (await h.get('/admin/v1/tenants/inexistente/consumo')).status_code==404
   assert (await h.get('/admin/v1/tenants/alpha/consumo?mes=2026-99')).status_code==400
   assert await c.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id='alpha' AND origem='bot'")==7
 banco(caso)
 assert intervalo('America/Sao_Paulo',agora=datetime(2026,10,1,1,tzinfo=timezone.utc))[0]=='2026-09'
