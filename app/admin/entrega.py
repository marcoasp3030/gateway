"""Gestão G4-a (docs/ESPEC_GESTAO.md seção 11): taxas de entrega por bairro/CEP e formas de pagamento da unidade.
Leitura para qualquer papel; escrita só com X-Role admin ou plataforma (decisão 1 do Marco). Toda escrita
entra no histórico de gestão (antes/depois) e na auditoria. Erro de validação vem com tudo o que está errado."""
from fastapi import APIRouter,Depends,Request,HTTPException
from starlette.responses import JSONResponse
from nucleo.autenticacao import painel,exigir_admin
from modulos import entrega as E
from .capacidades import empresa
from .servico import auditar
from .alteracoes import registrar
from .unidades import linha as unidade

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
B='/tenants/{tenant_id}/unidades/{uid}'


def erro_400(campo,motivo,**detalhes):
    return JSONResponse({'erro':{'codigo':'VALIDATION_ERROR','mensagem':'Confira os campos informados.','detalhes':{'campo':campo,'motivo':motivo,**detalhes}}},
                        status_code=400,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def _num(v): return float(v) if v is not None else None


def linha_api(r):
    """Linha de taxa como o painel vê (sem as colunas de comparação)."""
    base={'id':r['id'],'tipo':r['tipo']}
    if r['tipo']=='bairro': base.update(bairro=r['bairro'],apelidos=list(r['apelidos'] or []),atendido=r['atendido'])
    else: base.update(cep_inicio=r['cep_inicio'],cep_fim=r['cep_fim'])
    return {**base,'taxa':_num(r['taxa']),'ativo':r['ativo']}


def _sem_id(itens): return [{k:v for k,v in i.items() if k!='id'} for i in itens]


async def _linhas(c,t,uid):
    return await c.fetch('SELECT * FROM unidade_taxas WHERE tenant_id=$1 AND unidade_id=$2 ORDER BY ordem,id',t,uid)


def avisos_taxas(modo,itens):
    """Modo por bairro ou por CEP sem nenhuma linha ativa daquele tipo: aceito, mas na prática "a unidade confirma"."""
    if modo in ('bairro','cep') and not any(i['ativo'] and i['tipo']==modo for i in itens): return ['modo_sem_linhas']
    return []


async def visao_taxas(c,t,uid,u=None):
    u=u or await unidade(c,t,uid);itens=[linha_api(r) for r in await _linhas(c,t,uid)]
    return {'modo':u['taxa_modo'],'taxa_fixa':_num(u['taxa_entrega']),'frete_gratis_acima':_num(u['frete_gratis_acima']),'delivery_ativo':u['delivery_ativo'],
            'itens':itens,'resumo':{'bairros':sum(1 for i in itens if i['ativo'] and i['tipo']=='bairro'),
                                    'faixas_cep':sum(1 for i in itens if i['ativo'] and i['tipo']=='cep'),
                                    'inativos':sum(1 for i in itens if not i['ativo'])},
            'avisos':avisos_taxas(u['taxa_modo'],itens)}


async def _gravar_linha(c,t,uid,l,ordem,tid=None):
    valores=(l['tipo'],ordem,l.get('bairro'),E.nome_busca(l['bairro']) if l.get('bairro') else None,l.get('apelidos') or [],
             [E.nome_busca(a) for a in l.get('apelidos') or []],l['atendido'],l.get('cep_inicio'),l.get('cep_fim'),l['taxa'],l['ativo'])
    if tid is None:
        return await c.fetchrow('''INSERT INTO unidade_taxas(tenant_id,unidade_id,tipo,ordem,bairro,bairro_busca,apelidos,apelidos_busca,atendido,cep_inicio,cep_fim,taxa,ativo)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) RETURNING *''',t,uid,*valores)
    return await c.fetchrow('''UPDATE unidade_taxas SET tipo=$4,ordem=$5,bairro=$6,bairro_busca=$7,apelidos=$8,apelidos_busca=$9,atendido=$10,cep_inicio=$11,cep_fim=$12,taxa=$13,ativo=$14
        WHERE tenant_id=$1 AND unidade_id=$2 AND id=$3 RETURNING *''',t,uid,tid,*valores)


def _como_linha(r):
    """Linha gravada no formato da validação (para conferir conflitos com as novas)."""
    d=linha_api(r);d.pop('id');d.setdefault('atendido',True);return d


def _resposta_de_erros(erros,conflitos):
    motivo=(erros[0]['motivo'] if erros else conflitos[0]['motivo'])
    return erro_400('itens',motivo,erros=erros,conflitos=conflitos)


@router.get(B+'/taxas')
async def taxas(request:Request,tenant_id:str,uid:str):
    db=request.app.state.db;await empresa(db,tenant_id)
    return await visao_taxas(db,tenant_id,uid)


@router.put(B+'/taxas',dependencies=[Depends(exigir_admin)])
async def substituir_taxas(request:Request,tenant_id:str,uid:str,dados:dict,actor=Depends(painel)):
    """{"itens": [linhas]}: substitui todas, tudo ou nada. A posição no corpo vira a ordem e é a "linha" dos erros."""
    if set(dados)!={'itens'} or not isinstance(dados['itens'],list): return erro_400('itens','tipo',erros=[],conflitos=[])
    itens=dados['itens']
    if len(itens)>E.MAX_LINHAS: return erro_400('itens','limite_de_linhas',erros=[{'linha':None,'campo':'itens','motivo':'limite_de_linhas'}],conflitos=[])
    limpas=[];erros=[]
    for n,item in enumerate(itens):
        l,e=E.validar_linha_taxa(item);erros+=[{'linha':n,**x} for x in e]
        if l: limpas.append(({'linha':n},l))
    conflitos=E.conflitos_taxas([(r,l) for r,l in limpas if not any(x['linha']==r['linha'] for x in erros)])
    if erros or conflitos: return _resposta_de_erros(erros,conflitos)
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,tenant_id);u=await unidade(c,tenant_id,uid,travar=True)
        antes=[linha_api(r) for r in await _linhas(c,tenant_id,uid)]
        depois=[{**l} for _,l in limpas]
        if _sem_id(antes)!=_sem_id([linha_api_de(l) for l in depois]):
            await c.execute('DELETE FROM unidade_taxas WHERE tenant_id=$1 AND unidade_id=$2',tenant_id,uid)
            for n,l in enumerate(depois): await _gravar_linha(c,tenant_id,uid,l,n)
            await registrar(c,tenant_id,actor,'taxa',uid,'substituido',{'itens':_sem_id(antes)},{'itens':_sem_id([linha_api_de(l) for l in depois])})
            await auditar(c,tenant_id,actor,'unidade.taxas',uid)
        return await visao_taxas(c,tenant_id,uid,u)


def linha_api_de(l):
    """Linha validada (ainda sem id) no mesmo formato de linha_api."""
    base={'tipo':l['tipo']}
    if l['tipo']=='bairro': base.update(bairro=l['bairro'],apelidos=list(l['apelidos']),atendido=l['atendido'])
    else: base.update(cep_inicio=l['cep_inicio'],cep_fim=l['cep_fim'])
    return {**base,'taxa':l['taxa'],'ativo':l['ativo']}


async def _uma_linha(request,t,uid,dados,actor,tid=None):
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,t);await unidade(c,t,uid,travar=True)
        existentes=await _linhas(c,t,uid)
        atual=next((r for r in existentes if r['id']==tid),None) if tid is not None else None
        if tid is not None and not atual: raise HTTPException(404,'NOT_FOUND')
        if tid is None and len(existentes)>=E.MAX_LINHAS: return erro_400('itens','limite_de_linhas',erros=[{'linha':None,'campo':'itens','motivo':'limite_de_linhas'}],conflitos=[])
        l,e=E.validar_linha_taxa(dados,_como_linha(atual) if atual else None)
        erros=[{'linha':None,**x} for x in e]
        if erros: return _resposta_de_erros(erros,[])
        ref={'id':tid} if tid is not None else {'nova':True}
        conflitos=[x for x in E.conflitos_taxas([({'id':r['id']},_como_linha(r)) for r in existentes if r['id']!=tid]+[(ref,l)]) if _lado_e(x,ref)]
        if conflitos: return _resposta_de_erros([],conflitos)
        ordem=atual['ordem'] if atual else 1+max([r['ordem'] for r in existentes] or [-1])
        row=await _gravar_linha(c,t,uid,l,ordem,tid)
        novo=linha_api(row)
        if await registrar(c,t,actor,'taxa',f'{uid}:{row["id"]}','alterado' if atual else 'criado',
                           {k:v for k,v in linha_api(atual).items() if k!='id'} if atual else None,{k:v for k,v in novo.items() if k!='id'}):
            await auditar(c,t,actor,'unidade.taxas',uid)
        return novo


def _lado_e(conflito,ref):
    """O conflito envolve a linha enviada? (só esses importam: as gravadas já foram conferidas antes)."""
    def igual(lado): return all(lado.get(k)==v for k,v in ref.items())
    return igual(conflito['a']) or igual(conflito['b'])


@router.post(B+'/taxas',status_code=201,dependencies=[Depends(exigir_admin)])
async def criar_taxa(request:Request,tenant_id:str,uid:str,dados:dict,actor=Depends(painel)):
    return await _uma_linha(request,tenant_id,uid,dados,actor)


@router.patch(B+'/taxas/{tid}',dependencies=[Depends(exigir_admin)])
async def alterar_taxa(request:Request,tenant_id:str,uid:str,tid:int,dados:dict,actor=Depends(painel)):
    return await _uma_linha(request,tenant_id,uid,dados,actor,tid)


@router.delete(B+'/taxas/{tid}',dependencies=[Depends(exigir_admin)])
async def remover_taxa(request:Request,tenant_id:str,uid:str,tid:int,actor=Depends(painel)):
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,tenant_id);await unidade(c,tenant_id,uid,travar=True)
        row=await c.fetchrow('DELETE FROM unidade_taxas WHERE tenant_id=$1 AND unidade_id=$2 AND id=$3 RETURNING *',tenant_id,uid,tid)
        if not row: raise HTTPException(404,'NOT_FOUND')
        await registrar(c,tenant_id,actor,'taxa',f'{uid}:{tid}','removido',{k:v for k,v in linha_api(row).items() if k!='id'},None)
        await auditar(c,tenant_id,actor,'unidade.taxas',uid)
    return {'ok':True}


# ---------------- Formas de pagamento ----------------

def pagamento_api(r):
    return {'codigo':r['codigo'],'rotulo':r['rotulo'],'aceita_troco':r['aceita_troco'],'instrucoes':r['instrucoes'],'ativo':r['ativo']}


async def lista_pagamentos(c,t,uid):
    return [pagamento_api(r) for r in await c.fetch('SELECT * FROM unidade_pagamentos WHERE tenant_id=$1 AND unidade_id=$2 ORDER BY ordem,id',t,uid)]


def visao_pagamentos(itens):
    return {'itens':itens,'sugestoes':[{'codigo':k,'rotulo':v} for k,v in E.SUGESTOES.items()]}


@router.get(B+'/pagamentos')
async def pagamentos(request:Request,tenant_id:str,uid:str):
    db=request.app.state.db;await empresa(db,tenant_id);await unidade(db,tenant_id,uid)
    return visao_pagamentos(await lista_pagamentos(db,tenant_id,uid))


@router.put(B+'/pagamentos',dependencies=[Depends(exigir_admin)])
async def substituir_pagamentos(request:Request,tenant_id:str,uid:str,dados:dict,actor=Depends(painel)):
    """{"itens": [...]}: substitui a lista inteira; a ordem do corpo é a ordem mostrada ao cliente. Lista vazia =
    nenhuma forma cadastrada (o pedido segue com "a unidade confirma")."""
    if set(dados)!={'itens'}: return erro_400('itens','tipo',erros=[],bloqueios=[])
    itens,erros,bloqueios=E.validar_pagamentos(dados['itens'])
    if bloqueios and not erros:
        return erro_400('itens','texto_bloqueado',erros=[],bloqueios=bloqueios)
    if erros: return erro_400('itens',erros[0]['motivo'],erros=erros,bloqueios=bloqueios)
    async with request.app.state.db.acquire() as c,c.transaction():
        await empresa(c,tenant_id);await unidade(c,tenant_id,uid,travar=True)
        antes=await lista_pagamentos(c,tenant_id,uid)
        if antes!=itens:
            await c.execute('DELETE FROM unidade_pagamentos WHERE tenant_id=$1 AND unidade_id=$2',tenant_id,uid)
            for n,p in enumerate(itens):
                await c.execute('''INSERT INTO unidade_pagamentos(tenant_id,unidade_id,codigo,rotulo,aceita_troco,instrucoes,ativo,ordem)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''',tenant_id,uid,p['codigo'],p['rotulo'],p['aceita_troco'],p['instrucoes'],p['ativo'],n)
            await registrar(c,tenant_id,actor,'pagamento',uid,'alterado',{'itens':antes},{'itens':itens})
            await auditar(c,tenant_id,actor,'unidade.pagamentos',uid)
        return visao_pagamentos(await lista_pagamentos(c,tenant_id,uid))
