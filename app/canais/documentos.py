"""ARQ1-a1: envio de documento por API (formulário, site, sistema ou n8n) e a Skill `ler_documento` (docs/ESPEC_ARQ1.md, seção 14).

Duas credenciais (decisão 1 de 21/10):
- chave da empresa (`X-API-Key`, escopo `documentos:enviar`): para servidor/n8n; recusada quando vem de navegador;
- chave de formulário (`X-Form-Key`): só envia documento, presa a uma empresa e a um agente, com limite por minuto, tamanho,
  tipos, modos e origens (sites) próprios; revogável pelo painel. Com `Origin`, a origem precisa estar na lista da chave.

Dois modos (decisão 2): `extrair` devolve o JSON do documento, sem agente; `agente` extrai e abre ou continua a conversa no canal
`api` do agente, com o anexo no pacote. Pronto em poucos segundos → resposta síncrona; senão 202 com `anexo_id`, webhook de retorno
(`documento.pronto`/`documento.falhou`, assinado como os demais webhooks de saída) e consulta por GET."""
import asyncio
import json
import re
from datetime import datetime,timezone
from fastapi import APIRouter,Request
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse,Response
from nucleo import documentos as D
from nucleo.autenticacao import hash_chave,contexto_da_chave
from nucleo.banco import Repositorio
from admin.servico import auditar

router=APIRouter()
CONTATO=re.compile(r'[A-Za-z0-9_@+.-]{1,100}')
MAX_MENSAGEM=2000
LIMITE_FORMULARIO_PADRAO=10
LIMITE_EMPRESA_PADRAO=60
CABECALHOS={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
MENSAGENS={400:'Confira os campos informados.',401:'Credencial de acesso ausente ou inválida.',403:'Você não tem permissão para esta operação.',
           404:'Recurso não encontrado.',409:'A operação conflita com o estado atual do recurso.',413:'O arquivo excede o tamanho permitido.',
           429:'Limite de envios atingido. Aguarde e tente novamente.',503:'Serviço temporariamente indisponível.'}
CODIGOS={400:'VALIDATION_ERROR',401:'UNAUTHORIZED',403:'FORBIDDEN',404:'NOT_FOUND',409:'CONFLICT',413:'VALIDATION_ERROR',429:'RATE_LIMITED',503:'UNAVAILABLE'}


class Erro(Exception):
    def __init__(self,status,campo=None,motivo=None,**extra): self.status,self.campo,self.motivo,self.extra=status,campo,motivo,extra


def _erro(e,cors=None):
    detalhes={**({'campo':e.campo} if e.campo else {}),**({'motivo':e.motivo} if e.motivo else {}),**e.extra}
    h={**CABECALHOS,**(cors or {}),**({'Retry-After':'60'} if e.status==429 else {})}
    return JSONResponse({'erro':{'codigo':CODIGOS[e.status],'mensagem':MENSAGENS[e.status],'detalhes':detalhes}},status_code=e.status,headers=h)


def _do_navegador(request):
    """Requisição feita por navegador: ele sempre manda Origin em POST de outro site e os cabeçalhos Sec-Fetch-*."""
    return any(request.headers.get(h) for h in ('origin','sec-fetch-mode','sec-fetch-site'))


async def quem_envia(request):
    """Credencial do envio: {'tipo','tenant','chave','agente_id','actor','cors'}. Erro com o motivo seguro."""
    form,emp=request.headers.get('x-form-key',''),request.headers.get('x-api-key','')
    if form and emp: raise Erro(400,'credencial','duas_credenciais')
    if not form and not emp: raise Erro(401)
    db=request.app.state.db
    chave=await db.fetchrow('SELECT k.*,t.status AS tenant_status FROM tenant_api_keys k JOIN tenants t ON t.id=k.tenant_id WHERE k.hash=$1',
                            hash_chave(form or emp,request.app.state.infra.pepper))
    agora=datetime.now(timezone.utc)
    if not chave or chave['revogada_em'] or chave['tenant_status']!='ativo' or (chave['expira_em'] and chave['expira_em']<=agora): raise Erro(401)
    if form:
        if chave['tipo']!='formulario': raise Erro(401)
        origem=request.headers.get('origin')
        cors=None
        if origem:
            if origem.lower().rstrip('/') not in [o.lower() for o in (chave['origens_permitidas'] or [])]: raise Erro(403,'origem','origem_nao_permitida')
            cors={'Access-Control-Allow-Origin':origem,'Vary':'Origin'}
        return {'tipo':'formulario','tenant':chave['tenant_id'],'chave':chave,'agente_id':chave['agente_id'],'actor':'formulario:'+chave['id'],'cors':cors}
    if chave['tipo']!='integracao' or 'documentos:enviar' not in chave['escopos']: raise Erro(403,'escopo')
    # Nunca a chave da empresa vinda de navegador (ela daria acesso a tudo o que a chave permite).
    if _do_navegador(request): raise Erro(403,'credencial','chave_da_empresa_no_navegador')
    if chave['ips_permitidos']:
        import ipaddress
        from limites import ip_cliente
        try: ip=ipaddress.ip_address(ip_cliente(request.client.host if request.client else None,request.headers))
        except ValueError: raise Erro(403) from None
        if not any(ip in rede for rede in chave['ips_permitidos']): raise Erro(403)
    await db.execute('UPDATE tenant_api_keys SET ultimo_uso_em=now() WHERE tenant_id=$1 AND id=$2',chave['tenant_id'],chave['id'])
    return {'tipo':'integracao','tenant':chave['tenant_id'],'chave':chave,'agente_id':chave['agente_id'],'actor':'api-key:'+chave['id'],'cors':None}


async def _limite_por_minuto(db,ctx):
    k=ctx['chave'];limite=k['limite_por_minuto'] or (LIMITE_FORMULARIO_PADRAO if ctx['tipo']=='formulario' else LIMITE_EMPRESA_PADRAO)
    n=await db.fetchval("SELECT count(*) FROM anexos WHERE tenant_id=$1 AND chave_id=$2 AND criado_em>now()-interval '60 seconds'",ctx['tenant'],k['id'])
    if n>=limite: raise Erro(429,motivo='limite_por_minuto',limite=limite)


def _texto(form,campo,maximo,padrao=None):
    v=form.get(campo)
    if v is None: return padrao
    if not isinstance(v,str): raise Erro(400,campo)
    v=v.strip()
    if len(v)>maximo: raise Erro(400,campo,'longo')
    return v or padrao


async def receber(state,tenant,agente_id,dados,nome,modo,origem,chave=None,contato=None,nome_contato=None,mensagem=None,canal_id=None,
                  referencia=None,idempotencia=None,aguardar_s=D.ESPERA_PADRAO_S,simulacao=False,actor='sistema'):
    """Recebe um documento já autenticado: confere capacidade, tamanho e tipo, grava cifrado, lê (ou agenda a leitura) e, no modo agente,
    entrega à conversa do canal api. Devolve (status HTTP, corpo)."""
    db=state.db
    cfg=await db.fetchrow('SELECT id,status FROM agentes WHERE tenant_id=$1 AND id=$2',tenant,agente_id) if agente_id else None
    if cfg is None: raise Erro(404,'agente_id')
    if cfg['status']!='ativo': raise Erro(409,'agente_id','agente_inativo')
    cap=await D.capacidade(db,tenant,agente_id)
    if not cap or not cap['ativo']: raise Erro(409,'agente_id','capacidade_desligada')
    if not D.disponivel(): raise Erro(503,motivo='armazenamento_indisponivel')
    limite=min(cap['max_bytes'],(chave['max_bytes'] if chave and chave['max_bytes'] else cap['max_bytes']))
    if not dados: raise Erro(400,'arquivo','vazio')
    if len(dados)>limite: raise Erro(413,'arquivo','tamanho',max_bytes=limite)
    formato=D.formato_real(dados)
    aceitos=[t for t in cap['tipos'] if not (chave and chave['tipos_aceitos']) or t in chave['tipos_aceitos']]
    if formato is None or formato not in aceitos: raise Erro(400,'arquivo','tipo_nao_aceito',tipos=aceitos)
    canal=None
    if modo=='agente':
        if not contato or not CONTATO.fullmatch(contato): raise Erro(400,'contato')
        repo=Repositorio(db)
        if canal_id:
            canal=await repo.obter(tenant,'tenant_canais',canal_id)
            if not canal or canal['tipo']!='api' or canal['agente_id']!=agente_id: raise Erro(404,'canal_id')
        else:
            canal=await db.fetchrow("SELECT * FROM tenant_canais WHERE tenant_id=$1 AND agente_id=$2 AND tipo='api' AND ativo ORDER BY id LIMIT 1",tenant,agente_id)
            if not canal: raise Erro(409,'canal_id','sem_canal_api')
    a,novo=await D.criar(db,tenant,agente_id,dados,D.nome_seguro(nome,formato),modo,origem,chave['id'] if chave else None,referencia,idempotencia,
                         cap['retencao_dias'],formato)
    if novo:
        async with db.acquire() as conn,conn.transaction(): await auditar(conn,tenant,actor,'documento.recebido',a['id'])
        state.documentos_tarefas=getattr(state,'documentos_tarefas',set())
        tarefa=asyncio.create_task(D.processar(state,a['id']));state.documentos_tarefas.add(tarefa);tarefa.add_done_callback(state.documentos_tarefas.discard)
        if modo=='agente':
            texto=f'[anexo: {a["nome"]}]'+(f'\n{mensagem}' if mensagem else '')
            m={'identificador':contato,'texto':texto,'message_id':'doc:'+a['id'],'tipo':'documento','meta':{'anexo_id':a['id'],'nome':a['nome']},
               'nome':nome_contato,'origem_recebimento':'documentos',**({'simulacao':True} if simulacao else {})}
            r=await state.pipeline.receber(tenant,canal['id'],m)
            conversa=r.get('conversation_id') or f'{tenant}:{canal["id"]}:{contato}'
            await db.execute("""UPDATE anexos SET conversation_id=$3,mensagem_id=$4,atendimento_id=(SELECT atendimento_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$3)
                WHERE tenant_id=$1 AND id=$2 AND EXISTS(SELECT 1 FROM conversas WHERE tenant_id=$1 AND conversation_id=$3)""",tenant,a['id'],conversa,r.get('mensagem_id'))
    [a]=await D.aguardar(db,[a['id']],min(max(aguardar_s,0),D.ESPERA_MAXIMA_S) if novo else 0)
    corpo={'anexo_id':a['id'],'estado':a['estado'],'motivo':a['motivo'],'modo':a['modo'],'referencia':a['referencia'],'conversation_id':a['conversation_id'],
           'duplicado':not novo}
    if a['estado']=='processando':
        return 202,{**corpo,'consultar_em':f'/api/v1/documentos/{a["id"]}'}
    return 200,{**corpo,**D.documento(a,D.resultado(a),completo=(modo=='extrair'))}


@router.options('/api/v1/documentos')
@router.options('/api/v1/documentos/{anexo_id}')
async def preflight(request:Request,anexo_id:str|None=None):
    """CORS do formulário no navegador: só libera o cabeçalho X-Form-Key (a origem é conferida por chave no POST/GET)."""
    origem=request.headers.get('origin')
    if not origem: return Response(status_code=204)
    return Response(status_code=204,headers={'Access-Control-Allow-Origin':origem,'Vary':'Origin','Access-Control-Allow-Methods':'POST, GET',
        'Access-Control-Allow-Headers':'X-Form-Key, Content-Type','Access-Control-Max-Age':'600'})


@router.post('/api/v1/documentos')
async def enviar(request:Request):
    cors=None
    try:
        ctx=await quem_envia(request);cors=ctx['cors']
        await _limite_por_minuto(request.app.state.db,ctx)
        try: form=await request.form(max_files=1,max_fields=20)
        except Exception: raise Erro(400,'corpo','multipart_invalido') from None
        arquivo=form.get('arquivo')
        if arquivo is None or isinstance(arquivo,str): raise Erro(400,'arquivo')
        chave=ctx['chave']
        agente_id=_texto(form,'agente_id',100)
        if ctx['agente_id']:
            if agente_id and agente_id!=ctx['agente_id']: raise Erro(403,'agente_id')
            agente_id=ctx['agente_id']
        if not agente_id: raise Erro(400,'agente_id')
        modos=list(chave['modos'] or (['agente'] if ctx['tipo']=='formulario' else ['extrair','agente']))
        modo=_texto(form,'modo',10,modos[0] if ctx['tipo']=='formulario' else 'extrair')
        if modo not in ('extrair','agente'): raise Erro(400,'modo')
        if modo not in modos: raise Erro(403,'modo','modo_nao_permitido')
        try: aguardar_s=int(_texto(form,'aguardar_s',3,str(D.ESPERA_PADRAO_S)))
        except ValueError: raise Erro(400,'aguardar_s') from None
        if not 0<=aguardar_s<=D.ESPERA_MAXIMA_S: raise Erro(400,'aguardar_s')
        dados=await arquivo.read(D.LIMITE_MAXIMO+1)
        status,corpo=await receber(request.app.state,ctx['tenant'],agente_id,dados,arquivo.filename,modo,ctx['tipo'] if ctx['tipo']=='formulario' else 'api',chave,
            contato=_texto(form,'contato',100),nome_contato=_texto(form,'nome_contato',100),mensagem=_texto(form,'mensagem',MAX_MENSAGEM),
            canal_id=_texto(form,'canal_id',100),referencia=_texto(form,'referencia',100),idempotencia=_texto(form,'idempotencia',100),
            aguardar_s=aguardar_s,actor=ctx['actor'])
        return JSONResponse(jsonable_encoder(corpo),status_code=status,headers={**CABECALHOS,**(cors or {})})
    except Erro as e: return _erro(e,cors)


@router.get('/api/v1/documentos/{anexo_id}')
async def consultar(request:Request,anexo_id:str,pagina:int|None=None):
    """Estado e conteúdo de um documento enviado. Chave de formulário: só os enviados por ela, e só o estado (sem o conteúdo)."""
    cors=None
    try:
        ctx=await quem_envia(request);cors=ctx['cors']
        a=await request.app.state.db.fetchrow('SELECT * FROM anexos WHERE tenant_id=$1 AND id=$2',ctx['tenant'],anexo_id)
        if a is None or (ctx['tipo']=='formulario' and a['chave_id']!=ctx['chave']['id']) or (ctx['agente_id'] and a['agente_id']!=ctx['agente_id']): raise Erro(404)
        if ctx['tipo']=='formulario':
            return JSONResponse(jsonable_encoder(D.evento(a)),headers={**CABECALHOS,**(cors or {})})
        lido=D.resultado(a)
        if pagina is not None:
            p=next((x for x in (lido or {}).get('paginas',[]) if x['pagina']==pagina),None)
            if p is None: raise Erro(404,'pagina')
            return JSONResponse(jsonable_encoder({'anexo_id':a['id'],'pagina':p['pagina'],'origem':p['origem'],'texto':p['texto'],
                                                  'tabelas':[{'id':f'tab_{p["pagina"]}_{n}','linhas':t['linhas']} for n,t in enumerate(p['tabelas'],1)]}),headers=CABECALHOS)
        return JSONResponse(jsonable_encoder(D.documento(a,lido)),headers=CABECALHOS)
    except Erro as e: return _erro(e,cors)


# ---------------- Skill ler_documento ----------------

MAX_RESPOSTA=16*1024
TEXTO_POR_VEZ=4000
LINHAS_POR_VEZ=50
TRECHOS=10


def _cursor(v):
    try: n=int(v or 0)
    except ValueError: raise Erro(400,'cursor') from None
    if n<0: raise Erro(400,'cursor')
    return n


def normalizar(texto):
    """Sem acento, minúsculas, pontuação vira espaço (a mesma regra da busca do catálogo, sem depender do módulo)."""
    import unicodedata
    sem=''.join(c for c in unicodedata.normalize('NFKD',str(texto or '')) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+',' ',sem.lower()).strip()


@router.get('/api/v1/documentos/{anexo_id}/conteudo')
async def ler_documento(request:Request,anexo_id:str,conversation_id:str,pagina:int|None=None,busca:str|None=None,tabela:str|None=None,cursor:str|None=None):
    """Skill do agente: só documento da própria conversa. Sem parâmetro: a estrutura e todos os campos (com a confiança).
    pagina → texto da página (até 4.000 caracteres por vez); busca → trechos; tabela → linhas (até 50 por vez)."""
    from fastapi import HTTPException
    try: ctx=await contexto_da_chave('ferramentas:documentos')(request)
    except HTTPException as h: return _erro(Erro(h.status_code))
    try:
        db=request.app.state.db;t=ctx['tenant_id']
        conv=await Repositorio(db).obter(t,'conversas',conversation_id)
        if not conv or (ctx['agente_id'] and conv['agente_id']!=ctx['agente_id']): raise Erro(404)
        cap=await D.capacidade(db,t,conv['agente_id'])
        if not cap or not cap['ativo']: raise Erro(404)
        a=await db.fetchrow('SELECT * FROM anexos WHERE tenant_id=$1 AND id=$2 AND conversation_id=$3',t,anexo_id,conversation_id)
        if a is None or a['apagado_em'] is not None: raise Erro(404)
        request.state.skill_ref={'anexo_id':a['id'],'pagina':pagina,'busca':bool(busca),'tabela':tabela}
        if a['estado']=='processando': raise Erro(409,motivo='processando',tente_em_s=5)
        base={'anexo_id':a['id'],'estado':a['estado'],'motivo':a['motivo'],'formato':a['formato'],'paginas':a['paginas'],'tipo_documento':a['tipo_documento']}
        lido=D.resultado(a)
        if not lido: return {**base,'conteudo':None}
        paginas=lido['paginas'];inicio=_cursor(cursor)
        if pagina is not None:
            p=next((x for x in paginas if x['pagina']==pagina),None)
            if p is None: raise Erro(404,'pagina')
            parte=p['texto'][inicio:inicio+TEXTO_POR_VEZ];fim=inicio+len(parte)
            return {**base,'pagina':pagina,'origem':p['origem'],'texto':parte,'parcial':fim<len(p['texto']),'proximo_cursor':str(fim) if fim<len(p['texto']) else None}
        if busca is not None:
            alvo=' '.join(busca.lower().split())
            if not 2<=len(alvo)<=100: raise Erro(400,'busca')
            n_alvo=normalizar(alvo);achados=[]
            for p in paginas:
                texto=p['texto'];baixo=normalizar(texto)
                # Busca sem acento: a posição no texto normalizado aproxima a do texto original (mesmo tamanho na maioria dos casos).
                pos=baixo.find(n_alvo)
                while pos>=0 and len(achados)<TRECHOS:
                    achados.append({'pagina':p['pagina'],'trecho':texto[max(0,pos-100):pos+len(n_alvo)+100]})
                    pos=baixo.find(n_alvo,pos+len(n_alvo))
            return {**base,'busca':busca,'trechos':achados,'parcial':len(achados)>=TRECHOS}
        if tabela is not None:
            m=re.fullmatch(r'tab_(\d+)_(\d+)',tabela)
            p=next((x for x in paginas if m and x['pagina']==int(m.group(1))),None)
            if not m or p is None or not 1<=int(m.group(2))<=len(p['tabelas']): raise Erro(404,'tabela')
            linhas=p['tabelas'][int(m.group(2))-1]['linhas'];parte=linhas[inicio:inicio+LINHAS_POR_VEZ];fim=inicio+len(parte)
            corpo={**base,'tabela':tabela,'pagina':p['pagina'],'total_linhas':len(linhas),'linhas':parte,'parcial':fim<len(linhas),'proximo_cursor':str(fim) if fim<len(linhas) else None}
            while len(json.dumps(jsonable_encoder(corpo),ensure_ascii=False))>MAX_RESPOSTA and len(corpo['linhas'])>1:
                corpo['linhas']=corpo['linhas'][:len(corpo['linhas'])//2];fim=inicio+len(corpo['linhas'])
                corpo.update(parcial=True,proximo_cursor=str(fim))
            return corpo
        return {**base,'estrutura':[{'pagina':p['pagina'],'origem':p['origem'],'caracteres':len(p['texto']),
                                     'tabelas':[{'id':f'tab_{p["pagina"]}_{n}','linhas':len(t['linhas']),'colunas':max((len(l) for l in t['linhas']),default=0),
                                                 'cabecalho':t['linhas'][0] if t['linhas'] else []} for n,t in enumerate(p['tabelas'],1)]} for p in paginas],
                'campos':D.campos(lido),'avisos':D.avisos(lido),'verificacao_pagamento':D.verificacao(lido)}
    except Erro as e: return _erro(e)
