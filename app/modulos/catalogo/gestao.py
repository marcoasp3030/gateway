"""CAT3-a: gestão do cardápio pelo painel (docs/ESPEC_CAT3.md, decisões de 20/10/2026).

Itens (criar, duplicar, arquivar, código, ordem), categorias como cadastro, pratos (o mesmo produto em várias unidades, ligação
sempre confirmada pelo admin, com sugestões por nome), cópia entre unidades, esgotado com volta pela hora e operações em lote.
Membro só lê, e esgota/repõe (operação da cozinha, decisão 9); o resto é de admin e plataforma. Tudo vai para o histórico de gestão
(antes/depois) e para a auditoria. Nada aqui apaga item: pedidos guardam a cópia.

Posição nula (categoria ou item nunca ordenado pelo painel) mantém a ordem de antes para o agente: categorias pelo primeiro item
importado, itens pelo nome. Ordenar grava a posição de todos da unidade (ou da categoria)."""
import json
import re
from datetime import datetime,timedelta,time as hora
from decimal import Decimal,ROUND_HALF_UP
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel,ConfigDict
from starlette.responses import JSONResponse
from nucleo.autenticacao import painel,exigir_admin
from admin.servico import auditar
from admin.alteracoes import registrar
from . import busca as mod_busca
from . import disponibilidade as disp
from .importacao import TIPOS

gestao=APIRouter()
normalizar=mod_busca.normalizar

CODIGO=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,39}')
DELIVERY=('sim','nao','a_confirmar')
MAX_NOME=120
MAX_DESCRICAO=300
MAX_OBSERVACAO=300
MAX_PRECO=Decimal('99999.99')
MAX_LOTE=500
MAX_GRUPOS=400
MAX_ESGOTADO_DIAS=30
MAX_NOMES_ANTERIORES=3
# Campos que "aplicar_em" leva aos itens do mesmo prato (decisão 4). Nunca: unidade, código, categoria e posição.
APLICAVEIS=('nome','descricao','preco','tipo','alcoolico','delivery','ativo','disponibilidade')
COLUNAS='''id,codigo,unidade,categoria,categoria_id,posicao,prato_id,nome,descricao,preco,alcoolico,delivery,tipo,observacao,ativo,
    atualizado_em,disponibilidade,esgotado_ate,esgotado_motivo,arquivado_em'''


class Corpo(BaseModel):
    """Corpo livre validado campo a campo (como no CAT2): o erro diz o primeiro campo que não serve."""
    model_config=ConfigDict(extra='allow')


class Recusa(Exception):
    """Erro de validação com campo e motivo, devolvido pelo envelope de sempre (convertido em _responder)."""
    def __init__(self,status,campo,motivo=None,**extra): self.status,self.campo,self.motivo,self.extra=status,campo,motivo,extra


def resposta_da_recusa(r):
    codigo={400:'VALIDATION_ERROR',404:'NOT_FOUND',409:'CONFLICT',422:'VALIDATION_ERROR'}[r.status]
    mensagens={'VALIDATION_ERROR':'Confira os campos informados.','NOT_FOUND':'Recurso não encontrado.',
               'CONFLICT':'A operação conflita com o estado atual do recurso.'}
    detalhes={'campo':r.campo,**({'motivo':r.motivo} if r.motivo else {}),**r.extra}
    return JSONResponse(jsonable_encoder({'erro':{'codigo':codigo,'mensagem':mensagens[codigo],'detalhes':detalhes}}),status_code=r.status,
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def _rota(fn):
    """Converte Recusa em resposta com o envelope de erro (detalhes com campo e motivo)."""
    import functools
    @functools.wraps(fn)
    async def envolta(*a,**kw):
        try: return await fn(*a,**kw)
        except Recusa as r: return resposta_da_recusa(r)
    return envolta


# ---------------- validação dos campos ----------------

def _texto(valor,campo,maximo,obrigatorio=False):
    if valor is None and not obrigatorio: return None
    if not isinstance(valor,str): raise Recusa(400,campo)
    v=valor.strip()
    if obrigatorio and not v: raise Recusa(400,campo)
    if len(v)>maximo: raise Recusa(400,campo,'longo')
    return v or None


def _preco(valor,campo='preco'):
    if valor is None: return None
    if isinstance(valor,bool) or not isinstance(valor,(int,float,str)): raise Recusa(400,campo)
    from decimal import InvalidOperation
    try: d=Decimal(str(valor).strip().replace(',','.'))
    except InvalidOperation: raise Recusa(400,campo) from None
    if not d.is_finite() or d<=0 or d>MAX_PRECO or d!=d.quantize(Decimal('0.01')): raise Recusa(400,campo)
    return d.quantize(Decimal('0.01'))


def _bool(valor,campo):
    if not isinstance(valor,bool): raise Recusa(400,campo)
    return valor


def _ids(valor,campo,maximo=MAX_LOTE):
    if not isinstance(valor,list) or not valor or len(valor)>maximo or any(isinstance(i,bool) or not isinstance(i,int) or i<1 for i in valor): raise Recusa(400,campo)
    if len(set(valor))!=len(valor): raise Recusa(400,campo,'repetido')
    return valor


def _disponibilidade(valor):
    if valor is not None and not isinstance(valor,dict): raise Recusa(400,'disponibilidade')
    try: return disp.validar(valor)
    except ValueError: raise Recusa(400,'disponibilidade') from None


def campos_do_item(d,parciais=True):
    """{coluna: valor} do que veio no corpo, validado. Sem `parciais`, nome e unidade são obrigatórios (criação)."""
    novos={}
    if 'nome' in d or not parciais: novos['nome']=_texto(d.get('nome'),'nome',MAX_NOME,obrigatorio=True)
    if 'descricao' in d: novos['descricao']=_texto(d['descricao'],'descricao',MAX_DESCRICAO)
    if 'observacao' in d: novos['observacao']=_texto(d['observacao'],'observacao',MAX_OBSERVACAO)
    if 'preco' in d: novos['preco']=_preco(d['preco'])
    if 'tipo' in d:
        if d['tipo'] not in TIPOS: raise Recusa(400,'tipo')
        novos['tipo']=d['tipo']
    if 'alcoolico' in d: novos['alcoolico']=_bool(d['alcoolico'],'alcoolico')
    if 'delivery' in d:
        if d['delivery'] not in DELIVERY: raise Recusa(400,'delivery')
        novos['delivery']=d['delivery']
    if 'ativo' in d: novos['ativo']=_bool(d['ativo'],'ativo')
    if 'disponibilidade' in d: novos['disponibilidade']=_disponibilidade(d['disponibilidade'])
    if 'codigo' in d:
        if not isinstance(d['codigo'],str) or not CODIGO.fullmatch(d['codigo'].strip()): raise Recusa(400,'codigo')
        novos['codigo']=d['codigo'].strip()
    return novos


def para_o_historico(coluna,valor):
    if coluna=='preco': return float(valor) if valor is not None else None
    if coluna=='disponibilidade': return disp.ler(valor)
    if isinstance(valor,datetime): return valor.isoformat()
    return valor


# ---------------- leitura ----------------

async def empresa(db,tenant):
    if not await db.fetchval('SELECT EXISTS(SELECT 1 FROM tenants WHERE id=$1)',tenant): raise Recusa(404,'tenant')


async def item_da_empresa(conn,tenant,item_id,travar=False):
    row=await conn.fetchrow(f"SELECT {COLUNAS} FROM catalogo_itens WHERE tenant_id=$1 AND id=$2{' FOR UPDATE' if travar else ''}",tenant,item_id)
    if row is None: raise Recusa(404,'item_id')
    return row


async def unidade_da_empresa(conn,tenant,codigo,campo='unidade'):
    if not isinstance(codigo,str) or not codigo.strip(): raise Recusa(400,campo)
    u=await conn.fetchrow('SELECT id,codigo,nome,prefixo_codigo FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2',tenant,codigo.strip())
    if u is None: raise Recusa(400,campo,'unidade_inexistente')
    return u


async def categoria_da_empresa(conn,tenant,cid,unidade=None,campo='categoria_id'):
    if isinstance(cid,bool) or not isinstance(cid,int): raise Recusa(400,campo)
    c=await conn.fetchrow('SELECT * FROM catalogo_categorias WHERE tenant_id=$1 AND id=$2',tenant,cid)
    if c is None: raise Recusa(400 if unidade else 404,campo,'categoria_inexistente')
    if unidade is not None and c['unidade']!=unidade: raise Recusa(400,campo,'unidade_diferente')
    if c['arquivada_em'] is not None and unidade is not None: raise Recusa(400,campo,'categoria_arquivada')
    return c


async def para_o_painel(conn,tenant,rows):
    """Itens como o painel vê (com a situação agora, CAT2) + os campos do CAT3-a."""
    from .rotas import com_agora
    pratos={}
    ids={r['prato_id'] for r in rows if r['prato_id']}
    if ids:
        pratos={p['id']:{'id':p['id'],'codigo':p['codigo'],'nome':p['nome']} for p in await conn.fetch(
            'SELECT id,codigo,nome FROM catalogo_pratos WHERE tenant_id=$1 AND id=ANY($2::bigint[])',tenant,list(ids))}
    saida=[]
    for r,base in zip(rows,await com_agora(conn,tenant,rows)):
        saida.append({**base,'categoria_id':r['categoria_id'],'posicao':r['posicao'],'prato':pratos.get(r['prato_id']),
                      'esgotado_ate':r['esgotado_ate'],'esgotado_motivo':r['esgotado_motivo'],'arquivado_em':r['arquivado_em'],
                      'arquivado':r['arquivado_em'] is not None})
    return saida


# ---------------- código e posição ----------------

async def prefixo(conn,tenant,u):
    """Prefixo do código da unidade. Sem cadastro: o que os códigos da unidade já usam (GUE-0001); sem nenhum, as 3 primeiras
    letras do código da unidade (ou 4, ou 2), sem repetir. Fica gravado na unidade."""
    if u['prefixo_codigo']: return u['prefixo_codigo']
    letras=re.sub(r'[^A-Z]','',u['codigo'].upper()) or 'UN'
    usados={r['prefixo_codigo'] for r in await conn.fetch('SELECT prefixo_codigo FROM tenant_unidades WHERE tenant_id=$1 AND prefixo_codigo IS NOT NULL',tenant)}
    em_uso=await conn.fetchval(r"""SELECT substring(codigo from '^([A-Z]{2,4})-[0-9]+$') p FROM catalogo_itens WHERE tenant_id=$1 AND unidade=$2
        AND codigo ~ '^[A-Z]{2,4}-[0-9]+$' GROUP BY 1 ORDER BY count(*) DESC,1 LIMIT 1""",tenant,u['codigo'])
    candidatos=[em_uso or '',letras[:3],letras[:4],letras[:2]]+[letras[:2]+c for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ']
    escolhido=next(c for c in candidatos if 2<=len(c)<=4 and c not in usados)
    await conn.execute('UPDATE tenant_unidades SET prefixo_codigo=$3 WHERE tenant_id=$1 AND id=$2',tenant,u['id'],escolhido)
    return escolhido


async def proximo_codigo(conn,tenant,u):
    """Prefixo da unidade + próximo número livre com 4 dígitos (GUE-0224). Um por vez na empresa."""
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','catalogo-codigo:'+tenant)
    p=await prefixo(conn,tenant,u)
    n=await conn.fetchval(r"""SELECT coalesce(max(substring(codigo from '^'||$2||'-([0-9]+)$')::int),0) FROM catalogo_itens
        WHERE tenant_id=$1 AND codigo ~ ('^'||$2||'-[0-9]+$')""",tenant,p)
    return f'{p}-{n+1:04d}'


async def codigo_livre(conn,tenant,codigo,item_id=None):
    if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM catalogo_itens WHERE tenant_id=$1 AND codigo=$2 AND id IS DISTINCT FROM $3)',tenant,codigo,item_id):
        raise Recusa(409,'codigo','codigo_em_uso')


async def itens_em_ordem(conn,tenant,categoria_id):
    """Ids da categoria na ordem que o agente vê: posição primeiro, depois nome (sem acento) e id."""
    rows=await conn.fetch('SELECT id,nome,posicao FROM catalogo_itens WHERE tenant_id=$1 AND categoria_id=$2',tenant,categoria_id)
    return [r['id'] for r in sorted(rows,key=lambda r:(r['posicao'] or 10**9,normalizar(r['nome']),r['id']))]


async def categoria_ordenada(conn,tenant,categoria_id,sem=None):
    """A categoria tem ordem própria (algum item, fora `sem`, com posição)?"""
    return await conn.fetchval('SELECT EXISTS(SELECT 1 FROM catalogo_itens WHERE tenant_id=$1 AND categoria_id=$2 AND posicao IS NOT NULL AND id IS DISTINCT FROM $3)',
                               tenant,categoria_id,sem)


async def gravar_ordem(conn,tenant,categoria_id,ids):
    for n,i in enumerate(ids,1):
        await conn.execute('UPDATE catalogo_itens SET posicao=$3 WHERE tenant_id=$1 AND id=$2 AND posicao IS DISTINCT FROM $3',tenant,i,n)


async def colocar(conn,tenant,categoria_id,item_id,posicao=None):
    """Põe o item na categoria: na posição pedida (a categoria passa a ter ordem própria) ou no fim, se ela já tem ordem.
    Categoria sem ordem e sem posição pedida: o item fica sem posição (ordem pelo nome, como antes)."""
    if posicao is None and not await categoria_ordenada(conn,tenant,categoria_id,item_id):
        await conn.execute('UPDATE catalogo_itens SET posicao=NULL WHERE tenant_id=$1 AND id=$2',tenant,item_id);return
    ids=[i for i in await itens_em_ordem(conn,tenant,categoria_id) if i!=item_id]
    lugar=len(ids) if posicao is None else max(0,min(posicao-1,len(ids)))
    await gravar_ordem(conn,tenant,categoria_id,ids[:lugar]+[item_id]+ids[lugar:])


async def categorias_em_ordem(conn,tenant,unidade):
    rows=await conn.fetch("""SELECT c.id,c.posicao,(SELECT min(i.id) FROM catalogo_itens i WHERE i.tenant_id=c.tenant_id AND i.categoria_id=c.id) primeiro
        FROM catalogo_categorias c WHERE c.tenant_id=$1 AND c.unidade=$2""",tenant,unidade)
    return [r['id'] for r in sorted(rows,key=lambda r:(r['posicao'] or 10**9,r['primeiro'] or 10**12,r['id']))]


async def categoria_por_nome(conn,tenant,unidade,nome):
    """Categoria da unidade com este nome (sem acento/maiúsculas); cria no fim se faltar."""
    for c in await conn.fetch('SELECT id,nome FROM catalogo_categorias WHERE tenant_id=$1 AND unidade=$2',tenant,unidade):
        if normalizar(c['nome'])==normalizar(nome): return c['id']
    return await nova_categoria(conn,tenant,unidade,nome,None)


async def nova_categoria(conn,tenant,unidade,nome,posicao):
    cid=await conn.fetchval('INSERT INTO catalogo_categorias(tenant_id,unidade,nome) VALUES ($1,$2,$3) RETURNING id',tenant,unidade,nome)
    if posicao is not None or await conn.fetchval('SELECT EXISTS(SELECT 1 FROM catalogo_categorias WHERE tenant_id=$1 AND unidade=$2 AND posicao IS NOT NULL)',tenant,unidade):
        ids=[i for i in await categorias_em_ordem(conn,tenant,unidade) if i!=cid]
        lugar=len(ids) if posicao is None else max(0,min(posicao-1,len(ids)))
        for n,i in enumerate(ids[:lugar]+[cid]+ids[lugar:],1):
            await conn.execute('UPDATE catalogo_categorias SET posicao=$3 WHERE tenant_id=$1 AND id=$2',tenant,i,n)
    return cid


# ---------------- itens ----------------

async def gravar_item(conn,tenant,atual,novos,actor):
    """Grava `novos` (só o que mudou) no item, com histórico. Devolve (linha nova, {campo: (antes, depois)})."""
    mudou={c:v for c,v in novos.items() if para_o_historico(c,v)!=para_o_historico(c,atual[c])}
    if not mudou: return atual,{}
    if 'codigo' in mudou: await codigo_livre(conn,tenant,mudou['codigo'],atual['id'])
    colunas=list(mudou)
    valores=[json.dumps(mudou[c]) if c=='disponibilidade' and mudou[c] is not None else mudou[c] for c in colunas]
    atribuicoes=','.join(f'{c}=${n}'+('::jsonb' if c=='disponibilidade' else '') for n,c in enumerate(colunas,3))
    row=await conn.fetchrow(f'UPDATE catalogo_itens SET {atribuicoes},atualizado_em=clock_timestamp() WHERE tenant_id=$1 AND id=$2 RETURNING {COLUNAS}',
                            tenant,atual['id'],*valores)
    await registrar(conn,tenant,actor,'catalogo_item',atual['id'],'alterado',{c:para_o_historico(c,atual[c]) for c in colunas},
                    {c:para_o_historico(c,row[c]) for c in colunas})
    return row,{c:(para_o_historico(c,atual[c]),para_o_historico(c,row[c])) for c in colunas}


async def alvos(conn,tenant,item,aplicar_em):
    """Itens do mesmo prato que recebem a alteração (sem o próprio). 'este'/None = nenhum; 'todas' = todas as unidades do prato;
    lista = só essas unidades."""
    if aplicar_em in (None,'este'): return []
    if not (aplicar_em=='todas' or (isinstance(aplicar_em,list) and aplicar_em and all(isinstance(u,str) for u in aplicar_em) and len(aplicar_em)<=50)):
        raise Recusa(400,'aplicar_em')
    if not item['prato_id']: raise Recusa(400,'aplicar_em','sem_prato')
    rows=await conn.fetch(f'SELECT {COLUNAS} FROM catalogo_itens WHERE tenant_id=$1 AND prato_id=$2 AND id<>$3 ORDER BY unidade FOR UPDATE',
                          tenant,item['prato_id'],item['id'])
    if aplicar_em=='todas': return rows
    faltam=set(aplicar_em)-{r['unidade'] for r in rows}-{item['unidade']}
    if faltam: raise Recusa(400,'aplicar_em','unidade_sem_item_do_prato',unidades=sorted(faltam))
    return [r for r in rows if r['unidade'] in aplicar_em]


async def aplicar_aos_alvos(conn,tenant,lista,novos,actor):
    resultados=[]
    for r in lista:
        try:
            async with conn.transaction():
                _,mudou=await gravar_item(conn,tenant,r,novos,actor)
            resultados.append({'unidade':r['unidade'],'item_id':r['id'],'ok':True,'alterados':sorted(mudou)})
        except Recusa as x: resultados.append({'unidade':r['unidade'],'item_id':r['id'],'ok':False,'motivo':x.motivo or x.campo})
    return resultados


@gestao.post('/tenants/{tenant_id}/catalogo/itens',status_code=201,dependencies=[Depends(exigir_admin)])
@_rota
async def criar_item(request:Request,tenant_id:str,dados:Corpo,actor=Depends(painel)):
    """Item novo: unidade, categoria_id e nome obrigatórios; código gerado se não vier. Nasce ativo, salvo `ativo: false`."""
    d=dict(dados.model_extra or {})
    permitidos={'unidade','categoria_id','nome','descricao','observacao','preco','tipo','alcoolico','delivery','ativo','disponibilidade','codigo','posicao'}
    if set(d)-permitidos: raise Recusa(400,sorted(set(d)-permitidos)[0])
    db=request.app.state.db;await empresa(db,tenant_id)
    novos=campos_do_item(d,parciais=False)
    posicao=d.get('posicao')
    if posicao is not None and (isinstance(posicao,bool) or not isinstance(posicao,int) or posicao<1): raise Recusa(400,'posicao')
    async with db.acquire() as conn,conn.transaction():
        u=await unidade_da_empresa(conn,tenant_id,d.get('unidade'))
        c=await categoria_da_empresa(conn,tenant_id,d.get('categoria_id'),u['codigo'])
        codigo=novos.pop('codigo',None) or await proximo_codigo(conn,tenant_id,u)
        await codigo_livre(conn,tenant_id,codigo)
        valores={'tipo':'item','alcoolico':False,'delivery':'sim','ativo':True,**novos}
        colunas=list(valores)
        item_id=await conn.fetchval(f'''INSERT INTO catalogo_itens(tenant_id,unidade,categoria,categoria_id,codigo,{",".join(colunas)})
            VALUES ($1,$2,$3,$4,$5,{",".join(f"${n}"+("::jsonb" if k=="disponibilidade" else "") for n,k in enumerate(colunas,6))}) RETURNING id''',
            tenant_id,u['codigo'],c['nome'],c['id'],codigo,*[json.dumps(valores[k]) if k=='disponibilidade' and valores[k] is not None else valores[k] for k in colunas])
        await colocar(conn,tenant_id,c['id'],item_id,posicao)
        row=await item_da_empresa(conn,tenant_id,item_id)
        await registrar(conn,tenant_id,actor,'catalogo_item',item_id,'criado',None,{k:para_o_historico(k,row[k]) for k in ('codigo','unidade','categoria',*colunas)})
        await auditar(conn,tenant_id,actor,'catalogo.item_criado',str(item_id))
        return (await para_o_painel(conn,tenant_id,[row]))[0]


async def copiar_item(conn,tenant,origem,u,categoria_id,nome,actor,prato_id=None):
    """Cópia do item na unidade `u` (código novo com o prefixo dela), inativa (decisão 2)."""
    codigo=await proximo_codigo(conn,tenant,u)
    c=await conn.fetchrow('SELECT id,nome FROM catalogo_categorias WHERE tenant_id=$1 AND id=$2',tenant,categoria_id)
    item_id=await conn.fetchval('''INSERT INTO catalogo_itens(tenant_id,unidade,categoria,categoria_id,codigo,nome,descricao,preco,tipo,alcoolico,delivery,
            observacao,disponibilidade,ativo,prato_id)
        SELECT tenant_id,$3::text,$4::text,$5::bigint,$6::text,$7::text,descricao,preco,tipo,alcoolico,delivery,observacao,disponibilidade,false,$8::bigint
        FROM catalogo_itens WHERE tenant_id=$1 AND id=$2 RETURNING id''',tenant,origem['id'],u['codigo'],c['nome'],c['id'],codigo,nome or origem['nome'],prato_id)
    await colocar(conn,tenant,c['id'],item_id)
    await registrar(conn,tenant,actor,'catalogo_item',item_id,'criado',None,{'codigo':codigo,'unidade':u['codigo'],'categoria':c['nome'],
                    'copiado_de':origem['id'],'ativo':False,**({'prato_id':prato_id} if prato_id else {})})
    return item_id


@gestao.post('/tenants/{tenant_id}/catalogo/itens/{item_id}/duplicar',status_code=201,dependencies=[Depends(exigir_admin)])
@_rota
async def duplicar(request:Request,tenant_id:str,item_id:int,dados:Corpo|None=None,actor=Depends(painel)):
    """Cópia com código novo, na mesma unidade ou em outra (categoria de mesmo nome, criada se faltar). Nasce inativa."""
    d=dict((dados.model_extra if dados else None) or {})
    if set(d)-{'unidade','categoria_id','nome'}: raise Recusa(400,sorted(set(d)-{'unidade','categoria_id','nome'})[0])
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        origem=await item_da_empresa(conn,tenant_id,item_id)
        u=await unidade_da_empresa(conn,tenant_id,d.get('unidade') or origem['unidade'])
        nome=_texto(d.get('nome'),'nome',MAX_NOME) if 'nome' in d else None
        if d.get('categoria_id') is not None: cid=(await categoria_da_empresa(conn,tenant_id,d['categoria_id'],u['codigo']))['id']
        elif u['codigo']==origem['unidade']: cid=origem['categoria_id']
        else: cid=await categoria_por_nome(conn,tenant_id,u['codigo'],origem['categoria'])
        novo=await copiar_item(conn,tenant_id,origem,u,cid,nome,actor)
        await auditar(conn,tenant_id,actor,'catalogo.item_criado',str(novo))
        return (await para_o_painel(conn,tenant_id,[await item_da_empresa(conn,tenant_id,novo)]))[0]


async def _arquivo(request,tenant_id,item_id,actor,arquivar):
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        atual=await item_da_empresa(conn,tenant_id,item_id,travar=True)
        if (atual['arquivado_em'] is not None)!=arquivar:
            row=await conn.fetchrow(f"UPDATE catalogo_itens SET arquivado_em={'clock_timestamp()' if arquivar else 'NULL'},atualizado_em=clock_timestamp() WHERE tenant_id=$1 AND id=$2 RETURNING {COLUNAS}",tenant_id,item_id)
            await registrar(conn,tenant_id,actor,'catalogo_item',item_id,'arquivado' if arquivar else 'desarquivado',{'arquivado':not arquivar},{'arquivado':arquivar})
            await auditar(conn,tenant_id,actor,'catalogo.item_'+('arquivado' if arquivar else 'desarquivado'),str(item_id))
        else: row=atual
        return (await para_o_painel(conn,tenant_id,[row]))[0]


@gestao.post('/tenants/{tenant_id}/catalogo/itens/{item_id}/arquivar',dependencies=[Depends(exigir_admin)])
@_rota
async def arquivar_item(request:Request,tenant_id:str,item_id:int,actor=Depends(painel)):
    """Tira do dia a dia (some do agente e da lista padrão do painel; não pode ser pedido). Nunca apaga."""
    return await _arquivo(request,tenant_id,item_id,actor,True)


@gestao.post('/tenants/{tenant_id}/catalogo/itens/{item_id}/desarquivar',dependencies=[Depends(exigir_admin)])
@_rota
async def desarquivar_item(request:Request,tenant_id:str,item_id:int,actor=Depends(painel)):
    return await _arquivo(request,tenant_id,item_id,actor,False)


@gestao.put('/tenants/{tenant_id}/catalogo/categorias/{categoria_id}/ordem',dependencies=[Depends(exigir_admin)])
@_rota
async def ordenar_itens(request:Request,tenant_id:str,categoria_id:int,dados:Corpo,actor=Depends(painel)):
    """Ordem dos itens na categoria: {"itens":[id,…]}. O que faltar vai para o fim, na ordem em que estava."""
    d=dict(dados.model_extra or {})
    ids=_ids(d.get('itens'),'itens',2000)
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        c=await categoria_da_empresa(conn,tenant_id,categoria_id)
        atuais=await itens_em_ordem(conn,tenant_id,c['id'])
        if set(ids)-set(atuais): raise Recusa(400,'itens','item_de_outra_categoria',itens=sorted(set(ids)-set(atuais)))
        antes={i:n for n,i in enumerate(atuais,1)}
        nova=ids+[i for i in atuais if i not in ids]
        await gravar_ordem(conn,tenant_id,c['id'],nova)
        await registrar(conn,tenant_id,actor,'catalogo_categoria',c['id'],'itens_ordenados',{'itens':atuais},{'itens':nova})
        await auditar(conn,tenant_id,actor,'catalogo.itens_ordenados',str(c['id']))
    return {'categoria_id':c['id'],'itens':[{'item_id':i,'posicao':n,'posicao_anterior':antes[i]} for n,i in enumerate(nova,1)]}


# ---------------- esgotado ----------------

async def fuso_do_item(conn,tenant,item):
    f=await conn.fetchval('SELECT fuso FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2',tenant,item['unidade'])
    return f or await conn.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant)


def ate_quando(valor,fuso,agora):
    """"fim_do_dia" = 23:59:59 de hoje no fuso da unidade; ou "AAAA-MM-DDTHH:MM" no fuso da unidade. Futuro e até 30 dias."""
    z=ZoneInfo(fuso);local=agora.astimezone(z)
    if valor=='fim_do_dia': quando=datetime.combine(local.date(),hora(23,59,59),tzinfo=z)
    else:
        if not isinstance(valor,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}',valor): raise Recusa(400,'ate')
        try: quando=datetime.fromisoformat(valor).replace(tzinfo=z)
        except ValueError: raise Recusa(400,'ate') from None
    if quando<=agora: raise Recusa(400,'ate','passado')
    if quando>agora+timedelta(days=MAX_ESGOTADO_DIAS): raise Recusa(400,'ate','longe_demais')
    return quando


async def _esgotar(request,tenant_id,item_id,d,actor,repor):
    from modulos.pedidos import agendamento
    permitidos={'aplicar_em'} if repor else {'ate','motivo','aplicar_em'}
    if set(d)-permitidos: raise Recusa(400,sorted(set(d)-permitidos)[0])
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        item=await item_da_empresa(conn,tenant_id,item_id,travar=True)
        lista=await alvos(conn,tenant_id,item,d.get('aplicar_em'))
        agora=agendamento.agora_utc()
        def novos_para(r,fuso):
            if repor: return {'esgotado_ate':None,'esgotado_motivo':None}
            return {'esgotado_ate':ate_quando(d.get('ate','fim_do_dia'),fuso,agora),'esgotado_motivo':_texto(d.get('motivo'),'motivo',200)}
        row,_=await gravar_item(conn,tenant_id,item,novos_para(item,await fuso_do_item(conn,tenant_id,item)),actor)
        resultados=[]
        for r in lista:
            try:
                async with conn.transaction(): await gravar_item(conn,tenant_id,r,novos_para(r,await fuso_do_item(conn,tenant_id,r)),actor)
                resultados.append({'unidade':r['unidade'],'item_id':r['id'],'ok':True})
            except Recusa as x: resultados.append({'unidade':r['unidade'],'item_id':r['id'],'ok':False,'motivo':x.motivo or x.campo})
        await auditar(conn,tenant_id,actor,'catalogo.item_'+('reposto' if repor else 'esgotado'),str(item_id))
        return {**(await para_o_painel(conn,tenant_id,[row]))[0],**({'aplicado_em':resultados} if d.get('aplicar_em') not in (None,'este') else {})}


@gestao.post('/tenants/{tenant_id}/catalogo/itens/{item_id}/esgotar')
@_rota
async def esgotar(request:Request,tenant_id:str,item_id:int,dados:Corpo|None=None,actor=Depends(painel)):
    """Esgotado até "fim_do_dia" (padrão) ou "AAAA-MM-DDTHH:MM" (fuso da unidade). Volta sozinho na hora. Qualquer papel (cozinha)."""
    return await _esgotar(request,tenant_id,item_id,dict((dados.model_extra if dados else None) or {}),actor,False)


@gestao.post('/tenants/{tenant_id}/catalogo/itens/{item_id}/repor')
@_rota
async def repor(request:Request,tenant_id:str,item_id:int,dados:Corpo|None=None,actor=Depends(painel)):
    """Volta antes da hora. Qualquer papel."""
    return await _esgotar(request,tenant_id,item_id,dict((dados.model_extra if dados else None) or {}),actor,True)


# ---------------- categorias ----------------

async def nome_livre(conn,tenant,unidade,nome,categoria_id=None):
    for c in await conn.fetch('SELECT id,nome FROM catalogo_categorias WHERE tenant_id=$1 AND unidade=$2',tenant,unidade):
        if c['id']!=categoria_id and normalizar(c['nome'])==normalizar(nome): raise Recusa(409,'nome','nome_em_uso',categoria_id=c['id'])


def categoria_do_painel(c,contagens=None,ordem=None):
    k=contagens or {}
    return {'id':c['id'],'unidade':c['unidade'],'nome':c['nome'],'categoria':c['nome'],'posicao':c['posicao'],'ordem':ordem,
            'nomes_anteriores':list(c['nomes_anteriores']),'arquivada':c['arquivada_em'] is not None,'arquivada_em':c['arquivada_em'],
            'qtd_itens':k.get('qtd_itens',0),'itens_ativos':k.get('itens_ativos',0),'itens_no_cardapio':k.get('itens_no_cardapio',0),
            'itens_arquivados':k.get('itens_arquivados',0)}


@gestao.post('/tenants/{tenant_id}/catalogo/categorias',status_code=201,dependencies=[Depends(exigir_admin)])
@_rota
async def criar_categoria(request:Request,tenant_id:str,dados:Corpo,actor=Depends(painel)):
    d=dict(dados.model_extra or {})
    if set(d)-{'unidade','nome','posicao'}: raise Recusa(400,sorted(set(d)-{'unidade','nome','posicao'})[0])
    nome=_texto(d.get('nome'),'nome',MAX_NOME,obrigatorio=True)
    posicao=d.get('posicao')
    if posicao is not None and (isinstance(posicao,bool) or not isinstance(posicao,int) or posicao<1): raise Recusa(400,'posicao')
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','catalogo-categorias:'+tenant_id)
        u=await unidade_da_empresa(conn,tenant_id,d.get('unidade'))
        await nome_livre(conn,tenant_id,u['codigo'],nome)
        cid=await nova_categoria(conn,tenant_id,u['codigo'],nome,posicao)
        c=await conn.fetchrow('SELECT * FROM catalogo_categorias WHERE tenant_id=$1 AND id=$2',tenant_id,cid)
        await registrar(conn,tenant_id,actor,'catalogo_categoria',cid,'criada',None,{'unidade':u['codigo'],'nome':nome,'posicao':c['posicao']})
        await auditar(conn,tenant_id,actor,'catalogo.categoria_criada',str(cid))
        return categoria_do_painel(c)


@gestao.patch('/tenants/{tenant_id}/catalogo/categorias/{categoria_id}',dependencies=[Depends(exigir_admin)])
@_rota
async def renomear_categoria(request:Request,tenant_id:str,categoria_id:int,dados:Corpo,actor=Depends(painel)):
    """Renomeia: os itens dela passam a ter o nome novo; o antigo fica como sinônimo na busca do agente (até 3).
    Pedidos antigos guardam o nome da época."""
    d=dict(dados.model_extra or {})
    if set(d)-{'nome'}: raise Recusa(400,sorted(set(d)-{'nome'})[0])
    nome=_texto(d.get('nome'),'nome',MAX_NOME,obrigatorio=True)
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','catalogo-categorias:'+tenant_id)
        c=await categoria_da_empresa(conn,tenant_id,categoria_id)
        if nome!=c['nome']:
            await nome_livre(conn,tenant_id,c['unidade'],nome,c['id'])
            anteriores=[n for n in [c['nome'],*c['nomes_anteriores']] if normalizar(n)!=normalizar(nome)]
            anteriores=list(dict.fromkeys(anteriores))[:MAX_NOMES_ANTERIORES]
            c=await conn.fetchrow('UPDATE catalogo_categorias SET nome=$3,nomes_anteriores=$4,atualizado_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING *',
                                  tenant_id,c['id'],nome,anteriores)
            await conn.execute('UPDATE catalogo_itens SET categoria=$3,atualizado_em=clock_timestamp() WHERE tenant_id=$1 AND categoria_id=$2',tenant_id,c['id'],nome)
            await registrar(conn,tenant_id,actor,'catalogo_categoria',c['id'],'renomeada',{'nome':anteriores[0] if anteriores else None},{'nome':nome})
            await auditar(conn,tenant_id,actor,'catalogo.categoria_renomeada',str(c['id']))
        return categoria_do_painel(c)


@gestao.put('/tenants/{tenant_id}/catalogo/categorias/ordem',dependencies=[Depends(exigir_admin)])
@_rota
async def ordenar_categorias(request:Request,tenant_id:str,dados:Corpo,actor=Depends(painel)):
    """Ordem das categorias da unidade: {"unidade","categorias":[id,…]}. Vale para o painel e para a lista do agente.
    O que faltar vai para o fim, na ordem em que estava."""
    d=dict(dados.model_extra or {})
    ids=_ids(d.get('categorias'),'categorias',1000)
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','catalogo-categorias:'+tenant_id)
        u=await unidade_da_empresa(conn,tenant_id,d.get('unidade'))
        atuais=await categorias_em_ordem(conn,tenant_id,u['codigo'])
        if set(ids)-set(atuais): raise Recusa(400,'categorias','categoria_de_outra_unidade',categorias=sorted(set(ids)-set(atuais)))
        nova=ids+[i for i in atuais if i not in ids]
        for n,i in enumerate(nova,1): await conn.execute('UPDATE catalogo_categorias SET posicao=$3 WHERE tenant_id=$1 AND id=$2',tenant_id,i,n)
        await registrar(conn,tenant_id,actor,'catalogo_categoria',u['codigo'],'ordenadas',{'categorias':atuais},{'categorias':nova})
        await auditar(conn,tenant_id,actor,'catalogo.categorias_ordenadas',u['codigo'])
    return {'unidade':u['codigo'],'categorias':[{'id':i,'posicao':n} for n,i in enumerate(nova,1)]}


@gestao.post('/tenants/{tenant_id}/catalogo/categorias/{categoria_id}/arquivar',dependencies=[Depends(exigir_admin)])
@_rota
async def arquivar_categoria(request:Request,tenant_id:str,categoria_id:int,dados:Corpo|None=None,actor=Depends(painel)):
    """Com itens não arquivados, exige dizer o que fazer com eles: {"itens":"arquivar"} ou {"itens":"mover","destino":<categoria_id>}."""
    d=dict((dados.model_extra if dados else None) or {})
    if set(d)-{'itens','destino'}: raise Recusa(400,sorted(set(d)-{'itens','destino'})[0])
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        c=await categoria_da_empresa(conn,tenant_id,categoria_id)
        vivos=[r['id'] for r in await conn.fetch('SELECT id FROM catalogo_itens WHERE tenant_id=$1 AND categoria_id=$2 AND arquivado_em IS NULL ORDER BY id',tenant_id,c['id'])]
        movidos=arquivados=[]
        if vivos:
            if d.get('itens') not in ('arquivar','mover'): raise Recusa(409,'itens','categoria_com_itens',qtd_itens=len(vivos))
            if d['itens']=='mover':
                destino=await categoria_da_empresa(conn,tenant_id,d.get('destino'),c['unidade'],'destino')
                if destino['id']==c['id']: raise Recusa(400,'destino')
                for i in vivos:
                    await conn.execute('UPDATE catalogo_itens SET categoria_id=$3,atualizado_em=clock_timestamp() WHERE tenant_id=$1 AND id=$2',tenant_id,i,destino['id'])
                    await colocar(conn,tenant_id,destino['id'],i)
                movidos=vivos
            else:
                await conn.execute('UPDATE catalogo_itens SET arquivado_em=clock_timestamp(),atualizado_em=clock_timestamp() WHERE tenant_id=$1 AND id=ANY($2::int[])',tenant_id,vivos)
                arquivados=vivos
        if c['arquivada_em'] is None:
            c=await conn.fetchrow('UPDATE catalogo_categorias SET arquivada_em=now(),atualizado_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING *',tenant_id,c['id'])
            await registrar(conn,tenant_id,actor,'catalogo_categoria',c['id'],'arquivada',{'arquivada':False},
                            {'arquivada':True,**({'itens_movidos':movidos,'destino':d.get('destino')} if movidos else {}),**({'itens_arquivados':arquivados} if arquivados else {})})
            await auditar(conn,tenant_id,actor,'catalogo.categoria_arquivada',str(c['id']))
        return {**categoria_do_painel(c),'itens_movidos':movidos,'itens_arquivados':arquivados}


@gestao.post('/tenants/{tenant_id}/catalogo/categorias/{categoria_id}/desarquivar',dependencies=[Depends(exigir_admin)])
@_rota
async def desarquivar_categoria(request:Request,tenant_id:str,categoria_id:int,actor=Depends(painel)):
    """Volta a categoria (os itens arquivados com ela continuam arquivados: desarquive cada um, ou em lote)."""
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        c=await categoria_da_empresa(conn,tenant_id,categoria_id)
        if c['arquivada_em'] is not None:
            c=await conn.fetchrow('UPDATE catalogo_categorias SET arquivada_em=NULL,atualizado_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING *',tenant_id,c['id'])
            await registrar(conn,tenant_id,actor,'catalogo_categoria',c['id'],'desarquivada',{'arquivada':True},{'arquivada':False})
            await auditar(conn,tenant_id,actor,'catalogo.categoria_desarquivada',str(c['id']))
        return categoria_do_painel(c)


# ---------------- pratos ----------------

async def prato_da_empresa(conn,tenant,prato_id,travar=False):
    p=await conn.fetchrow(f"SELECT * FROM catalogo_pratos WHERE tenant_id=$1 AND id=$2{' FOR UPDATE' if travar else ''}",tenant,prato_id)
    if p is None: raise Recusa(404,'prato_id')
    return p


async def proximo_prato(conn,tenant):
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','catalogo-prato:'+tenant)
    n=await conn.fetchval(r"SELECT coalesce(max(substring(codigo from '^P-([0-9]+)$')::int),0) FROM catalogo_pratos WHERE tenant_id=$1",tenant)
    return f'P-{n+1:04d}'


async def prato_do_painel(conn,tenant,p):
    itens=await conn.fetch('''SELECT id,codigo,unidade,nome,preco,ativo,arquivado_em,esgotado_ate FROM catalogo_itens
        WHERE tenant_id=$1 AND prato_id=$2 ORDER BY unidade''',tenant,p['id'])
    return {'id':p['id'],'codigo':p['codigo'],'nome':p['nome'],'criado_em':p['criado_em'],
            'itens':[{'item_id':i['id'],'codigo':i['codigo'],'unidade':i['unidade'],'nome':i['nome'],'preco':float(i['preco']) if i['preco'] is not None else None,
                      'ativo':i['ativo'],'arquivado':i['arquivado_em'] is not None,'esgotado_ate':i['esgotado_ate']} for i in itens]}


async def ligar(conn,tenant,prato,ids,actor):
    """Liga itens ao prato: um por unidade; item já ligado a outro prato é recusado (desligue antes)."""
    rows=await conn.fetch('SELECT id,unidade,prato_id FROM catalogo_itens WHERE tenant_id=$1 AND id=ANY($2::int[]) FOR UPDATE',tenant,ids)
    if len(rows)!=len(ids): raise Recusa(400,'itens','item_inexistente',itens=sorted(set(ids)-{r['id'] for r in rows}))
    outros=[r['id'] for r in rows if r['prato_id'] not in (None,prato['id'])]
    if outros: raise Recusa(409,'itens','item_ja_ligado',itens=outros)
    ja={r['unidade'] for r in await conn.fetch('SELECT unidade FROM catalogo_itens WHERE tenant_id=$1 AND prato_id=$2 AND NOT (id=ANY($3::int[]))',tenant,prato['id'],ids)}
    unidades=[r['unidade'] for r in rows]
    repetidas=sorted({u for u in unidades if unidades.count(u)>1 or u in ja})
    if repetidas: raise Recusa(409,'itens','unidade_repetida',unidades=repetidas)
    novos=[r['id'] for r in rows if r['prato_id'] is None]
    if novos:
        await conn.execute('UPDATE catalogo_itens SET prato_id=$3,atualizado_em=clock_timestamp() WHERE tenant_id=$1 AND id=ANY($2::int[])',tenant,novos,prato['id'])
        await registrar(conn,tenant,actor,'catalogo_prato',prato['id'],'itens_ligados',None,{'codigo':prato['codigo'],'itens':sorted(novos)})
    return novos


@gestao.get('/tenants/{tenant_id}/catalogo/pratos')
@_rota
async def lista_pratos(request:Request,tenant_id:str,q:str='',limite:int=50,cursor:int=0):
    if not 1<=limite<=200 or cursor<0 or len(q)>200: raise Recusa(400,'limite')
    db=request.app.state.db;await empresa(db,tenant_id)
    rows=await db.fetch('SELECT * FROM catalogo_pratos WHERE tenant_id=$1 AND id>$2 ORDER BY id',tenant_id,cursor)
    if q.strip(): rows=[p for p in rows if normalizar(q) in normalizar(p['nome']) or q.strip().upper()==p['codigo']]
    pagina=rows[:limite]
    return {'itens':[await prato_do_painel(db,tenant_id,p) for p in pagina],'proximo_cursor':pagina[-1]['id'] if len(rows)>limite else None}


@gestao.get('/tenants/{tenant_id}/catalogo/pratos/sugestoes')
@_rota
async def sugestoes(request:Request,tenant_id:str,ignoradas:bool=False,limite:int=100,cursor:str|None=None):
    """Sugestões de ligação: itens de unidades diferentes com o mesmo nome (sem acento, maiúsculas e pontuação), sem prato ou com
    um prato só no grupo. Ordem pela chave (o nome normalizado); `cursor` = a última chave da página. `ignoradas=true` lista as
    ignoradas (que voltam sozinhas se aparecer item novo com o nome)."""
    if not 1<=limite<=500: raise Recusa(400,'limite')
    db=request.app.state.db;await empresa(db,tenant_id)
    grupos=await grupos_sugeridos(db,tenant_id)
    marcadas={r['chave']:set(r['itens']) for r in await db.fetch('SELECT chave,itens FROM catalogo_sugestoes_ignoradas WHERE tenant_id=$1',tenant_id)}
    escolhidos=[g for g in grupos if (g['chave'] in marcadas and set(g['sem_prato'])<=marcadas[g['chave']])==ignoradas]
    if cursor: escolhidos=[g for g in escolhidos if g['chave']>cursor]
    pagina=escolhidos[:limite]
    return {'total':len(escolhidos) if not cursor else None,'grupos':[{k:v for k,v in g.items() if k!='sem_prato'} for g in pagina],
            'proximo_cursor':pagina[-1]['chave'] if len(escolhidos)>limite else None}


async def grupos_sugeridos(db,tenant,chaves=None):
    rows=await db.fetch('''SELECT i.id,i.codigo,i.unidade,i.nome,i.categoria,i.preco,i.ativo,i.prato_id,p.codigo prato_codigo,p.nome prato_nome
        FROM catalogo_itens i LEFT JOIN catalogo_pratos p ON p.tenant_id=i.tenant_id AND p.id=i.prato_id
        WHERE i.tenant_id=$1 AND i.arquivado_em IS NULL ORDER BY i.id''',tenant)
    por={}
    for r in rows:
        k=normalizar(r['nome'])
        if k and (chaves is None or k in chaves): por.setdefault(k,[]).append(r)
    saida=[]
    for k in sorted(por):
        itens=por[k];unidades={r['unidade'] for r in itens};pratos={r['prato_id'] for r in itens if r['prato_id']}
        sem=[r['id'] for r in itens if not r['prato_id']]
        if len(unidades)<2 or not sem or len(pratos)>1: continue
        # Unidade que entraria duas vezes no prato: o admin escolhe quais itens ligar.
        contagem={u:sum(1 for r in itens if r['unidade']==u) for u in unidades}
        prato=next(({'id':r['prato_id'],'codigo':r['prato_codigo'],'nome':r['prato_nome']} for r in itens if r['prato_id']),None)
        saida.append({'chave':k,'nome':itens[0]['nome'],'unidades':sorted(unidades),'prato':prato,
                      'conflito':'varios_na_unidade' if any(n>1 for n in contagem.values()) else None,
                      'itens':[{'item_id':r['id'],'codigo':r['codigo'],'unidade':r['unidade'],'nome':r['nome'],'categoria':r['categoria'],
                                'preco':float(r['preco']) if r['preco'] is not None else None,'ativo':r['ativo'],'prato_id':r['prato_id']} for r in itens],
                      'sem_prato':sem})
    return saida


def _chaves(valor):
    if not isinstance(valor,list) or not valor or len(valor)>MAX_GRUPOS or any(not isinstance(k,str) or not 0<len(k)<=200 for k in valor): raise Recusa(400,'chaves')
    return list(dict.fromkeys(valor))


@gestao.post('/tenants/{tenant_id}/catalogo/pratos/sugestoes/confirmar',dependencies=[Depends(exigir_admin)])
@_rota
async def confirmar_sugestoes(request:Request,tenant_id:str,dados:Corpo,actor=Depends(painel)):
    """Confirma em lote: {"grupos":[{"chave","itens"?:[ids]}]}. Sem `itens`, liga todos os do grupo. Cada grupo à parte:
    um que não dá certo não desfaz os outros. Grupo com prato existente ganha os itens novos nele."""
    d=dict(dados.model_extra or {})
    grupos=d.get('grupos')
    if not isinstance(grupos,list) or not grupos or len(grupos)>MAX_GRUPOS or any(not isinstance(g,dict) or set(g)-{'chave','itens'} or not isinstance(g.get('chave'),str) for g in grupos):
        raise Recusa(400,'grupos')
    for g in grupos:
        if 'itens' in g: _ids(g['itens'],'itens',50)
    db=request.app.state.db;await empresa(db,tenant_id)
    atuais={g['chave']:g for g in await grupos_sugeridos(db,tenant_id,{g['chave'] for g in grupos})}
    resultados=[]
    for g in grupos:
        s=atuais.get(g['chave'])
        if s is None: resultados.append({'chave':g['chave'],'ok':False,'motivo':'sugestao_inexistente'});continue
        do_grupo={i['item_id']:i for i in s['itens']}
        ids=g.get('itens') or list(do_grupo)
        if set(ids)-set(do_grupo): resultados.append({'chave':g['chave'],'ok':False,'motivo':'item_fora_do_grupo'});continue
        unidades=[do_grupo[i]['unidade'] for i in ids]
        if len(set(unidades))!=len(unidades): resultados.append({'chave':g['chave'],'ok':False,'motivo':'varios_na_unidade'});continue
        try:
            async with db.acquire() as conn,conn.transaction():
                if s['prato']: prato=await prato_da_empresa(conn,tenant_id,s['prato']['id'],travar=True)
                else:
                    prato=await conn.fetchrow('INSERT INTO catalogo_pratos(tenant_id,codigo,nome) VALUES ($1,$2,$3) RETURNING *',
                                              tenant_id,await proximo_prato(conn,tenant_id),do_grupo[ids[0]]['nome'][:MAX_NOME])
                    await registrar(conn,tenant_id,actor,'catalogo_prato',prato['id'],'criado',None,{'codigo':prato['codigo'],'nome':prato['nome'],'origem':'sugestao'})
                ligados=await ligar(conn,tenant_id,prato,ids,actor)
                await conn.execute('DELETE FROM catalogo_sugestoes_ignoradas WHERE tenant_id=$1 AND chave=$2',tenant_id,g['chave'])
            resultados.append({'chave':g['chave'],'ok':True,'prato':{'id':prato['id'],'codigo':prato['codigo'],'nome':prato['nome']},'itens_ligados':ligados})
        except Recusa as x: resultados.append({'chave':g['chave'],'ok':False,'motivo':x.motivo or x.campo})
    if any(r['ok'] for r in resultados):
        async with db.acquire() as conn,conn.transaction():
            await auditar(conn,tenant_id,actor,'catalogo.pratos_confirmados',tenant_id)
    return {'resultados':resultados}


@gestao.post('/tenants/{tenant_id}/catalogo/pratos/sugestoes/ignorar',dependencies=[Depends(exigir_admin)])
@_rota
async def ignorar_sugestoes(request:Request,tenant_id:str,dados:Corpo,actor=Depends(painel)):
    """Ignora em lote: {"chaves":[…]}. A sugestão volta se aparecer item novo com o mesmo nome."""
    chaves=_chaves(dict(dados.model_extra or {}).get('chaves'))
    db=request.app.state.db;await empresa(db,tenant_id)
    atuais={g['chave']:g for g in await grupos_sugeridos(db,tenant_id,set(chaves))}
    async with db.acquire() as conn,conn.transaction():
        for k in chaves:
            if k in atuais:
                await conn.execute('''INSERT INTO catalogo_sugestoes_ignoradas(tenant_id,chave,itens,actor) VALUES ($1,$2,$3,$4)
                    ON CONFLICT (tenant_id,chave) DO UPDATE SET itens=EXCLUDED.itens,actor=EXCLUDED.actor,ignorado_em=now()''',tenant_id,k,atuais[k]['sem_prato'],actor)
        if any(k in atuais for k in chaves):
            await registrar(conn,tenant_id,actor,'catalogo_prato','sugestoes','ignoradas',None,{'chaves':[k for k in chaves if k in atuais]})
            await auditar(conn,tenant_id,actor,'catalogo.sugestoes_ignoradas',tenant_id)
    return {'resultados':[{'chave':k,'ok':k in atuais,**({} if k in atuais else {'motivo':'sugestao_inexistente'})} for k in chaves]}


@gestao.post('/tenants/{tenant_id}/catalogo/pratos/sugestoes/restaurar',dependencies=[Depends(exigir_admin)])
@_rota
async def restaurar_sugestoes(request:Request,tenant_id:str,dados:Corpo,actor=Depends(painel)):
    chaves=_chaves(dict(dados.model_extra or {}).get('chaves'))
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        feitas=[r['chave'] for r in await conn.fetch('DELETE FROM catalogo_sugestoes_ignoradas WHERE tenant_id=$1 AND chave=ANY($2::text[]) RETURNING chave',tenant_id,chaves)]
        if feitas:
            await registrar(conn,tenant_id,actor,'catalogo_prato','sugestoes','restauradas',None,{'chaves':feitas})
            await auditar(conn,tenant_id,actor,'catalogo.sugestoes_restauradas',tenant_id)
    return {'restauradas':feitas}


@gestao.post('/tenants/{tenant_id}/catalogo/pratos',status_code=201,dependencies=[Depends(exigir_admin)])
@_rota
async def criar_prato(request:Request,tenant_id:str,dados:Corpo,actor=Depends(painel)):
    """{"itens":[id,…],"nome"?}: um item por unidade. Nome padrão: o do primeiro item."""
    d=dict(dados.model_extra or {})
    if set(d)-{'itens','nome'}: raise Recusa(400,sorted(set(d)-{'itens','nome'})[0])
    ids=_ids(d.get('itens'),'itens',50)
    nome=_texto(d.get('nome'),'nome',MAX_NOME) if 'nome' in d else None
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        primeiro=await item_da_empresa(conn,tenant_id,ids[0])
        p=await conn.fetchrow('INSERT INTO catalogo_pratos(tenant_id,codigo,nome) VALUES ($1,$2,$3) RETURNING *',tenant_id,await proximo_prato(conn,tenant_id),nome or primeiro['nome'])
        await registrar(conn,tenant_id,actor,'catalogo_prato',p['id'],'criado',None,{'codigo':p['codigo'],'nome':p['nome']})
        await ligar(conn,tenant_id,p,ids,actor)
        await auditar(conn,tenant_id,actor,'catalogo.prato_criado',str(p['id']))
        return await prato_do_painel(conn,tenant_id,p)


@gestao.get('/tenants/{tenant_id}/catalogo/pratos/{prato_id}')
@_rota
async def um_prato(request:Request,tenant_id:str,prato_id:int):
    db=request.app.state.db;await empresa(db,tenant_id)
    return await prato_do_painel(db,tenant_id,await prato_da_empresa(db,tenant_id,prato_id))


@gestao.patch('/tenants/{tenant_id}/catalogo/pratos/{prato_id}',dependencies=[Depends(exigir_admin)])
@_rota
async def renomear_prato(request:Request,tenant_id:str,prato_id:int,dados:Corpo,actor=Depends(painel)):
    d=dict(dados.model_extra or {})
    if set(d)-{'nome'}: raise Recusa(400,sorted(set(d)-{'nome'})[0])
    nome=_texto(d.get('nome'),'nome',MAX_NOME,obrigatorio=True)
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        p=await prato_da_empresa(conn,tenant_id,prato_id,travar=True)
        if nome!=p['nome']:
            p2=await conn.fetchrow('UPDATE catalogo_pratos SET nome=$3,atualizado_em=now() WHERE tenant_id=$1 AND id=$2 RETURNING *',tenant_id,p['id'],nome)
            await registrar(conn,tenant_id,actor,'catalogo_prato',p['id'],'alterado',{'nome':p['nome']},{'nome':nome});p=p2
            await auditar(conn,tenant_id,actor,'catalogo.prato_alterado',str(p['id']))
        return await prato_do_painel(conn,tenant_id,p)


@gestao.post('/tenants/{tenant_id}/catalogo/pratos/{prato_id}/itens',dependencies=[Depends(exigir_admin)])
@_rota
async def ligar_itens(request:Request,tenant_id:str,prato_id:int,dados:Corpo,actor=Depends(painel)):
    ids=_ids(dict(dados.model_extra or {}).get('itens'),'itens',50)
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        p=await prato_da_empresa(conn,tenant_id,prato_id,travar=True)
        await ligar(conn,tenant_id,p,ids,actor)
        await auditar(conn,tenant_id,actor,'catalogo.prato_alterado',str(p['id']))
        return await prato_do_painel(conn,tenant_id,p)


@gestao.delete('/tenants/{tenant_id}/catalogo/pratos/{prato_id}/itens/{item_id}',dependencies=[Depends(exigir_admin)])
@_rota
async def desligar_item(request:Request,tenant_id:str,prato_id:int,item_id:int,actor=Depends(painel)):
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        p=await prato_da_empresa(conn,tenant_id,prato_id,travar=True)
        if not await conn.fetchval('UPDATE catalogo_itens SET prato_id=NULL,atualizado_em=clock_timestamp() WHERE tenant_id=$1 AND id=$2 AND prato_id=$3 RETURNING true',tenant_id,item_id,p['id']):
            raise Recusa(404,'item_id')
        await registrar(conn,tenant_id,actor,'catalogo_prato',p['id'],'item_desligado',{'item_id':item_id},None)
        await auditar(conn,tenant_id,actor,'catalogo.prato_alterado',str(p['id']))
        return await prato_do_painel(conn,tenant_id,p)


@gestao.delete('/tenants/{tenant_id}/catalogo/pratos/{prato_id}',dependencies=[Depends(exigir_admin)])
@_rota
async def apagar_prato(request:Request,tenant_id:str,prato_id:int,actor=Depends(painel)):
    """Só prato sem itens ligados (desligue antes). Os itens nunca são apagados."""
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn,conn.transaction():
        p=await prato_da_empresa(conn,tenant_id,prato_id,travar=True)
        n=await conn.fetchval('SELECT count(*) FROM catalogo_itens WHERE tenant_id=$1 AND prato_id=$2',tenant_id,p['id'])
        if n: raise Recusa(409,'prato_id','prato_com_itens',qtd_itens=n)
        await conn.execute('DELETE FROM catalogo_pratos WHERE tenant_id=$1 AND id=$2',tenant_id,p['id'])
        await registrar(conn,tenant_id,actor,'catalogo_prato',p['id'],'apagado',{'codigo':p['codigo'],'nome':p['nome']},None)
        await auditar(conn,tenant_id,actor,'catalogo.prato_apagado',str(p['id']))
    return {'ok':True}


@gestao.post('/tenants/{tenant_id}/catalogo/copiar',dependencies=[Depends(exigir_admin)])
@_rota
async def copiar(request:Request,tenant_id:str,dados:Corpo,actor=Depends(painel)):
    """Copia itens (ou uma categoria inteira) para outra(s) unidade(s): {"itens":[…]|"categoria_id", "destino": "morumbi"|[…], "ligar": true}.
    Cópias inativas, código com o prefixo do destino, categoria de mesmo nome (criada se faltar). Com `ligar`, origem e cópia ficam no
    mesmo prato (criado a partir do item de origem, se ele ainda não tem). Item que já tem cópia ligada no destino é pulado (`ja_existe`)."""
    d=dict(dados.model_extra or {})
    if set(d)-{'itens','categoria_id','destino','ligar'}: raise Recusa(400,sorted(set(d)-{'itens','categoria_id','destino','ligar'})[0])
    if ('itens' in d)==('categoria_id' in d): raise Recusa(400,'itens')
    destino=d.get('destino');destinos=[destino] if isinstance(destino,str) else destino
    if not isinstance(destinos,list) or not destinos or len(destinos)>50 or any(not isinstance(x,str) for x in destinos): raise Recusa(400,'destino')
    ligar_=d.get('ligar',True)
    if not isinstance(ligar_,bool): raise Recusa(400,'ligar')
    db=request.app.state.db;await empresa(db,tenant_id)
    async with db.acquire() as conn:
        if 'itens' in d: ids=_ids(d['itens'],'itens')
        else:
            c=await categoria_da_empresa(conn,tenant_id,d['categoria_id'])
            ids=[r['id'] for r in await conn.fetch('SELECT id FROM catalogo_itens WHERE tenant_id=$1 AND categoria_id=$2 AND arquivado_em IS NULL ORDER BY id',tenant_id,c['id'])]
        unidades=[await unidade_da_empresa(conn,tenant_id,x,'destino') for x in dict.fromkeys(destinos)]
        if len(ids)*len(unidades)>MAX_LOTE: raise Recusa(400,'itens','acima_do_limite',limite=MAX_LOTE)
        resultados=[]
        for item_id in ids:
            for u in unidades:
                r={'item_id':item_id,'destino':u['codigo']}
                try:
                    async with conn.transaction():
                        origem=await item_da_empresa(conn,tenant_id,item_id,travar=True)
                        if origem['unidade']==u['codigo']: raise Recusa(400,'destino','mesma_unidade')
                        prato_id=origem['prato_id']
                        if prato_id and await conn.fetchval('SELECT EXISTS(SELECT 1 FROM catalogo_itens WHERE tenant_id=$1 AND prato_id=$2 AND unidade=$3)',tenant_id,prato_id,u['codigo']):
                            raise Recusa(409,'destino','ja_existe')
                        if ligar_ and not prato_id:
                            p=await conn.fetchrow('INSERT INTO catalogo_pratos(tenant_id,codigo,nome) VALUES ($1,$2,$3) RETURNING *',tenant_id,await proximo_prato(conn,tenant_id),origem['nome'])
                            await registrar(conn,tenant_id,actor,'catalogo_prato',p['id'],'criado',None,{'codigo':p['codigo'],'nome':p['nome'],'origem':'copia'})
                            await ligar(conn,tenant_id,p,[origem['id']],actor);prato_id=p['id']
                        cid=await categoria_por_nome(conn,tenant_id,u['codigo'],origem['categoria'])
                        novo=await copiar_item(conn,tenant_id,origem,u,cid,None,actor,prato_id if ligar_ else None)
                    r.update(ok=True,novo_item_id=novo,codigo=await conn.fetchval('SELECT codigo FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant_id,novo))
                except Recusa as x: r.update(ok=False,motivo=x.motivo or x.campo)
                resultados.append(r)
        if any(r['ok'] for r in resultados):
            async with conn.transaction():
                await registrar(conn,tenant_id,actor,'catalogo_lote',tenant_id,'copia',None,{'itens':ids,'destinos':[u['codigo'] for u in unidades],
                                'copias':[r['novo_item_id'] for r in resultados if r['ok']]})
                await auditar(conn,tenant_id,actor,'catalogo.itens_copiados',tenant_id)
    return {'resultados':resultados}


# ---------------- lote ----------------

ACOES_DE_LOTE=('reajustar','mover','pausar','ativar','arquivar','desarquivar','esgotar','repor')


@gestao.post('/tenants/{tenant_id}/catalogo/lote',dependencies=[Depends(exigir_admin)])
@_rota
async def lote(request:Request,tenant_id:str,dados:Corpo,actor=Depends(painel)):
    """Até 500 itens, cada um na sua transação (um que falha não desfaz os outros). `simulacao: true` mostra sem gravar."""
    from modulos.pedidos import agendamento
    d=dict(dados.model_extra or {})
    acao=d.get('acao')
    if acao not in ACOES_DE_LOTE: raise Recusa(400,'acao')
    permitidos={'itens','acao','simulacao'}|{'reajustar':{'percentual','valor'},'mover':{'categoria_id'},'esgotar':{'ate','motivo'}}.get(acao,set())
    if set(d)-permitidos: raise Recusa(400,sorted(set(d)-permitidos)[0])
    ids=_ids(d.get('itens'),'itens')
    simulacao=d.get('simulacao',False)
    if not isinstance(simulacao,bool): raise Recusa(400,'simulacao')
    percentual=valor=None
    if acao=='reajustar':
        if ('percentual' in d)==('valor' in d): raise Recusa(400,'percentual')
        bruto=d.get('percentual',d.get('valor'))
        if isinstance(bruto,bool) or not isinstance(bruto,(int,float)): raise Recusa(400,'percentual' if 'percentual' in d else 'valor')
        if 'percentual' in d:
            percentual=Decimal(str(bruto))
            if not Decimal('-90')<=percentual<=Decimal('500'): raise Recusa(400,'percentual')
        else:
            valor=Decimal(str(bruto))
            if abs(valor)>MAX_PRECO or valor!=valor.quantize(Decimal('0.01')): raise Recusa(400,'valor')
    db=request.app.state.db;await empresa(db,tenant_id)
    resultados=[]
    async with db.acquire() as conn:
        destino=await categoria_da_empresa(conn,tenant_id,d.get('categoria_id'),campo='categoria_id') if acao=='mover' else None
        if destino and destino['arquivada_em'] is not None: raise Recusa(400,'categoria_id','categoria_arquivada')
        agora=agendamento.agora_utc()
        for item_id in ids:
            r={'item_id':item_id}
            try:
                tr=conn.transaction();await tr.start()
                try:
                    atual=await item_da_empresa(conn,tenant_id,item_id,travar=True)
                    novos=await _novos_do_lote(conn,tenant_id,atual,acao,percentual,valor,destino,d,agora)
                    depois,mudou=await gravar_item(conn,tenant_id,atual,novos,actor) if not simulacao else (None,{c:(para_o_historico(c,atual[c]),para_o_historico(c,v)) for c,v in novos.items() if para_o_historico(c,v)!=para_o_historico(c,atual[c])})
                    if acao=='mover' and mudou and not simulacao: await colocar(conn,tenant_id,destino['id'],item_id)
                    r.update(ok=True,antes={c:a for c,(a,_) in mudou.items()},depois={c:b for c,(_,b) in mudou.items()})
                except BaseException:
                    await tr.rollback();raise
                else:
                    await (tr.rollback() if simulacao else tr.commit())
            except Recusa as x: r.update(ok=False,motivo=x.motivo or x.campo)
            resultados.append(r)
        if not simulacao and any(r['ok'] and r['depois'] for r in resultados):
            async with conn.transaction():
                await registrar(conn,tenant_id,actor,'catalogo_lote',tenant_id,acao,None,{'itens':[r['item_id'] for r in resultados if r['ok'] and r['depois']],
                                **({'percentual':float(percentual)} if percentual is not None else {}),**({'valor':float(valor)} if valor is not None else {}),
                                **({'categoria_id':destino['id']} if destino else {})})
                await auditar(conn,tenant_id,actor,'catalogo.lote_'+acao,tenant_id)
    return {'simulacao':simulacao,'resultados':resultados}


async def _novos_do_lote(conn,tenant,atual,acao,percentual,valor,destino,d,agora):
    if acao=='reajustar':
        if atual['preco'] is None: raise Recusa(400,'preco','sem_preco')
        novo=atual['preco']*(1+percentual/100) if percentual is not None else atual['preco']+valor
        novo=max(Decimal('0.01'),novo.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
        if novo>MAX_PRECO: raise Recusa(400,'preco','acima_do_maximo')
        return {'preco':novo}
    if acao=='mover':
        if destino['unidade']!=atual['unidade']: raise Recusa(400,'categoria_id','unidade_diferente')
        return {'categoria_id':destino['id']}
    if acao in ('pausar','ativar'): return {'ativo':acao=='ativar'}
    if acao=='arquivar': return {'arquivado_em':atual['arquivado_em'] or agora}
    if acao=='desarquivar': return {'arquivado_em':None}
    if acao=='esgotar': return {'esgotado_ate':ate_quando(d.get('ate','fim_do_dia'),await fuso_do_item(conn,tenant,atual),agora),'esgotado_motivo':_texto(d.get('motivo'),'motivo',200)}
    return {'esgotado_ate':None,'esgotado_motivo':None}
