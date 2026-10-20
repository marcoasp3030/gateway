"""CAT3-b: opções do item — grupos de complementos (docs/ESPEC_CAT3.md, seção 6).

Grupo: nome, mínimo, máximo (obrigatório ⇔ mínimo ≥ 1), `repetir` (a mesma opção mais de uma vez) e opções com preço extra ≥ 0.
O agente recebe os grupos na busca e devolve ids (nunca nomes) no adicionar_item/alterar_item; aqui as escolhas são conferidas
e copiadas (nome e preço do momento) para a linha do carrinho. Limites (decisão 10): 10 grupos por item, 30 opções por grupo,
até 20 escolhas por grupo."""
import json
from decimal import Decimal,InvalidOperation
from fastapi import APIRouter,Depends,Request
from nucleo.autenticacao import painel,exigir_admin
from admin.servico import auditar
from admin.alteracoes import registrar

MAX_GRUPOS=10
MAX_OPCOES=30
MAX_ESCOLHAS=20
MAX_NOME=60
MAX_EXTRA=Decimal('9999.99')


class OpcaoRecusada(Exception):
    """Escolha que não serve: motivo da seção 6.2 e o grupo completo, para o agente perguntar de novo ao cliente."""
    def __init__(self,motivo,grupo=None): self.motivo,self.grupo=motivo,grupo


def dinheiro(v): return float(v) if v is not None else None


def para_o_agente(g,opcoes):
    return {'grupo_id':g['id'],'nome':g['nome'],'obrigatorio':g['minimo']>=1,'minimo':g['minimo'],'maximo':g['maximo'],'repetir':g['repetir'],
            'opcoes':[{'opcao_id':o['id'],'nome':o['nome'],'preco_extra':dinheiro(o['preco_extra']),'disponivel':True} for o in opcoes]}


async def _carregar(db,tenant,item_ids,so_ativos=True):
    """{item_id: [(grupo, [opções])]} na ordem de posição."""
    if not item_ids: return {}
    filtro=' AND ativo' if so_ativos else ''
    grupos=await db.fetch(f'SELECT * FROM catalogo_grupos WHERE tenant_id=$1 AND item_id=ANY($2::int[]){filtro} ORDER BY item_id,posicao,id',tenant,list(item_ids))
    if not grupos: return {}
    opcoes={}
    for o in await db.fetch(f'SELECT * FROM catalogo_opcoes WHERE tenant_id=$1 AND grupo_id=ANY($2::bigint[]){filtro} ORDER BY grupo_id,posicao,id',
                            tenant,[g['id'] for g in grupos]):
        opcoes.setdefault(o['grupo_id'],[]).append(o)
    saida={}
    for g in grupos:
        # Grupo sem nenhuma opção ativa não vai ao agente (não há o que escolher).
        if so_ativos and not opcoes.get(g['id']): continue
        saida.setdefault(g['item_id'],[]).append((g,opcoes.get(g['id'],[])))
    return saida


async def do_agente(db,tenant,item_ids):
    """{item_id: [grupos no formato da busca]} — só grupos e opções ativos. Item sem grupo não aparece (vale lista vazia)."""
    return {i:[para_o_agente(g,os) for g,os in gs] for i,gs in (await _carregar(db,tenant,item_ids)).items()}


async def escolher(db,tenant,item_id,pedidas):
    """Confere as escolhas do agente contra os grupos ativos do item. Devolve (escolhas copiadas, soma dos extras, assinatura).
    `pedidas`: [{"grupo_id","opcao_id","quantidade"?}] ou None. Recusa com OpcaoRecusada."""
    grupos=(await _carregar(db,tenant,[item_id])).get(item_id,[])
    por_grupo={g['id']:(g,{o['id']:o for o in os}) for g,os in grupos}
    contagem={}
    for p in pedidas or []:
        g=por_grupo.get(p['grupo_id'])
        if g is None: raise OpcaoRecusada('opcao_inexistente')
        if p['opcao_id'] not in g[1]: raise OpcaoRecusada('opcao_inexistente',para_o_agente(g[0],list(g[1].values())))
        chave=(p['grupo_id'],p['opcao_id'])
        contagem[chave]=contagem.get(chave,0)+int(p.get('quantidade') or 1)
    escolhas=[];extras=Decimal('0')
    for g,os in grupos:
        publico=para_o_agente(g,os)
        minhas=[(oid,n) for (gid,oid),n in contagem.items() if gid==g['id']]
        if not g['repetir'] and any(n>1 for _,n in minhas): raise OpcaoRecusada('opcao_repetida',publico)
        total=sum(n for _,n in minhas)
        if total>g['maximo']: raise OpcaoRecusada('opcoes_acima_do_maximo',publico)
        if total<g['minimo']: raise OpcaoRecusada('opcao_obrigatoria' if total==0 else 'opcoes_abaixo_do_minimo',publico)
        ordem={o['id']:n for n,o in enumerate(os)}
        for oid,n in sorted(minhas,key=lambda x: ordem[x[0]]):
            o={x['id']:x for x in os}[oid]
            escolhas.append({'grupo_id':g['id'],'grupo':g['nome'],'opcao_id':oid,'opcao':o['nome'],'quantidade':n,'preco_extra':dinheiro(o['preco_extra'])})
            extras+=o['preco_extra']*n
    assinatura=json.dumps(sorted([e['grupo_id'],e['opcao_id'],e['quantidade']] for e in escolhas)) if escolhas else ''
    return escolhas,extras,assinatura


def ler(valor):
    """Escolhas gravadas na linha do carrinho ou no pedido (JSON) → lista."""
    if valor is None: return []
    return json.loads(valor) if isinstance(valor,str) else list(valor)


# ---------------- painel ----------------

opcoes_admin=APIRouter()


def _recusa(campo,motivo=None,**extra):
    from .gestao import Recusa
    return Recusa(400,campo,motivo,**extra)


async def do_painel(db,tenant,item):
    grupos=(await _carregar(db,tenant,[item['id']],so_ativos=False)).get(item['id'],[])
    return {'item_id':item['id'],'atualizado_em':item['opcoes_atualizado_em'],
            'grupos':[{'id':g['id'],'nome':g['nome'],'obrigatorio':g['minimo']>=1,'minimo':g['minimo'],'maximo':g['maximo'],'repetir':g['repetir'],
                       'posicao':g['posicao'],'ativo':g['ativo'],
                       'opcoes':[{'id':o['id'],'nome':o['nome'],'preco_extra':dinheiro(o['preco_extra']),'codigo':o['codigo'],'posicao':o['posicao'],'ativo':o['ativo']}
                                 for o in os]} for g,os in grupos]}


def _validar(grupos):
    """Conjunto inteiro de grupos do PUT, validado. 400 com o campo (grupos[n].campo) no primeiro problema."""
    if not isinstance(grupos,list) or len(grupos)>MAX_GRUPOS: raise _recusa('grupos','acima_do_limite' if isinstance(grupos,list) else None)
    saida=[]
    for n,g in enumerate(grupos):
        c=f'grupos[{n}]'
        if not isinstance(g,dict) or set(g)-{'id','nome','minimo','maximo','repetir','ativo','opcoes'}: raise _recusa(c)
        nome=g.get('nome')
        if not isinstance(nome,str) or not nome.strip() or len(nome.strip())>MAX_NOME: raise _recusa(c+'.nome')
        minimo,maximo=g.get('minimo',0),g.get('maximo',1)
        if any(isinstance(v,bool) or not isinstance(v,int) for v in (minimo,maximo)) or not (0<=minimo<=maximo<=MAX_ESCOLHAS and maximo>=1): raise _recusa(c+'.minimo')
        for campo in ('repetir','ativo'):
            if campo in g and not isinstance(g[campo],bool): raise _recusa(f'{c}.{campo}')
        if 'id' in g and (isinstance(g['id'],bool) or not isinstance(g['id'],int)): raise _recusa(c+'.id')
        opcoes=g.get('opcoes')
        if not isinstance(opcoes,list) or not opcoes or len(opcoes)>MAX_OPCOES: raise _recusa(c+'.opcoes','acima_do_limite' if isinstance(opcoes,list) and opcoes else None)
        os=[]
        for m,o in enumerate(opcoes):
            co=f'{c}.opcoes[{m}]'
            if not isinstance(o,dict) or set(o)-{'id','nome','preco_extra','codigo','ativo'}: raise _recusa(co)
            if not isinstance(o.get('nome'),str) or not o['nome'].strip() or len(o['nome'].strip())>MAX_NOME: raise _recusa(co+'.nome')
            bruto=o.get('preco_extra',0)
            if isinstance(bruto,bool) or not isinstance(bruto,(int,float,str)): raise _recusa(co+'.preco_extra')
            try: extra=Decimal(str(bruto).strip().replace(',','.'))
            except InvalidOperation: raise _recusa(co+'.preco_extra') from None
            if not extra.is_finite() or extra<0 or extra>MAX_EXTRA or extra!=extra.quantize(Decimal('0.01')): raise _recusa(co+'.preco_extra')
            codigo=o.get('codigo')
            import re
            if codigo is not None and (not isinstance(codigo,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,39}',codigo)): raise _recusa(co+'.codigo')
            if 'ativo' in o and not isinstance(o['ativo'],bool): raise _recusa(co+'.ativo')
            if 'id' in o and (isinstance(o['id'],bool) or not isinstance(o['id'],int)): raise _recusa(co+'.id')
            os.append({'id':o.get('id'),'nome':o['nome'].strip(),'preco_extra':extra,'codigo':codigo,'ativo':o.get('ativo',True)})
        saida.append({'id':g.get('id'),'nome':nome.strip(),'minimo':minimo,'maximo':maximo,'repetir':g.get('repetir',False),'ativo':g.get('ativo',True),'opcoes':os})
    return saida


async def gravar(conn,tenant,item_id,grupos,manter_ids=True):
    """Substitui o conjunto de grupos do item. Com `manter_ids`, grupo/opção com id do próprio item é atualizado (o id não muda);
    o que não veio é apagado (carrinhos e pedidos guardam a cópia; o fechamento confere de novo)."""
    atuais={g['id']:g for g in await conn.fetch('SELECT id FROM catalogo_grupos WHERE tenant_id=$1 AND item_id=$2',tenant,item_id)}
    atuais_op={o['id']:o['grupo_id'] for o in await conn.fetch('SELECT id,grupo_id FROM catalogo_opcoes WHERE tenant_id=$1 AND grupo_id=ANY($2::bigint[])',tenant,list(atuais))}
    vistos=set()
    for n,g in enumerate(grupos,1):
        gid=g['id'] if manter_ids else None
        if gid is not None and gid not in atuais: raise _recusa(f'grupos[{n-1}].id','grupo_de_outro_item')
        if gid is None:
            gid=await conn.fetchval('INSERT INTO catalogo_grupos(tenant_id,item_id,nome,minimo,maximo,repetir,posicao,ativo) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id',
                                    tenant,item_id,g['nome'],g['minimo'],g['maximo'],g['repetir'],n,g['ativo'])
        else:
            await conn.execute('UPDATE catalogo_grupos SET nome=$3,minimo=$4,maximo=$5,repetir=$6,posicao=$7,ativo=$8 WHERE tenant_id=$1 AND id=$2',
                               tenant,gid,g['nome'],g['minimo'],g['maximo'],g['repetir'],n,g['ativo'])
        vistos.add(gid);ops=set()
        for m,o in enumerate(g['opcoes'],1):
            oid=o['id'] if manter_ids else None
            if oid is not None and atuais_op.get(oid)!=gid: raise _recusa(f'grupos[{n-1}].opcoes[{m-1}].id','opcao_de_outro_grupo')
            if oid is None:
                oid=await conn.fetchval('INSERT INTO catalogo_opcoes(tenant_id,grupo_id,nome,preco_extra,codigo,posicao,ativo) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id',
                                        tenant,gid,o['nome'],o['preco_extra'],o['codigo'],m,o['ativo'])
            else:
                await conn.execute('UPDATE catalogo_opcoes SET nome=$3,preco_extra=$4,codigo=$5,posicao=$6,ativo=$7 WHERE tenant_id=$1 AND id=$2',
                                   tenant,oid,o['nome'],o['preco_extra'],o['codigo'],m,o['ativo'])
            ops.add(oid)
        await conn.execute('DELETE FROM catalogo_opcoes WHERE tenant_id=$1 AND grupo_id=$2 AND NOT (id=ANY($3::bigint[]))',tenant,gid,list(ops))
    await conn.execute('DELETE FROM catalogo_grupos WHERE tenant_id=$1 AND item_id=$2 AND NOT (id=ANY($3::bigint[]))',tenant,item_id,list(vistos))
    await conn.execute('UPDATE catalogo_itens SET opcoes_atualizado_em=clock_timestamp() WHERE tenant_id=$1 AND id=$2',tenant,item_id)


def _resumo(conjunto):
    """Para o histórico: o conjunto como o painel vê, sem ids."""
    return [{'nome':g['nome'],'minimo':g['minimo'],'maximo':g['maximo'],'repetir':g['repetir'],'ativo':g['ativo'],
             'opcoes':[{'nome':o['nome'],'preco_extra':o['preco_extra'],'ativo':o['ativo']} for o in g['opcoes']]} for g in conjunto]


@opcoes_admin.get('/tenants/{tenant_id}/catalogo/itens/{item_id}/opcoes')
async def ver_opcoes(request:Request,tenant_id:str,item_id:int):
    from .gestao import _rota,empresa,item_da_empresa
    @_rota
    async def ler_():
        db=request.app.state.db;await empresa(db,tenant_id)
        item=await db.fetchrow('SELECT id,opcoes_atualizado_em FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant_id,item_id)
        if item is None: await item_da_empresa(db,tenant_id,item_id)
        return await do_painel(db,tenant_id,item)
    return await ler_()


@opcoes_admin.put('/tenants/{tenant_id}/catalogo/itens/{item_id}/opcoes',dependencies=[Depends(exigir_admin)])
async def trocar_opcoes(request:Request,tenant_id:str,item_id:int,actor=Depends(painel)):
    """Conjunto inteiro: {"grupos":[{"id"?, "nome", "minimo", "maximo", "repetir"?, "ativo"?, "opcoes":[{"id"?, "nome", "preco_extra", "codigo"?, "ativo"?}]}],
    "atualizado_em"?, "aplicar_em"?}. A ordem da lista é a posição. Com `atualizado_em` diferente do atual → 409 com o conjunto atual.
    `aplicar_em` copia o conjunto para os itens do mesmo prato (substitui o deles)."""
    from .gestao import _rota,empresa,item_da_empresa,alvos,Recusa
    from fastapi.encoders import jsonable_encoder
    from starlette.responses import JSONResponse
    @_rota
    async def trocar():
        try: d=await request.json()
        except ValueError: raise _recusa('corpo') from None
        if not isinstance(d,dict) or set(d)-{'grupos','atualizado_em','aplicar_em'}: raise _recusa('corpo')
        grupos=_validar(d.get('grupos'))
        db=request.app.state.db;await empresa(db,tenant_id)
        async with db.acquire() as conn,conn.transaction():
            item=await item_da_empresa(conn,tenant_id,item_id,travar=True)
            marca=await conn.fetchval('SELECT opcoes_atualizado_em FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant_id,item_id)
            if d.get('atualizado_em') is not None and d['atualizado_em']!=(jsonable_encoder(marca) if marca else None):
                corpo={'erro':{'codigo':'CONFLICT','mensagem':'As opções foram alteradas por outra pessoa. Confira e salve de novo.',
                               'detalhes':{'campo':'atualizado_em','motivo':'opcoes_alteradas'}},
                       'opcoes':await do_painel(conn,tenant_id,{'id':item_id,'opcoes_atualizado_em':marca})}
                return JSONResponse(jsonable_encoder(corpo),status_code=409,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
            lista=await alvos(conn,tenant_id,item,d.get('aplicar_em'))
            antes=await do_painel(conn,tenant_id,{'id':item_id,'opcoes_atualizado_em':marca})
            await gravar(conn,tenant_id,item_id,grupos)
            depois=await do_painel(conn,tenant_id,await conn.fetchrow('SELECT id,opcoes_atualizado_em FROM catalogo_itens WHERE tenant_id=$1 AND id=$2',tenant_id,item_id))
            await registrar(conn,tenant_id,actor,'catalogo_opcoes',item_id,'alterado',{'grupos':_resumo(antes['grupos'])},{'grupos':_resumo(depois['grupos'])})
            await auditar(conn,tenant_id,actor,'catalogo.opcoes_alteradas',str(item_id))
            aplicados=[]
            for r in lista:
                try:
                    async with conn.transaction():
                        await gravar(conn,tenant_id,r['id'],grupos,manter_ids=False)
                        await registrar(conn,tenant_id,actor,'catalogo_opcoes',r['id'],'alterado',None,{'grupos':_resumo(depois['grupos']),'copiado_de':item_id})
                    aplicados.append({'unidade':r['unidade'],'item_id':r['id'],'ok':True})
                except Recusa as x: aplicados.append({'unidade':r['unidade'],'item_id':r['id'],'ok':False,'motivo':x.motivo or x.campo})
            if d.get('aplicar_em') not in (None,'este'): depois['aplicado_em']=aplicados
            return depois
    return await trocar()
