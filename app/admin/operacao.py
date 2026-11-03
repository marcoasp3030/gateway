"""Busca dentro da conversa e painel da operação. Os tempos só existem a partir da migração 058: antes dela não se guardava
quem respondeu nem quanto a conversa esperou."""
from datetime import datetime,timedelta,timezone
from fastapi import APIRouter,Depends,HTTPException,Request
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
RESULTADOS=50


def _trecho(texto,termo,largura=70):
    """Pedaço do texto em volta do achado. O índice é o da forma sem acento, que tem o mesmo tamanho no caso comum."""
    i=texto.lower().find(termo.lower())
    if i<0 or len(texto)<=largura*2: return texto[:largura*2]
    inicio=max(0,i-largura//2)
    return ('…' if inicio else '')+texto[inicio:inicio+largura*2]+('…' if inicio+largura*2<len(texto) else '')


@router.get('/tenants/{tenant}/conversas/{conv}/busca')
async def buscar(request:Request,tenant:str,conv:str,q:str):
    q=q.strip()
    if not 2<=len(q)<=100: raise HTTPException(400,{'campo':'q'})
    db=request.app.state.db
    if not await Repositorio(db).obter(tenant,'conversas',conv): raise HTTPException(404,'NOT_FOUND')
    # public.unaccent: a extensão fica em public; nos testes o search_path é só o schema isolado.
    mensagens=await db.fetch('''SELECT id,conteudo,enviada_em,origem FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2
        AND strpos(lower(public.unaccent(conteudo)),lower(public.unaccent($3)))>0 ORDER BY id DESC LIMIT $4''',tenant,conv,q,RESULTADOS)
    notas=await db.fetch('''SELECT id,texto AS conteudo,criado_em AS enviada_em,autor FROM conversa_notas WHERE tenant_id=$1 AND conversation_id=$2
        AND strpos(lower(public.unaccent(texto)),lower(public.unaccent($3)))>0 ORDER BY id DESC LIMIT $4''',tenant,conv,q,RESULTADOS)
    itens=[{'tipo':'mensagem','id':r['id'],'trecho':_trecho(r['conteudo'],q),'em':r['enviada_em'].isoformat(),'origem':r['origem']} for r in mensagens]
    itens+=[{'tipo':'nota','id':r['id'],'trecho':_trecho(r['conteudo'],q),'em':r['enviada_em'].isoformat(),'origem':'nota'} for r in notas]
    itens.sort(key=lambda i:i['em'],reverse=True)
    return {'itens':itens[:RESULTADOS],'limitado':len(mensagens)==RESULTADOS or len(notas)==RESULTADOS}


def _seg(v): return None if v is None else round(float(v))


@router.get('/tenants/{tenant}/operacao')
async def operacao(request:Request,tenant:str,dias:int=7):
    if dias not in (1,7,30): raise HTTPException(400,{'campo':'dias'})
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant): raise HTTPException(404,'NOT_FOUND')
    desde=datetime.now(timezone.utc)-timedelta(days=dias)
    agora=await db.fetchrow("""SELECT count(*) FILTER (WHERE aguardando_atendente_desde IS NOT NULL AND encerrada_em IS NULL) AS fila,
        min(aguardando_atendente_desde) FILTER (WHERE encerrada_em IS NULL) AS mais_antiga,
        count(*) FILTER (WHERE atribuida_a IS NOT NULL AND bot_pausado_ate>now() AND encerrada_em IS NULL) AS com_atendente
        FROM conversas WHERE tenant_id=$1""",tenant)
    # Tempo de resposta: da última fala do cliente até a mensagem da pessoa que veio logo depois.
    respostas=await db.fetch("""WITH m AS (SELECT conversation_id,autor,origem,enviada_em,
            lag(direcao) OVER w AS ant_direcao,lag(enviada_em) OVER w AS ant_em
            FROM mensagens WHERE tenant_id=$1 AND enviada_em>=$2 WINDOW w AS (PARTITION BY conversation_id ORDER BY id))
        SELECT autor,count(*) AS mensagens,count(DISTINCT conversation_id) AS conversas,
            percentile_cont(0.5) WITHIN GROUP(ORDER BY extract(epoch FROM enviada_em-ant_em)) FILTER (WHERE ant_direcao='entrada') AS p50,
            percentile_cont(0.95) WITHIN GROUP(ORDER BY extract(epoch FROM enviada_em-ant_em)) FILTER (WHERE ant_direcao='entrada') AS p95
        FROM m WHERE origem='humano' AND autor IS NOT NULL GROUP BY GROUPING SETS ((autor),())""",tenant,desde)
    esperas=await db.fetch("""SELECT atendente,count(*) AS esperas,
            percentile_cont(0.5) WITHIN GROUP(ORDER BY extract(epoch FROM fim-inicio)) AS p50,
            percentile_cont(0.95) WITHIN GROUP(ORDER BY extract(epoch FROM fim-inicio)) AS p95
        FROM conversa_esperas WHERE tenant_id=$1 AND fim>=$2 GROUP BY GROUPING SETS ((atendente),())""",tenant,desde)
    abertas={r['atribuida_a']:r['n'] for r in await db.fetch("""SELECT atribuida_a,count(*) AS n FROM conversas WHERE tenant_id=$1 AND atribuida_a IS NOT NULL
        AND bot_pausado_ate>now() AND encerrada_em IS NULL GROUP BY 1""",tenant)}
    # GROUPING SETS: a linha com autor nulo é o total. Espera devolvida ao agente (atendente nulo) entra só no total.
    total_r=next((r for r in respostas if r['autor'] is None),None)
    grupos=[g for g in esperas if g['atendente'] is None]
    total_e=grupos[-1] if grupos else None
    pessoas={r['autor']:{'atendente':r['autor'],'mensagens':r['mensagens'],'conversas':r['conversas'],'resposta_p50_s':_seg(r['p50']),'esperas_assumidas':0}
             for r in respostas if r['autor']}
    for g in esperas:
        if g['atendente']:
            p=pessoas.setdefault(g['atendente'],{'atendente':g['atendente'],'mensagens':0,'conversas':0,'resposta_p50_s':None,'esperas_assumidas':0})
            p['esperas_assumidas']=g['esperas']
    for autor,n in abertas.items():
        pessoas.setdefault(autor,{'atendente':autor,'mensagens':0,'conversas':0,'resposta_p50_s':None,'esperas_assumidas':0})
    return {'dias':dias,
            'agora':{'fila':agora['fila'],'mais_antiga':agora['mais_antiga'].isoformat() if agora['mais_antiga'] else None,'com_atendente':agora['com_atendente']},
            'periodo':{'mensagens':total_r['mensagens'] if total_r else 0,'conversas':total_r['conversas'] if total_r else 0,
                       'resposta_p50_s':_seg(total_r['p50']) if total_r else None,'resposta_p95_s':_seg(total_r['p95']) if total_r else None,
                       'esperas':total_e['esperas'] if total_e else 0,'espera_p50_s':_seg(total_e['p50']) if total_e else None,'espera_p95_s':_seg(total_e['p95']) if total_e else None},
            'por_atendente':sorted(({**p,'abertas_agora':abertas.get(k,0)} for k,p in pessoas.items()),key=lambda p:(-p['mensagens'],p['atendente']))}
