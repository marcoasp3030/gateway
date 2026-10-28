"""Teste de carga e de falhas da fila durável, somente na pilha isolada (docker-compose.carga.yml).

Cria empresas de carga com agente mock/dry, dispara N conversas ao mesmo tempo pela
simulação administrativa e confere no banco da pilha isolada: espera na fila, perda
e duplicidade. Nunca chama o Skyone nem o canal: recusa agente que não seja mock/dry
e só roda com CARGA_ISOLADA=1 (definido pelo scripts/carga_fila.sh).
"""
import argparse,asyncio,json,os,sys,time,uuid
import asyncpg,httpx

BASE='/admin/v1/tenants'


def aviso(*a): print(*a,file=sys.stderr,flush=True)


async def pedir(c,metodo,url,ok=(200,201),**kw):
    r=await c.request(metodo,url,**kw)
    if r.status_code not in ok: raise ValueError(f'HTTP {r.status_code} em {metodo} {url}')
    return r.json()


async def preparar(c,tenant,debounce,limite=100):
    """Empresa, agente mock/dry e canal API de carga (reaproveita se já existirem)."""
    if (await c.get(f'{BASE}/{tenant}')).status_code==404:
        await pedir(c,'POST',BASE,json={'id':tenant,'nome':'[carga] '+tenant,'tenant_teste':True})
    agentes=(await pedir(c,'GET',f'{BASE}/{tenant}/agentes'))['itens']
    agente=agentes[0]['id'] if agentes else (await pedir(c,'POST',f'{BASE}/{tenant}/agentes',
        json={'nome':'[carga] agente simulado','modelo_id':'assistente_generico'}))['id']
    # Limites por empresa/agente no teto: quem limita é a capacidade dos workers.
    await pedir(c,'PATCH',f'{BASE}/{tenant}/agentes/{agente}',json={'status':'ativo','modo_piloto':False,
        'agente_mode':'mock','send_mode':'dry','debounce_s':debounce,'max_wait_s':debounce,
        'max_lotes_simultaneos':limite,'max_concorrencia':limite})
    cfg=(await pedir(c,'GET',f'{BASE}/{tenant}/agentes/{agente}'))['configuracao_efetiva']
    if cfg['agente_mode']!='mock' or cfg['send_mode']!='dry': raise ValueError('Carga exige agente mock/dry')
    canais=[k for k in (await pedir(c,'GET',f'{BASE}/{tenant}/canais',params={'limite':99}))['itens']
            if k['agente_id']==agente and k['tipo']=='api']
    canal=canais[0]['id'] if canais else (await pedir(c,'POST',f'{BASE}/{tenant}/canais',
        json={'nome':'[carga] canal','tipo':'api','agente_id':agente}))['id']
    await pedir(c,'PATCH',f'{BASE}/{tenant}/canais/{canal}',json={'ativo':True})
    return agente,canal


async def enviar(c,sem,tenant,agente,canal,ident,limite):
    """Como um provedor: se a recepção falhar, reenvia a MESMA mensagem (mesmo message_id)."""
    corpo={'canal_id':canal,'identificador':ident,'texto':'carga '+ident,'message_id':ident+'-1'}
    fim=time.monotonic()+limite;tentativas=0
    async with sem:
        while True:
            tentativas+=1
            try:
                r=await c.post(f'{BASE}/{tenant}/agentes/{agente}/simular',json=corpo)
                if r.status_code==200: return r.json()['conversation_id'],tentativas
            except httpx.HTTPError: pass
            if time.monotonic()>fim: raise ValueError('Recepção indisponível além do limite')
            await asyncio.sleep(1)


RELATORIO='''
WITH alvo AS (SELECT unnest($1::text[]) AS conversation_id),
t AS (SELECT u.* FROM turnos u JOIN alvo USING (conversation_id)),
m AS (SELECT x.* FROM mensagens x JOIN alvo USING (conversation_id))
SELECT
 (SELECT count(*) FROM alvo) AS conversas,
 (SELECT count(*) FROM m WHERE direcao='entrada') AS entradas,
 (SELECT count(*) FROM m WHERE direcao='entrada' AND processada_em IS NULL) AS entradas_pendentes,
 (SELECT count(*) FROM m WHERE origem='bot') AS respostas,
 (SELECT count(*) FROM (SELECT conversation_id FROM m WHERE origem='bot' GROUP BY 1 HAVING count(*)>1) d) AS conversas_com_resposta_duplicada,
 (SELECT count(*) FROM alvo a WHERE NOT EXISTS (SELECT 1 FROM m WHERE m.conversation_id=a.conversation_id AND origem='bot')) AS conversas_sem_resposta,
 (SELECT count(*) FROM m WHERE entrega='incerta') AS partes_incertas,
 (SELECT count(*) FROM m WHERE entrega='enviando') AS partes_presas,
 (SELECT count(*) FROM t WHERE etapa='concluido') AS turnos_concluidos,
 (SELECT count(*) FROM (SELECT conversation_id FROM t WHERE etapa='concluido' GROUP BY 1 HAVING count(*)>1) d) AS conversas_com_turno_duplicado,
 (SELECT count(*) FROM t WHERE etapa='abandonado') AS turnos_refeitos,
 (SELECT count(*) FROM t WHERE etapa NOT IN ('concluido','abandonado')) AS turnos_em_aberto,
 (SELECT count(*) FROM t WHERE etapa='concluido' AND status<>'ok') AS turnos_com_erro,
 (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY espera_ms) FROM t WHERE etapa='concluido') AS espera_p50,
 (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY espera_ms) FROM t WHERE etapa='concluido') AS espera_p95,
 (SELECT max(espera_ms) FROM t WHERE etapa='concluido') AS espera_max,
 (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) FROM t WHERE etapa='concluido') AS total_p50,
 (SELECT max(total_ms) FROM t WHERE etapa='concluido') AS total_max,
 (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY agente_ms) FROM t WHERE etapa='concluido') AS agente_p50
'''


async def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://gateway:8000')
    p.add_argument('--conversas',type=int,default=200)
    p.add_argument('--tenants',type=int,default=2)
    p.add_argument('--debounce',type=int,default=1)
    p.add_argument('--timeout',type=float,default=600)
    p.add_argument('--limite',type=int,default=100,help='max_lotes_simultaneos e max_concorrencia das empresas de carga')
    p.add_argument('--rotulo',default='carga')
    a=p.parse_args()
    if os.getenv('CARGA_ISOLADA')!='1': raise SystemExit('Só roda na pilha isolada (scripts/carga_fila.sh)')
    if not 1<=a.conversas<=2000 or not 1<=a.tenants<=10: p.error('Conversas 1–2000; empresas 1–10')
    rodada=uuid.uuid4().hex[:8]
    db=await asyncpg.create_pool(os.environ['DATABASE_URL'],min_size=1,max_size=2)
    cabecalhos={'Authorization':'Bearer '+(os.environ.get('PANEL_PLATFORM_KEY') or os.environ['PANEL_SERVICE_KEY']),'X-Actor':'carga','X-Role':'plataforma'}
    async with httpx.AsyncClient(base_url=a.url,headers=cabecalhos,timeout=30,
                                 limits=httpx.Limits(max_connections=60)) as c:
        alvos=[]
        for i in range(a.tenants):
            t=f'carga_{chr(97+i)}'
            alvos.append((t,*await preparar(c,t,a.debounce,a.limite)))
        sem=asyncio.Semaphore(50);inicio=time.monotonic()
        aviso(f'[{a.rotulo}] rodada {rodada}: enviando {a.conversas} conversas em {a.tenants} empresas')
        feitos=await asyncio.gather(*(enviar(c,sem,*alvos[i%a.tenants],f'carga-{rodada}-{i:04d}',a.timeout)
                                      for i in range(a.conversas)))
        convs=[f[0] for f in feitos];recebidas_s=round(time.monotonic()-inicio,1)
        aviso(f'[{a.rotulo}] recebidas em {recebidas_s} s; aguardando o processamento')
        completo=False
        while time.monotonic()-inicio<a.timeout:
            n=await db.fetchval("SELECT count(DISTINCT conversation_id) FROM turnos WHERE etapa='concluido' AND conversation_id=ANY($1::text[])",convs)
            pend=await db.fetchval("SELECT count(*) FROM mensagens WHERE direcao='entrada' AND processada_em IS NULL AND conversation_id=ANY($1::text[])",convs)
            if n==len(set(convs)) and not pend: completo=True;break
            aviso(f'[{a.rotulo}] {int(time.monotonic()-inicio)} s: {n}/{len(convs)} concluídas, {pend} pendentes')
            await asyncio.sleep(5)
        duracao=round(time.monotonic()-inicio,1)
        await asyncio.sleep(1)
        r=dict(await db.fetchrow(RELATORIO,convs))
    await db.close()
    seg=lambda ms: None if ms is None else round(float(ms)/1000,2)
    fila=lambda ms: None if ms is None else round(max(0.0,float(ms)/1000-a.debounce),2)
    perdidas=r['conversas_sem_resposta']+r['entradas_pendentes']+(a.conversas-r['conversas'])
    duplicadas=r['conversas_com_resposta_duplicada']+r['conversas_com_turno_duplicado']
    saida={'rotulo':a.rotulo,'rodada':rodada,'conversas':a.conversas,'empresas':a.tenants,
        'completo':completo,'duracao_total_s':duracao,'recepcao_s':recebidas_s,
        'reenvios_do_provedor':sum(f[1]-1 for f in feitos),
        'perdidas':perdidas,'duplicadas':duplicadas,
        # Espera na fila = do fim do debounce até um worker começar a conversa.
        'espera_fila_s':{'mediana':fila(r['espera_p50']),'p95':fila(r['espera_p95']),'max':fila(r['espera_max'])},
        'total_ate_resposta_s':{'mediana':seg(r['total_p50']),'max':seg(r['total_max'])},
        'agente_mediana_s':seg(r['agente_p50']),
        'detalhe':{k:int(r[k]) for k in ('entradas','respostas','turnos_concluidos','turnos_refeitos',
            'turnos_em_aberto','turnos_com_erro','partes_incertas','partes_presas')}}
    print(json.dumps(saida,ensure_ascii=False,indent=2))
    return 0 if completo and not perdidas and not duplicadas and len(set(convs))==a.conversas else 1


if __name__=='__main__':
    try: raise SystemExit(asyncio.run(main()))
    except (ValueError,KeyError,httpx.HTTPError,asyncpg.PostgresError) as erro:
        # Sem detalhes do driver: podem conter DSN ou conteúdo.
        raise SystemExit('Carga recusada/falhou: '+(str(erro) if isinstance(erro,ValueError) else type(erro).__name__)) from None
