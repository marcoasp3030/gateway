"""Cardápio interativo: página pública, sem login, ligada a uma conversa por um link que expira.

O token é a única credencial. Fica fora da URL do servidor (o painel lê do # e manda no corpo), então não entra em log.
Nada do cliente sai daqui: nem telefone, nem nome, nem endereço. O pedido passa pelas mesmas funções do agente, na mesma
transação: preço, opções, horário, pagamento e maioridade são conferidos em servico, nunca confiados ao que vem da página."""
import base64,hashlib,hmac,os,secrets
from datetime import datetime,timedelta,timezone
from functools import wraps
import httpx
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from starlette.responses import JSONResponse
from admin.servico import auditar
from modulos import habilitado
from modulos.catalogo import busca as mod_busca
from modulos.catalogo import disponibilidade as disp
from modulos.catalogo.opcoes import do_agente
from modulos.catalogo.rotas import COLUNAS_DO_ITEM,contar_categorias,marcar_vistos,ordem_na_categoria,vendavel
from nucleo import follow_up
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio
from nucleo.horarios import situacao_da_unidade
from nucleo.webhooks import emitir
from . import avisos,servico
from .aparencia import da_empresa
from .rotas import Estrito,NovoItem,recusa,resumo_evento

router=APIRouter()
VALIDADE=timedelta(hours=24)
# Link que vence antes disso é trocado por um novo, para o cliente não abrir um link quase vencido.
FOLGA=timedelta(hours=2)
VALIDADE_PREVIA=timedelta(hours=1)
LIMITE_POR_MINUTO=120
SEM_CACHE={'Cache-Control':'no-store','X-Robots-Tag':'noindex','X-Content-Type-Options':'nosniff'}


def _token(nonce):
    """Mesmo link enquanto o nonce valer. Sem o pepper, o que está no banco não gera o token."""
    chave=os.getenv('API_KEY_PEPPER','').encode()
    return base64.urlsafe_b64encode(hmac.new(chave,b'cardapio:'+nonce.encode(),hashlib.sha256).digest()[:18]).decode()


def _hash(token): return hashlib.sha256(token.encode()).hexdigest()


def _url(token):
    base=os.getenv('PAINEL_PUBLIC_URL','').rstrip('/')
    return f'{base}/cardapio#{token}' if base else None


async def garantir_link(conn,tenant,conversa,por='sistema'):
    """(url, expira_em) do link vigente da conversa; cria se não houver. Sem endereço público configurado, (None, None)."""
    if not os.getenv('PAINEL_PUBLIC_URL') or not os.getenv('API_KEY_PEPPER'): return None,None
    r=await conn.fetchrow('''SELECT nonce,expira_em FROM cardapio_links WHERE tenant_id=$1 AND conversation_id=$2 AND usado_em IS NULL
        AND expira_em>$3 ORDER BY expira_em DESC LIMIT 1''',tenant,conversa,datetime.now(timezone.utc)+FOLGA)
    if r: return _url(_token(r['nonce'])),r['expira_em']
    return await _novo(conn,tenant,conversa,VALIDADE,por)


async def _novo(conn,tenant,conversa,validade,por):
    nonce=secrets.token_urlsafe(16);token=_token(nonce);expira=datetime.now(timezone.utc)+validade
    await conn.execute('INSERT INTO cardapio_links(tenant_id,conversation_id,token_hash,nonce,expira_em,criado_por) VALUES ($1,$2,$3,$4,$5,$6)',
                       tenant,conversa,_hash(token),nonce,expira,por)
    return _url(token),expira


def _texto(valor,maximo=120):
    return str(valor or '').strip()[:maximo]


async def consultar_cep(http,cep):
    """Rua, bairro, cidade e UF do CEP. ViaCEP e, se ele falhar, BrasilAPI: uma fonte fora do ar não trava a entrega.
    Devolve None quando o CEP não existe e levanta quando nenhuma fonte respondeu. Só dígitos vão na URL."""
    falhou=False
    try:
        r=await http.get(f'https://viacep.com.br/ws/{cep}/json/',timeout=4)
        if r.status_code==200:
            d=r.json()
            if d.get('erro'): return None
            return {'rua':_texto(d.get('logradouro')),'bairro':_texto(d.get('bairro')),'cidade':_texto(d.get('localidade')),'uf':_texto(d.get('uf'),2)}
        falhou=True
    except (httpx.HTTPError,ValueError): falhou=True
    if falhou:
        try: r=await http.get(f'https://brasilapi.com.br/api/cep/v2/{cep}',timeout=4)
        except httpx.HTTPError as e: raise RuntimeError('cep_indisponivel') from e
        if r.status_code==404: return None
        if r.status_code!=200: raise RuntimeError('cep_indisponivel')
        try: d=r.json()
        except ValueError as e: raise RuntimeError('cep_indisponivel') from e
        return {'rua':_texto(d.get('street')),'bairro':_texto(d.get('neighborhood')),'cidade':_texto(d.get('city')),'uf':_texto(d.get('state'),2)}


class LinkRecusado(Exception):
    """Recusa do link com o motivo para a página. Não usa HTTPException: o envelope padrão descarta `motivo` fora da validação."""
    def __init__(self,status,motivo):
        self.status=status;self.motivo=motivo


def _erro(status,motivo): return LinkRecusado(status,motivo)


def publica(rota):
    """Rota pública: a recusa do link vira resposta com motivo, fora da transação (que já foi desfeita)."""
    @wraps(rota)
    async def envolta(*args,**kwargs):
        try: return await rota(*args,**kwargs)
        except LinkRecusado as e:
            return JSONResponse({'erro':{'codigo':'LINK_RECUSADO','mensagem':'Link recusado.','detalhes':{'motivo':e.motivo}}},status_code=e.status,headers=SEM_CACHE)
    return envolta


async def _abrir(request,token,conn=None,travar=False):
    """Linha do link, já conferida. Token desconhecido e vencido têm respostas distintas só depois do hash bater."""
    db=conn or request.app.state.db
    r=await db.fetchrow('SELECT * FROM cardapio_links WHERE token_hash=$1'+(' FOR UPDATE' if travar else ''),_hash(token))
    if r is None: raise _erro(404,'link_invalido')
    janela=datetime.now(timezone.utc).replace(second=0,microsecond=0)
    chamadas=await db.fetchval('''UPDATE cardapio_links SET janela=$2,chamadas=CASE WHEN janela=$2 THEN chamadas+1 ELSE 1 END
        WHERE id=$1 RETURNING chamadas''',r['id'],janela)
    if chamadas>LIMITE_POR_MINUTO: raise _erro(429,'muitas_chamadas')
    if r['usado_em'] is None and r['expira_em']<=datetime.now(timezone.utc): raise _erro(410,'link_expirado')
    repo=Repositorio(db)
    try: await habilitado(repo,r['tenant_id'],'pedidos');await habilitado(repo,r['tenant_id'],'catalogo')
    except HTTPException: raise _erro(404,'link_invalido') from None
    if await db.fetchval('SELECT status FROM tenants WHERE id=$1',r['tenant_id'])!='ativo': raise _erro(404,'link_invalido')
    if r['conversation_id']:
        agente=await db.fetchval('SELECT agente_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',r['tenant_id'],r['conversation_id'])
        modulos=(await request.app.state.configuracoes.efetiva(r['tenant_id'],agente))['modulos']
        if not {'pedidos','catalogo'}<=set(modulos): raise _erro(404,'link_invalido')
    return r


async def _por_area(db,tenant,u):
    """A taxa depende do endereço? Modo por área sem linhas ativas vale como valor fixo, igual ao carrinho."""
    if u['taxa_modo'] not in ('bairro','cep'): return False
    return await db.fetchval('SELECT EXISTS(SELECT 1 FROM unidade_taxas WHERE tenant_id=$1 AND unidade_id=$2 AND ativo AND tipo=$3)',tenant,u['id'],u['taxa_modo'])


async def _unidades(db,tenant):
    fuso=await db.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant);saida=[]
    for u in await db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND ativo ORDER BY id',tenant):
        s=await situacao_da_unidade(db,tenant,u,fuso)
        saida.append({'codigo':u['codigo'],'nome':u['nome'],'endereco':u['endereco'],'aberta_agora':s['aberta_agora'],
                      'proxima_abertura':s.get('proxima_abertura'),'aceita_retirada':u['aceita_retirada'] is not False,
                      'faz_entrega':u['delivery_ativo'] is not False,'taxa_entrega':servico.dinheiro(u['taxa_entrega']),
                      'taxa_por_area':await _por_area(db,tenant,u),'frete_gratis_acima':servico.dinheiro(u['frete_gratis_acima']),
                      'pedido_minimo':servico.dinheiro(u['pedido_minimo']),'formas':await servico.formas_aceitas(db,tenant,u['codigo'])})
    return saida


class Ler(Estrito):
    token:str=Field(min_length=16,max_length=64)


class LerUnidade(Ler):
    unidade:str=Field(pattern=r'^[a-z0-9][a-z0-9_]{0,39}$')


class CotarTaxa(LerUnidade):
    bairro:str|None=Field(None,max_length=80)
    cep:str|None=Field(None,pattern=r'^\d{8}$')
    bairro_confirmado:bool=False


class LerCep(Ler):
    cep:str=Field(pattern=r'^[0-9]{8}$')


class Envio(Ler):
    unidade:str=Field(pattern=r'^[a-z0-9][a-z0-9_]{0,39}$')
    itens:list[NovoItem]=Field(min_length=1,max_length=servico.MAX_LINHAS)
    modalidade:str=Field(pattern=r'^(delivery|retirada)$')
    endereco:str|None=Field(None,max_length=500)
    bairro:str|None=Field(None,max_length=80)
    cep:str|None=Field(None,pattern=r'^\d{8}$')
    bairro_confirmado:bool=False
    forma_pagamento:str=Field(pattern=r'^[a-z0-9_]{1,40}$')
    troco_para:float|None=Field(None,gt=0,le=100000)
    observacao:str|None=Field(None,max_length=500)
    maioridade_confirmada:bool=False
    # O total que o cliente viu: se o do catálogo for outro, nada é gravado e a página mostra o novo.
    total_visto:float=Field(ge=0,le=10000000)


def _resposta(corpo):
    return JSONResponse(jsonable_encoder(corpo),headers=SEM_CACHE)


@router.post('/api/v1/publico/cardapio/ler')
@publica
async def ler(request:Request,dados:Ler):
    db=request.app.state.db;r=await _abrir(request,dados.token);t=r['tenant_id']
    empresa=await db.fetchval('SELECT nome FROM tenants WHERE id=$1',t)
    tema=await da_empresa(db,t)
    if r['usado_em']: return _resposta({'empresa':empresa,'aparencia':tema,'usado':{'numero':r['pedido_numero']}})
    carrinho=None
    if r['conversation_id']:
        c=await servico.retrato(db,t,r['conversation_id'])
        # O endereço fica no servidor: a página só sabe que existe. Quem recebe o link encaminhado não o vê.
        carrinho={'unidade':c['unidade'],'modalidade':c['modalidade'],'forma_pagamento':c['forma_pagamento'],'tem_endereco':bool(c['endereco']),
                  'agendado_para':c['agendado_para'],'observacao':None,
                  'itens':[{'item_id':i['item_id'],'quantidade':i['quantidade'],'observacao':i['observacao'],
                            'opcoes':[{'grupo_id':o['grupo_id'],'opcao_id':o['opcao_id'],'quantidade':o['quantidade']} for o in i['opcoes']]} for i in c['itens']]}
    return _resposta({'empresa':empresa,'aparencia':tema,'previa':r['conversation_id'] is None,'expira_em':r['expira_em'],'unidades':await _unidades(db,t),'carrinho':carrinho})


@router.post('/api/v1/publico/cardapio/unidade')
@publica
async def unidade(request:Request,dados:LerUnidade):
    db=request.app.state.db;r=await _abrir(request,dados.token);t=r['tenant_id']
    if not await db.fetchval('SELECT EXISTS(SELECT 1 FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2 AND ativo)',t,dados.unidade):
        raise _erro(404,'unidade_inexistente')
    linhas=[dict(l) for l in await db.fetch(f'SELECT {COLUNAS_DO_ITEM} FROM catalogo_itens WHERE tenant_id=$1 AND unidade=$2 AND ativo AND arquivado_em IS NULL ORDER BY id LIMIT 5000',t,dados.unidade)]
    linhas=[l for l in linhas if vendavel(l)]
    cadastro=await db.fetch('SELECT nome,posicao FROM catalogo_categorias WHERE tenant_id=$1 AND unidade=$2 AND arquivada_em IS NULL',t,dados.unidade)
    ordem=[c['categoria'] for c in contar_categorias(linhas,{c['nome']:c['posicao'] for c in cadastro if c['posicao']})]
    if r['conversation_id']: conf=await disp.da_conversa(db,t,r['conversation_id'])
    else: conf=disp.Conferencia(db,t,datetime.now(timezone.utc),await db.fetchval('SELECT timezone FROM tenants WHERE id=$1',t))
    grupos=await do_agente(db,t,[l['id'] for l in linhas]);por_categoria={c:[] for c in ordem}
    for l in sorted(linhas,key=ordem_na_categoria):
        a=await conf.item(l)
        por_categoria.setdefault(l['categoria'],[]).append({'item_id':l['id'],'nome':l['nome'],'descricao':l['descricao'],'preco':servico.dinheiro(l['preco']),
            'alcoolico':l['alcoolico'],'disponivel':a['disponivel'],'aviso':a['resumo'] if not a['disponivel'] else None,'grupos':grupos.get(l['id'],[])})
    return _resposta({'unidade':dados.unidade,'categorias':[{'nome':c,'itens':por_categoria[c]} for c in ordem]})


@router.post('/api/v1/publico/cardapio/cep')
@publica
async def cep(request:Request,dados:LerCep):
    """Só com link válido e dentro do limite do link: não é uma consulta de CEP aberta na internet."""
    await _abrir(request,dados.token)
    try: achado=await consultar_cep(request.app.state.http,dados.cep)
    except RuntimeError: raise _erro(503,'cep_indisponivel') from None
    if achado is None: raise _erro(404,'cep_inexistente')
    return _resposta(achado)


@router.post('/api/v1/publico/cardapio/taxa')
@publica
async def taxa(request:Request,dados:CotarTaxa):
    """Taxa para o endereço que o cliente digitou. O frete grátis depende do carrinho: a página aplica, e o fechamento confere."""
    db=request.app.state.db;r=await _abrir(request,dados.token)
    u=await servico.unidade_da_empresa(db,r['tenant_id'],dados.unidade)
    if not u: raise _erro(404,'unidade_inexistente')
    e=await servico.entrega_da_unidade(db,r['tenant_id'],u,dados.bairro,dados.cep,None,dados.bairro_confirmado)
    return _resposta({'atende':e['atende'],'motivo':e['motivo'],'taxa':servico.dinheiro(e['taxa']),'origem':e['origem'],'pedir':e['pedir']})


@router.post('/api/v1/publico/cardapio/pedido',status_code=201)
@publica
async def pedido(request:Request,dados:Envio):
    async with request.app.state.db.acquire() as conn:
        try:
            async with conn.transaction():
                # A trava do link é o que impede dois pedidos do mesmo envio: o segundo espera aqui e encontra o link usado.
                r=await _abrir(request,dados.token,conn,travar=True);t=r['tenant_id'];conversa=r['conversation_id']
                if conversa is None: raise _erro(403,'previa')
                if r['usado_em']: return _resposta({'ok':True,'numero':r['pedido_numero'],'repetido':True})
                await servico.substituir_itens(conn,t,conversa)
                await servico.entrega(conn,t,conversa,{'unidade':dados.unidade})
                await marcar_vistos(conn,t,conversa,[i.item_id for i in dados.itens])
                for i in dados.itens:
                    await servico.adicionar(conn,t,conversa,i.item_id,i.quantidade,i.observacao,[o.model_dump() for o in i.opcoes or []])
                campos={'modalidade':dados.modalidade,'observacao':dados.observacao,'maioridade_confirmada':dados.maioridade_confirmada,
                        'forma_pagamento':dados.forma_pagamento,'troco_para':dados.troco_para}
                # Sem endereço na página, vale o que o cliente já disse na conversa.
                if (dados.endereco or '').strip():
                    campos.update(endereco=dados.endereco,bairro=dados.bairro,cep=dados.cep,bairro_confirmado=dados.bairro_confirmado)
                await servico.entrega(conn,t,conversa,campos)
                atual=await servico.retrato(conn,t,conversa)
                if abs((atual['total'] or 0)-dados.total_visto)>0.005:
                    raise servico.Recusa('preco_alterado',409,detalhes={'total':atual['total']})
                p=await servico.fechar(conn,t,conversa)
                # Com os itens recém-gravados não há preço a atualizar; sem pedido aqui, o carrinho mudou no meio do caminho.
                if p is None: raise servico.Recusa('preco_alterado',409,detalhes={'total':atual['total']})
                await follow_up.cancelar(conn,t,conversa,'pedido_fechado',sem_esperar=True)
                await auditar(conn,t,'cliente','pedido.criado',p['numero'])
                await emitir(conn,t,'pedido.criado',resumo_evento(p))
                await avisos.avisar_unidade_do_pedido(conn,t,p,servico.retrato_pedido(p)['itens'])
                await avisos.enfileirar(conn,t,'pedido_cliente',f'{p["numero"]}:recebido',
                    f'Recebemos seu pedido nº {p["numero"]} pelo cardápio, no total de {avisos.reais(p["total"])}. Agora é só aguardar a confirmação da unidade.',None,conversa)
                await servico.registrar_skill(conn,t,conversa,'fechar_pedido',{'detalhe':'pedido '+str(p['numero'])+' pelo cardápio'},'ok')
                await conn.execute('UPDATE cardapio_links SET usado_em=now(),pedido_numero=$2 WHERE id=$1',r['id'],p['numero'])
        except servico.Recusa as e:
            return recusa(e)
    return JSONResponse(jsonable_encoder({'ok':True,'numero':p['numero'],'total':servico.dinheiro(p['total']),'modalidade':p['modalidade']}),status_code=201,headers=SEM_CACHE)


@router.post('/admin/v1/tenants/{tenant}/conversas/{conv}/cardapio-link',dependencies=[Depends(painel)])
async def link_da_conversa(request:Request,tenant:str,conv:str,actor=Depends(painel)):
    """Para o atendente mandar o link na conversa."""
    db=request.app.state.db
    if not await Repositorio(db).obter(tenant,'conversas',conv): raise HTTPException(404,'NOT_FOUND')
    await habilitado(Repositorio(db),tenant,'pedidos')
    url,expira=await garantir_link(db,tenant,conv,actor)
    if not url: raise HTTPException(409,{'motivo':'endereco_publico_ausente'})
    return {'url':url,'expira_em':expira.isoformat()}


@router.post('/admin/v1/tenants/{tenant}/cardapio/previa',dependencies=[Depends(painel)])
async def previa(request:Request,tenant:str,actor=Depends(painel)):
    """Mostra a página como o cliente vê. Sem conversa: não fecha pedido."""
    db=request.app.state.db
    await habilitado(Repositorio(db),tenant,'pedidos')
    if not os.getenv('PAINEL_PUBLIC_URL') or not os.getenv('API_KEY_PEPPER'): raise HTTPException(409,{'motivo':'endereco_publico_ausente'})
    url,expira=await _novo(db,tenant,None,VALIDADE_PREVIA,actor)
    return {'url':url,'expira_em':expira.isoformat()}
