"""Benchmark da API administrativa, somente agentes mock/dry já configurados.

PANEL_SERVICE_KEY=... python scripts/bench.py --tenant bykoji --agente ag_...
Repita pares --tenant/--agente para executar alvos simultaneamente.
A chave pode vir de --panel-key, mas prefira a variável para não expor em argv.
"""
import argparse,asyncio,json,os,statistics,time,uuid
from urllib.parse import quote
import httpx


def caminho(tenant,sufixo=''):
    return '/admin/v1/tenants/'+quote(tenant,safe='')+sufixo

async def requisitar(c,metodo,url,**kw):
    r=await c.request(metodo,url,**kw)
    if not 200<=r.status_code<300:
        raise ValueError('API recusou requisição: HTTP '+str(r.status_code))
    return r.json()

async def executar(c,alvos,clientes=5,timeout=90):
    preparados=[]
    for tenant,agente,canal in alvos:
        base=caminho(tenant)
        cfg=(await requisitar(c,'GET',base+'/agentes/'+quote(agente,safe='')))['configuracao_efetiva']
        if cfg['status']!='ativo' or cfg['agente_mode']!='mock' or cfg['send_mode']!='dry' or cfg['modo_piloto']:
            raise ValueError('Benchmark exige agente ativo mock/dry e piloto desligado')
        canais=(await requisitar(c,'GET',base+'/canais',params={'limite':99}))['itens']
        candidatos=[r for r in canais if r['agente_id']==agente and r['tipo']=='api' and r['ativo'] and (not canal or r['id']==canal)]
        if len(candidatos)!=1:
            raise ValueError('Informe --canal para selecionar um único canal API ativo')
        antes=await requisitar(c,'GET',base+'/metricas',params={'agente':agente})
        preparados.append((tenant,agente,candidatos[0]['id'],antes))

    async def cliente(t,a,can):
        usuario='bench-'+uuid.uuid4().hex
        textos=['diagnostico '+usuario+' parte 1','diagnostico '+usuario+' parte 2']
        conv=None
        for i,texto in enumerate(textos):
            if i: await asyncio.sleep(.15)
            r=await requisitar(c,'POST',caminho(t)+'/agentes/'+quote(a,safe='')+'/simular',json={'canal_id':can,'identificador':usuario,'texto':texto,'message_id':usuario+'-'+str(i)})
            if r.get('ignorado'): raise ValueError('Simulação ignorada')
            conv=r['conversation_id']
        inicio=time.monotonic()
        while time.monotonic()-inicio<timeout:
            await asyncio.sleep(.3)
            msgs=(await requisitar(c,'GET',caminho(t)+'/conversas/'+quote(conv,safe='')+'/mensagens'))['itens']
            if any(m['origem']=='bot' for m in msgs):
                if [m['conteudo'] for m in msgs if m['origem']=='cliente']!=textos: raise ValueError('Histórico divergente')
                return {'tenant':t,'agente':a,'canal':can,'conversation_id':conv,'tempo_s':round(time.monotonic()-inicio,3)}
        raise ValueError('Timeout aguardando resposta')

    resultados=await asyncio.gather(*(cliente(t,a,can) for t,a,can,_ in preparados for _ in range(clientes)),return_exceptions=True)
    ok=[r for r in resultados if isinstance(r,dict)]
    await asyncio.sleep(.5)  # turno confirmado após mensagem de saída
    metricas=[]
    for t,a,can,antes in preparados:
        depois=await requisitar(c,'GET',caminho(t)+'/metricas',params={'agente':a})
        delta={k:depois[k]-antes[k] for k in ('turnos','erros')}
        metricas.append({'tenant':t,'agente':a,'delta':delta,'acumulado':depois})
    erros=len(resultados)-len(ok)
    if any(m['delta']['erros'] or m['delta']['turnos']<clientes for m in metricas): erros+=1
    tempos=[r['tempo_s'] for r in ok]
    return {'respondidos':len(ok),'esperados':len(resultados),'erros':erros,
            'p50_s':statistics.median(tempos) if tempos else None,'max_s':max(tempos) if tempos else None,
            'conversas':ok,'metricas':metricas}

async def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://127.0.0.1:8100')
    p.add_argument('--panel-key',default=os.getenv('PANEL_SERVICE_KEY'))
    p.add_argument('--tenant',action='append',required=True)
    p.add_argument('--agente',action='append',required=True)
    p.add_argument('--canal',action='append')
    p.add_argument('--clientes',type=int,default=5)
    p.add_argument('--timeout',type=float,default=90)
    a=p.parse_args()
    if not a.panel_key: p.error('Forneça PANEL_SERVICE_KEY ou --panel-key')
    if len(a.tenant)!=len(a.agente) or (a.canal and len(a.canal)!=len(a.agente)): p.error('Repita os pares tenant/agente e, se usado, canal')
    if not 1<=a.clientes<=100 or not 0<a.timeout<=600: p.error('Clientes 1–100; timeout até 600 s')
    alvos=list(zip(a.tenant,a.agente,a.canal or [None]*len(a.agente)))
    if len({(t,ag) for t,ag,can in alvos})!=len(alvos): p.error('Alvos duplicados')
    async with httpx.AsyncClient(base_url=a.url.rstrip('/'),headers={'Authorization':'Bearer '+a.panel_key,'X-Actor':'benchmark'},timeout=30,follow_redirects=False) as c:
        try: result=await executar(c,alvos,a.clientes,a.timeout)
        except (ValueError,httpx.HTTPError,KeyError): raise SystemExit('Benchmark recusado/falhou; confira configuração e disponibilidade (segredos omitidos)') from None
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['erros']==0 and result['respondidos']==result['esperados'] else 1

if __name__=='__main__': raise SystemExit(asyncio.run(main()))
