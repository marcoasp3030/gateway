"""IFOOD1-b3 (docs/ESPEC_IFOOD1.md): avaliações do iFood (Review v2) e resposta pelo painel.

Ciclo no iFood: nota 5 ou sem comentário é publicada na hora; nota < 5 com comentário fica NOT_REPLIED por até 5 dias esperando a
resposta (10 a 300 caracteres); respondida, o cliente tem 5 dias para reagir. Coleta: a primeira busca 90 dias; as seguintes, 15 dias
(o status muda nesse prazo). Sozinha quando o painel abre e a última passou de 30 min, ou pelo botão "Atualizar" (no máximo a cada 10 min).

Responder é a única escrita no iFood (decisão 2 do Marco: gestor e acima). Enquanto IFOOD_RESPOSTAS não for "live", a resposta fica
gravada como simulada e não sai."""
import json
import os
from datetime import date,datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter,BackgroundTasks,Depends,Request
from admin.servico import auditar
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from .cliente import Ifood,ErroIfood
from .rotas import _falha,_prefixo

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
FUSO=ZoneInfo('America/Sao_Paulo')
HISTORICO_DIAS=90
REFAZER_DIAS=15
VELHA_MIN=30
INTERVALO_MIN=10
PRAZO_DIAS=5
POR_PAGINA=50
MAX_PAGINAS=40


def _quando(v):
    if not isinstance(v,str) or not v: return None
    try:
        d=datetime.fromisoformat(v.replace('Z','+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError: return None


def _t(v,n): return v[:n] if isinstance(v,str) and v else None


def avaliacao(r):
    """Linha gravada a partir de uma avaliação do iFood; None sem id."""
    if not isinstance(r,dict) or not isinstance(r.get('id'),str): return None
    pedido=r.get('order') if isinstance(r.get('order'),dict) else {}
    nota=r.get('score')
    respostas=[{'texto':_t(x.get('text'),300),'de':_t(x.get('from'),20),'criado_em':x.get('createdAt') if isinstance(x.get('createdAt'),str) else None}
               for x in (r.get('replies') or []) if isinstance(x,dict)]
    perguntas=[{'titulo':_t(q.get('title'),200),'respostas':[_t(a.get('title'),200) for a in (q.get('answers') or []) if isinstance(a,dict)]}
               for q in (r.get('questions') or []) if isinstance(q,dict)]
    return {'review_id':r['id'][:100],'status':_t(r.get('status'),40),'visibilidade':_t(r.get('visibility'),20),
            'nota':round(float(nota),1) if isinstance(nota,(int,float)) and not isinstance(nota,bool) else None,
            'comentario':_t(r.get('comment'),4000),'cliente_nome':_t(r.get('customerName'),200),'criado_em':_quando(r.get('createdAt')),
            'pedido_id':_t(pedido.get('id'),100),'pedido_numero':_t(str(pedido['shortId']) if pedido.get('shortId') is not None else None,40),
            'respostas':respostas,'perguntas':perguntas}


async def _gravar(db,tenant,merchant,a):
    await db.execute("""INSERT INTO ifood_avaliacoes(merchant_id,review_id,tenant_id,status,visibilidade,nota,comentario,cliente_nome,criado_em,pedido_id,
            pedido_numero,respostas,perguntas) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb)
        ON CONFLICT (merchant_id,review_id) DO UPDATE SET status=EXCLUDED.status,visibilidade=EXCLUDED.visibilidade,nota=EXCLUDED.nota,
            comentario=EXCLUDED.comentario,cliente_nome=coalesce(EXCLUDED.cliente_nome,ifood_avaliacoes.cliente_nome),criado_em=EXCLUDED.criado_em,
            pedido_id=EXCLUDED.pedido_id,pedido_numero=EXCLUDED.pedido_numero,respostas=EXCLUDED.respostas,
            perguntas=CASE WHEN EXCLUDED.perguntas='[]'::jsonb THEN ifood_avaliacoes.perguntas ELSE EXCLUDED.perguntas END,atualizado_em=now()""",
        merchant,a['review_id'],tenant,a['status'],a['visibilidade'],a['nota'],a['comentario'],a['cliente_nome'],a['criado_em'],a['pedido_id'],
        a['pedido_numero'],json.dumps(a['respostas'],ensure_ascii=False),json.dumps(a['perguntas'],ensure_ascii=False))


async def coletar(estado,prefixo,tenant,merchant):
    """None se deu certo; senão 'em_andamento' ou o motivo do iFood."""
    db=estado.db
    await db.execute("INSERT INTO ifood_coletas(merchant_id,area,tenant_id) VALUES ($1,'avaliacoes',$2) ON CONFLICT DO NOTHING",merchant,tenant)
    c=await db.fetchrow(f"""UPDATE ifood_coletas SET iniciada_em=now() WHERE merchant_id=$1 AND area='avaliacoes'
        AND (iniciada_em IS NULL OR iniciada_em<now()-interval '{INTERVALO_MIN} minutes')
        AND (sucesso_em IS NULL OR sucesso_em<now()-interval '{INTERVALO_MIN} minutes') RETURNING coletado_desde""",merchant)
    if not c: return 'em_andamento'
    agora=datetime.now(timezone.utc)
    desde=(agora-timedelta(days=REFAZER_DIAS if c['coletado_desde'] else HISTORICO_DIAS)).replace(microsecond=0)
    try:
        cli=await Ifood(estado.http,estado.redis,estado.cofre,prefixo).carregar(db)
        if not cli.configurado: raise ErroIfood('nao_configurado')
        for pagina in range(1,MAX_PAGINAS+1):
            d=await cli.chamar('GET',f'/review/v2.0/merchants/{merchant}/reviews',params={'page':pagina,'pageSize':POR_PAGINA,
                               'dateFrom':desde.strftime('%Y-%m-%dT%H:%M:%SZ'),'dateTo':agora.strftime('%Y-%m-%dT%H:%M:%SZ')})
            lista=(d or {}).get('reviews') if isinstance(d,dict) else None
            if not isinstance(lista,list): raise ErroIfood('formato_inesperado')
            for r in lista:
                a=avaliacao(r)
                if a: await _gravar(db,tenant,merchant,a)
            paginas=d.get('pageCount') if isinstance(d.get('pageCount'),int) else None
            if not lista or (paginas is not None and pagina>=paginas) or (paginas is None and len(lista)<POR_PAGINA): break
        try:
            s=await cli.chamar('GET',f'/review/v2.0/merchants/{merchant}/summary')
        except ErroIfood as e:
            if e.motivo!='nao_encontrado': raise
            s=None
        if isinstance(s,dict):
            await db.execute("""INSERT INTO ifood_avaliacoes_resumo(merchant_id,tenant_id,nota,total,validas) VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (merchant_id) DO UPDATE SET nota=EXCLUDED.nota,total=EXCLUDED.total,validas=EXCLUDED.validas,atualizado_em=now()""",
                merchant,tenant,s.get('score') if isinstance(s.get('score'),(int,float)) else None,
                s.get('totalReviewsCount') if isinstance(s.get('totalReviewsCount'),int) else None,
                s.get('validReviewsCount') if isinstance(s.get('validReviewsCount'),int) else None)
    except ErroIfood as e:
        await db.execute("UPDATE ifood_coletas SET iniciada_em=NULL,erro=$2 WHERE merchant_id=$1 AND area='avaliacoes'",merchant,e.motivo)
        return e.motivo
    await db.execute("""UPDATE ifood_coletas SET iniciada_em=NULL,erro=NULL,sucesso_em=now(),coletado_desde=LEAST(coalesce(coletado_desde,$2),$2)
        WHERE merchant_id=$1 AND area='avaliacoes'""",merchant,desde.date())
    return None


async def coletar_empresa(estado,prefixo,tenant):
    for m in await estado.db.fetch('SELECT merchant_id FROM ifood_lojas WHERE tenant_id=$1 AND ativo',tenant):
        await coletar(estado,prefixo,tenant,m['merchant_id'])


def _item(r):
    prazo=r['criado_em']+timedelta(days=PRAZO_DIAS) if r['criado_em'] and r['status']=='NOT_REPLIED' else None
    return {'review_id':r['review_id'],'loja':r['merchant_id'],'status':r['status'],'visibilidade':r['visibilidade'],
            'nota':float(r['nota']) if r['nota'] is not None else None,'comentario':r['comentario'],'cliente_nome':r['cliente_nome'],
            'criado_em':r['criado_em'],'pedido_id':r['pedido_id'],'pedido_numero':r['pedido_numero'],
            'respostas':json.loads(r['respostas']) if isinstance(r['respostas'],str) else r['respostas'],
            'perguntas':json.loads(r['perguntas']) if isinstance(r['perguntas'],str) else r['perguntas'],
            'pode_responder':r['status']=='NOT_REPLIED' and r['resposta_texto'] is None,'responder_ate':prazo,
            'resposta_painel':None if r['resposta_texto'] is None else {'texto':r['resposta_texto'],'por':r['respondida_por'],'em':r['respondida_em'],
                                                                       'simulada':r['resposta_simulada']}}


def _dia(d,fim=False): return datetime.combine(d+timedelta(days=1) if fim else d,time(),FUSO)


@router.get('/tenants/{tenant_id}/ifood/avaliacoes')
async def lista(request:Request,tenant_id:str,tarefas:BackgroundTasks,de:date|None=None,ate:date|None=None,loja:str|None=None,
                status:str|None=None,nota:int|None=None,limite:int=50,antes:datetime|None=None):
    """Resumo do iFood por loja (nota, total, válidas), números do período (média, notas de 1 a 5, aguardando resposta, vencendo em
    24 h) e a lista, da mais nova para a mais antiga. Padrão: 30 dias."""
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): return _falha(404,'NOT_FOUND')
    if not 1<=limite<=100 or (nota is not None and not 1<=nota<=5) or (antes is not None and antes.tzinfo is None): return _falha(400,'formato','limite')
    hoje=datetime.now(FUSO).date();ate=ate or hoje;de=de or ate-timedelta(days=29)
    if de>ate: return _falha(400,'formato','periodo')
    coletas=[dict(r) for r in await db.fetch("""SELECT l.merchant_id,l.nome,c.sucesso_em,c.erro,c.iniciada_em IS NOT NULL AS coletando,
            s.nota,s.total,s.validas FROM ifood_lojas l LEFT JOIN ifood_coletas c ON c.merchant_id=l.merchant_id AND c.area='avaliacoes'
        LEFT JOIN ifood_avaliacoes_resumo s ON s.merchant_id=l.merchant_id WHERE l.tenant_id=$1 AND l.ativo ORDER BY l.merchant_id""",tenant_id)]
    agora=datetime.now(timezone.utc)
    if any(not c['coletando'] and (c['sucesso_em'] is None or agora-c['sucesso_em']>timedelta(minutes=VELHA_MIN)) for c in coletas):
        tarefas.add_task(coletar_empresa,request.app.state,_prefixo(request),tenant_id)
    args=(tenant_id,_dia(de),_dia(ate,True),loja)
    filtro='WHERE tenant_id=$1 AND criado_em>=$2 AND criado_em<$3 AND ($4::text IS NULL OR merchant_id=$4)'
    p=await db.fetchrow(f"""SELECT avg(nota) AS media,count(*) AS total,count(*) FILTER (WHERE status='NOT_REPLIED' AND resposta_texto IS NULL) AS aguardando,
        count(*) FILTER (WHERE status='NOT_REPLIED' AND resposta_texto IS NULL AND criado_em<now()-interval '{PRAZO_DIAS-1} days') AS vencendo,
        count(*) FILTER (WHERE comentario IS NOT NULL) AS com_comentario FROM ifood_avaliacoes {filtro}""",*args)
    notas={int(r['n']):r['q'] for r in await db.fetch(f'SELECT round(nota)::int AS n,count(*) AS q FROM ifood_avaliacoes {filtro} AND nota IS NOT NULL GROUP BY 1',*args)}
    rows=await db.fetch(f"""SELECT * FROM ifood_avaliacoes {filtro} AND ($5::text[] IS NULL OR status=ANY($5)) AND ($6::int IS NULL OR round(nota)=$6)
        AND ($7::timestamptz IS NULL OR criado_em<$7) ORDER BY criado_em DESC,review_id LIMIT $8""",
        *args,[s for s in (status or '').split(',') if s] or None,nota,antes,limite+1)
    return {'periodo':{'de':de,'ate':ate},
            'lojas':[{'loja':c['merchant_id'],'nome':c['nome'],'nota_ifood':float(c['nota']) if c['nota'] is not None else None,'total_ifood':c['total'],
                      'validas_ifood':c['validas'],'sucesso_em':c['sucesso_em'],'erro':c['erro'],'coletando':c['coletando']} for c in coletas],
            'periodo_resumo':{'media':round(float(p['media']),2) if p['media'] is not None else None,'total':p['total'],'com_comentario':p['com_comentario'],
                              'aguardando_resposta':p['aguardando'],'vencendo_24h':p['vencendo'],'notas':{str(n):notas.get(n,0) for n in range(1,6)}},
            'respostas_ligadas':os.getenv('IFOOD_RESPOSTAS','').strip().lower()=='live',
            'itens':[_item(r) for r in rows[:limite]],'proximo_antes':rows[limite-1]['criado_em'] if len(rows)>limite else None}


@router.post('/tenants/{tenant_id}/ifood/avaliacoes/atualizar')
async def atualizar(request:Request,tenant_id:str,tarefas:BackgroundTasks):
    db=request.app.state.db
    if not await Repositorio(db).tenant(tenant_id): return _falha(404,'NOT_FOUND')
    n=await db.fetchval('SELECT count(*) FROM ifood_lojas WHERE tenant_id=$1 AND ativo',tenant_id)
    if n: tarefas.add_task(coletar_empresa,request.app.state,_prefixo(request),tenant_id)
    return {'coletando':n}


@router.post('/tenants/{tenant_id}/ifood/avaliacoes/{review_id}/responder')
async def responder(request:Request,tenant_id:str,review_id:str,dados:dict,actor=Depends(painel)):
    """{"texto"}: 10 a 300 caracteres, só avaliação aguardando resposta. Com IFOOD_RESPOSTAS=live vai para o iFood; senão fica simulada."""
    db=request.app.state.db
    texto=dados.get('texto') if set(dados)=={'texto'} else None
    if not isinstance(texto,str) or not 10<=len(texto.strip())<=300: return _falha(400,'formato','texto')
    texto=texto.strip()
    r=await db.fetchrow('SELECT * FROM ifood_avaliacoes WHERE tenant_id=$1 AND review_id=$2',tenant_id,review_id)
    if not r: return _falha(404,'NOT_FOUND')
    if r['status']!='NOT_REPLIED' or r['resposta_texto'] is not None: return _falha(409,'nao_aguarda_resposta','status')
    real=os.getenv('IFOOD_RESPOSTAS','').strip().lower()=='live'
    if real:
        try:
            cli=await Ifood(request.app.state.http,request.app.state.redis,request.app.state.cofre,_prefixo(request)).carregar(db)
            await cli.chamar('POST',f'/review/v2.0/merchants/{r["merchant_id"]}/reviews/{review_id}/answers',json={'text':texto})
        except ErroIfood as e:
            # 409: o iFood já não aceita (respondida, publicada ou fora do prazo); 400/422 (recusado): tamanho ou conteúdo.
            if e.status==409: return _falha(409,'nao_aguarda_resposta','status')
            if e.motivo=='recusado': return _falha(400,'conteudo_recusado','texto')
            if e.motivo=='nao_configurado': return _falha(503,e.motivo)
            return _falha(502,e.motivo)
    async with db.acquire() as c,c.transaction():
        await c.execute("""UPDATE ifood_avaliacoes SET resposta_texto=$3,respondida_por=$4,respondida_em=now(),resposta_simulada=$5,
            status=CASE WHEN $5 THEN status ELSE 'REPLIED' END,atualizado_em=now() WHERE tenant_id=$1 AND review_id=$2""",tenant_id,review_id,texto,actor,not real)
        # Na auditoria, a avaliação e se foi simulada; o texto fica na própria avaliação.
        await auditar(c,tenant_id,actor,'ifood.avaliacao_respondida',review_id,{'loja':r['merchant_id'],'simulada':not real})
    return {'ok':True,'enviada':real,'simulada':not real}
