import asyncio,importlib.util,json
from pathlib import Path
import httpx
spec=importlib.util.spec_from_file_location('bench',Path(__file__).resolve().parents[1]/'scripts/bench.py')
bench=importlib.util.module_from_spec(spec);spec.loader.exec_module(bench)

def test_bench_multitenant_novas_rotas_e_erro_de_metrica():
 async def caso():
  mensagens={};metricas={};cont={};erro=False
  def responder(req):
   path=req.url.path;parts=path.split('/');t=parts[4];ag=t+'ag'
   if path.endswith('/agentes/'+ag): return httpx.Response(200,json={'configuracao_efetiva':{'status':'ativo','agente_mode':'mock','send_mode':'dry','modo_piloto':False}})
   if path.endswith('/canais'): return httpx.Response(200,json={'itens':[{'id':t+'can','agente_id':ag,'tipo':'api','ativo':True}]})
   if path.endswith('/metricas'):
    return httpx.Response(200,json={'turnos':metricas.get(t,0),'erros':int(erro and cont.get(t,0)>0)})
   if path.endswith('/simular'):
    body=json.loads(req.content);conv=t+':'+body['canal_id']+':'+body['identificador'];lst=mensagens.setdefault(conv,[])
    lst.append({'origem':'cliente','conteudo':body['texto']})
    if len(lst)==2:
     lst.append({'origem':'bot','conteudo':'ok'});metricas[t]=metricas.get(t,0)+1;cont[t]=1
    return httpx.Response(200,json={'conversation_id':conv})
   if path.endswith('/mensagens'): return httpx.Response(200,json={'itens':mensagens[parts[6]]})
   raise AssertionError(path)
  async with httpx.AsyncClient(base_url='http://teste',transport=httpx.MockTransport(responder)) as c:
   r=await bench.executar(c,[('alpha','alphaag',None),('bravo','bravoag',None)],1,2)
   assert r['respondidos']==2 and r['erros']==0 and len(r['metricas'])==2
   # Falha explícita de transporte de uma mensagem deve aparecer na contagem.
   def falhar(req):
    if req.method=='POST': return httpx.Response(503)
    return responder(req)
   async with httpx.AsyncClient(base_url='http://teste',transport=httpx.MockTransport(falhar)) as c2:
    r=await bench.executar(c2,[('alpha','alphaag',None)],1,2)
    assert r['respondidos']==0 and r['erros']>0
 asyncio.run(caso())
