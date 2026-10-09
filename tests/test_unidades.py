from datetime import datetime,timezone
from test_admin import banco,migrar,preparar,criar,pytestmark
from nucleo.horarios import estado,pacote

def test_horarios_fuso_fechamento_e_madrugada():
 h={'seg':[['22:00','02:00']],'ter':[['12:00','15:00']]}
 assert estado(h,'America/Sao_Paulo',datetime(2026,10,6,4,tzinfo=timezone.utc))['aberta_agora']
 d=estado(h,'America/Sao_Paulo',datetime(2026,10,6,5,tzinfo=timezone.utc))
 assert not d['aberta_agora'] and d['proxima_abertura']=='2026-10-06T12:00:00-03:00'
 assert estado({},'UTC')['proxima_abertura'] is None

def test_INV08_unidades_crud_pacote_so_tenant_ativo():
 async def caso(c):
  await migrar.aplicar(c);app,h=preparar(c)
  async with h:
   await criar(h);await criar(h,'bravo')
   root='/admin/v1/tenants/alpha/unidades'
   r=await h.post(root,json={'nome':'Centro','horarios':{'seg':[['09:00','18:00']]}});assert r.status_code==201,r.text
   uid=r.json()['id']
   for method,body in [('GET',None),('PATCH',{'nome':'errado'}),('DELETE',None)]:
    assert (await h.request(method,root.replace('alpha','bravo')+'/'+uid,json=body)).status_code==404
   assert (await h.patch(root+'/'+uid,json={'horarios':{'seg':[['25:00','18:00']]}})).status_code==400
   assert len(await pacote(c,'alpha','UTC'))==1 and await pacote(c,'bravo','UTC')==[]
   assert (await h.delete(root+'/'+uid)).json()['ativo'] is False
   assert await pacote(c,'alpha','UTC')==[]
 banco(caso)
