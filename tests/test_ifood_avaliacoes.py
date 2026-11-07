"""IFOOD1-b3 (docs/ESPEC_IFOOD1.md): avaliações do iFood e resposta pelo painel (gestor e acima), simulada enquanto IFOOD_RESPOSTAS não
for live. iFood simulado."""
import json
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_ifood1a import RedisSimples,SEGREDO,GESTOR,SUP
from test_ifood1b import IfoodFinanceiro

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
T='bykoji'


class IfoodAvaliacoes(IfoodFinanceiro):
    def __init__(self):
        super().__init__();self.reviews=[];self.respostas=[];self.resumo={'totalReviewsCount':3,'validReviewsCount':2,'score':4.3};self.status_resposta=201
    def __call__(self,req):
        p=req.url.path
        if p.startswith('/review/v2.0/') and req.headers.get('authorization','').removeprefix('Bearer ') in self.validos:
            if p.endswith('/summary'): return httpx.Response(200,json=self.resumo) if self.resumo else httpx.Response(404,json={'status':404})
            if p.endswith('/answers'):
                self.respostas.append((p.split('/')[-2],json.loads(req.content)['text']))
                return httpx.Response(self.status_resposta,json={'reviewId':p.split('/')[-2],'text':'x','createdAt':'2026-01-01T00:00:00Z'})
            q=req.url.params;assert int(q['pageSize'])<=50
            pg=int(q['page']);fatia=self.reviews[(pg-1)*2:pg*2]
            return httpx.Response(200,json={'page':pg,'size':2,'reviews':fatia,'total':len(self.reviews),'pageCount':max(1,-(-len(self.reviews)//2))})
        return super().__call__(req)


def review(i,nota,status,comentario=None,horas=1):
    return {'id':f'rev-{i}','status':status,'visibility':'PUBLIC','version':'v2','score':nota,'comment':comentario,'customerName':'Cliente Teste',
            'createdAt':(datetime.now(timezone.utc)-timedelta(hours=horas)).isoformat().replace('+00:00','Z'),'replies':[],
            'order':{'id':f'ped-{i}','shortId':f'{1000+i}','createdAt':'2026-01-01T00:00:00Z'},
            'questions':[{'id':'q1','type':'CHOICE','title':'Do que você gostou?','answers':[{'id':'a1','title':'Entrega'}]}]}


def test_avaliacoes_e_resposta(monkeypatch):
    monkeypatch.setenv('IFOOD_CLIENT_ID','app-teste');monkeypatch.setenv('IFOOD_CLIENT_SECRET',SEGREDO);monkeypatch.delenv('IFOOD_RESPOSTAS',raising=False)
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);falso=IfoodAvaliacoes()
        app.state.redis=RedisSimples();app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(falso))
        falso.reviews=[review(1,2,'NOT_REPLIED','Comida chegou fria',horas=100),review(2,5,'PUBLISHED'),review(3,3,'NOT_REPLIED','Demorou',horas=2),
                       review(4,4,'PUBLISHED','Bom')]
        async with client:
            assert (await client.put(f'/admin/v1/tenants/{T}/ifood/lojas/loja-a-1',json={})).status_code==200
            q=f'/admin/v1/tenants/{T}/ifood/avaliacoes'
            assert (await client.get(q,headers=SUP)).status_code==403
            # Primeira vez coleta depois da resposta (2 páginas) e o resumo do iFood.
            await client.get(q,headers=GESTOR)
            assert await conn.fetchval('SELECT count(*) FROM ifood_avaliacoes')==4
            r=(await client.get(q,headers=GESTOR)).json()
            assert r['lojas'][0]['nota_ifood']==4.3 and r['lojas'][0]['total_ifood']==3 and r['respostas_ligadas'] is False
            assert r['periodo_resumo']=={'media':3.5,'total':4,'com_comentario':3,'aguardando_resposta':2,'vencendo_24h':1,'notas':{'1':0,'2':1,'3':1,'4':1,'5':1}}
            i={x['review_id']:x for x in r['itens']}
            assert i['rev-1']['pode_responder'] and i['rev-1']['pedido_numero']=='1001' and i['rev-1']['perguntas'][0]['respostas']==['Entrega']
            assert not i['rev-2']['pode_responder'] and i['rev-2']['responder_ate'] is None
            assert [x['review_id'] for x in (await client.get(q+'?status=NOT_REPLIED&nota=2',headers=GESTOR)).json()['itens']]==['rev-1']
            # Responder: só gestor, 10 a 300 caracteres, só aguardando resposta; sem IFOOD_RESPOSTAS=live fica simulada (nada sai).
            rq=q+'/rev-1/responder'
            assert (await client.post(rq,json={'texto':'Sentimos muito pelo ocorrido.'},headers=SUP)).status_code==403
            for corpo in ({'texto':'curto'},{'texto':'x'*301},{'texto':'Sentimos muito.','extra':1},{}):
                assert (await client.post(rq,json=corpo,headers=GESTOR)).status_code==400,corpo
            assert (await client.post(q+'/rev-2/responder',json={'texto':'Obrigado pela avaliação!'},headers=GESTOR)).status_code==409
            r=await client.post(rq,json={'texto':'Sentimos muito pelo ocorrido.'},headers=GESTOR)
            assert r.json()=={'ok':True,'enviada':False,'simulada':True} and falso.respostas==[]
            assert (await client.post(rq,json={'texto':'Outra resposta qualquer.'},headers=GESTOR)).status_code==409
            a=await conn.fetchrow("SELECT actor,dados FROM audit_log WHERE acao='ifood.avaliacao_respondida'")
            assert a['actor']=='gestor@exemplo.test' and '"simulada": true' in a['dados'] and 'Sentimos' not in a['dados']
            # Ligado: vai para o iFood e a avaliação passa a respondida.
            monkeypatch.setenv('IFOOD_RESPOSTAS','live')
            r=await client.post(q+'/rev-3/responder',json={'texto':'Obrigado, vamos melhorar o tempo.'},headers=GESTOR)
            assert r.json()=={'ok':True,'enviada':True,'simulada':False} and falso.respostas==[('rev-3','Obrigado, vamos melhorar o tempo.')]
            assert await conn.fetchval("SELECT status FROM ifood_avaliacoes WHERE review_id='rev-3'")=='REPLIED'
            # iFood recusa (fora do prazo / conteúdo): nada gravado.
            await conn.execute("UPDATE ifood_avaliacoes SET status='NOT_REPLIED',resposta_texto=NULL WHERE review_id='rev-1'")
            falso.status_resposta=409
            assert (await client.post(rq,json={'texto':'Sentimos muito pelo ocorrido.'},headers=GESTOR)).status_code==409
            falso.status_resposta=422
            r=await client.post(rq,json={'texto':'Sentimos muito pelo ocorrido.'},headers=GESTOR)
            assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(400,'conteudo_recusado')
            assert await conn.fetchval("SELECT resposta_texto FROM ifood_avaliacoes WHERE review_id='rev-1'") is None
            await app.state.http.aclose()
    banco(cenario)


class IfoodIndicadores(IfoodAvaliacoes):
    def __init__(self):
        super().__init__();self.kpis=0
    def __call__(self,req):
        p=req.url.path
        if p.endswith('/orders/kpis') and req.headers.get('authorization','').removeprefix('Bearer ') in self.validos:
            if p in self.forcar: return httpx.Response(self.forcar[p])
            self.kpis+=1;c=json.loads(req.content);agg=c['agg']
            assert c['filter']['referenceDate']['gte'] and len(agg.get('dateIntervals',[0,0]))>=2
            if 'dateIntervals' in agg:
                a,b=agg['dateIntervals']
                return httpx.Response(200,json={'data':[{'dateRangeKey':{'count':10,'value':{'from':a['from'],'to':a['to']}},'gmv':{'sum':500.0,'avg':50.0}},
                                                        {'dateRangeKey':{'count':12,'value':{'from':b['from'],'to':b['to']}},'gmv':{'sum':660.0,'avg':55.0},
                                                         'preparationTimeInMin':{'avg':18.5}}],'currentPage':1,'size':100,'totalItems':2,'totalPages':1})
            campo=agg['groupBy']['fields'][0]
            dados=[{'groupByKey':{'count':7,'value':{campo:'FRIDAY'}},'gmv':{'sum':300.0}}] if campo=='dayOfWeek' else []
            return httpx.Response(200,json={'data':dados,'currentPage':1,'size':100,'totalItems':len(dados),'totalPages':1})
        return super().__call__(req)


def test_indicadores_do_ifood(monkeypatch):
    monkeypatch.setenv('IFOOD_CLIENT_ID','app-teste');monkeypatch.setenv('IFOOD_CLIENT_SECRET',SEGREDO)
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);falso=IfoodIndicadores()
        app.state.redis=RedisSimples();app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(falso))
        async with client:
            assert (await client.put(f'/admin/v1/tenants/{T}/ifood/lojas/loja-a-1',json={})).status_code==200
            q=f'/admin/v1/tenants/{T}/ifood/indicadores?loja=loja-a-1&de=2026-10-01&ate=2026-10-31'
            assert (await client.get(q,headers=SUP)).status_code==403
            assert (await client.get(f'/admin/v1/tenants/{T}/ifood/indicadores?loja=loja-b-2',headers=GESTOR)).status_code==404
            r=(await client.get(q,headers=GESTOR)).json()
            assert r['periodo']=={'de':'2026-10-01','ate':'2026-10-31','anterior':{'de':'2026-08-31','ate':'2026-09-30'}}
            assert r['resumo']['pedidos']==12 and r['resumo']['metricas']['gmv']=={'sum':660.0,'avg':55.0} and r['anterior']['pedidos']==10
            assert r['grupos']['dia_semana']==[{'grupo':{'dayOfWeek':'FRIDAY'},'pedidos':7,'metricas':{'gmv':{'sum':300.0}}}] and r['grupos']['hora']==[]
            # Cópia de 10 min: a segunda consulta não chama o iFood.
            n=falso.kpis;assert (await client.get(q,headers=GESTOR)).json()==r and falso.kpis==n
            falso.forcar['/analytics/v1.0/merchants/loja-a-1/orders/kpis']=403
            r=await client.get(f'/admin/v1/tenants/{T}/ifood/indicadores?loja=loja-a-1&de=2026-09-01&ate=2026-09-30',headers=GESTOR)
            assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(502,'sem_permissao')
            await app.state.http.aclose()
    banco(cenario)
