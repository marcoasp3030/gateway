"""Configuração de atendimento por empresa e por agente (CFG1-a): rascunho, publicação, versões, restauração e prévia.

O que roda em produção é sempre o publicado: os comportamentos ficam nas colunas de sempre (o motor não muda)
e o perfil numa coluna própria de cada escopo. Cada publicação grava uma versão com o antes e o depois."""
import json
from typing import Literal
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from starlette.responses import JSONResponse
from nucleo.autenticacao import painel,papel,exigir_admin
from nucleo.banco import Repositorio,registro
from nucleo.configuracao import Configuracoes,PADROES
from nucleo import atendimento as A,horarios
from nucleo.atendimento import COMPORTAMENTOS
from .modelos import Comportamento
from .servico import auditar

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
MAX_NOTA=300


class Estrito(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Rascunho(Estrito):
    baseado_em:int|None=Field(None,ge=0)
    perfil:dict|None=None
    comportamentos:dict|None=None


class Publicar(Estrito):
    baseado_em:int=Field(ge=0)
    nota:str|None=Field(None,max_length=MAX_NOTA)
    confirmar_avisos:bool=False


class Restaurar(Estrito):
    nota:str|None=Field(None,max_length=MAX_NOTA)


class Previa(Estrito):
    fonte:Literal['rascunho','publicado']='rascunho'


def _json(valor): return json.loads(valor) if isinstance(valor,str) else valor


def erro(status,codigo,mensagem,detalhes):
    return JSONResponse({'erro':{'codigo':codigo,'mensagem':mensagem,'detalhes':detalhes}},status_code=status,
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def definicao_do_escopo(linha):
    """O que o escopo (linha de tenant_config ou de agentes) define hoje: perfil publicado e comportamentos não nulos."""
    return {'perfil':{k:v for k,v in (_json(linha.get('perfil')) or {}).items() if v is not None},
            'comportamentos':{c:_json(linha[c]) if c=='transbordo_horario_equipe' else linha[c] for c in COMPORTAMENTOS if linha.get(c) is not None}}


async def registrar_versao(conn,tenant,agente,conteudo,alteracoes,origem,actor,nota=None,restaurada_de=None):
    """Grava a próxima versão do escopo e marca o número em vigor. Devolve o número."""
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'atendimento:{tenant}:{agente or ""}')
    n=1+await conn.fetchval("SELECT coalesce(max(versao),0) FROM atendimento_versoes WHERE tenant_id=$1 AND coalesce(agente_id,'')=coalesce($2,'')",tenant,agente)
    await conn.execute('''INSERT INTO atendimento_versoes(tenant_id,agente_id,versao,conteudo,alteracoes,origem,restaurada_de,nota,publicado_por)
        VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8,$9)''',tenant,agente,n,json.dumps(conteudo,ensure_ascii=False),json.dumps(alteracoes,ensure_ascii=False,default=str),
        origem,restaurada_de,nota,actor[:100])
    if agente: await conn.execute('UPDATE agentes SET perfil_versao=$3 WHERE tenant_id=$1 AND id=$2',tenant,agente,n)
    else: await conn.execute('UPDATE tenant_config SET perfil_versao=$2 WHERE tenant_id=$1',tenant,n)
    return n


def validar_comportamentos(dados):
    """(limpo, erros) dos comportamentos que o escopo define. None = herda. Só os que a empresa pode ajustar."""
    if not isinstance(dados,dict): return {},[{'campo':'comportamentos','motivo':'tipo'}]
    erros=[{'campo':'comportamentos.'+str(c)[:40],'motivo':'so_plataforma' if c in A.SO_PLATAFORMA else 'campo_desconhecido'} for c in dados if c not in COMPORTAMENTOS]
    conhecidos={c:v for c,v in dados.items() if c in COMPORTAMENTOS}
    try: limpo=Comportamento.model_validate(conhecidos).model_dump(include=set(conhecidos))
    except ValidationError as exc:
        ruins={str(e['loc'][0]) for e in exc.errors() if e.get('loc')}
        erros+=[{'campo':'comportamentos.'+c,'motivo':'valor'} for c in sorted(ruins)]
        limpo={c:v for c,v in conhecidos.items() if c not in ruins}
    for campo,(grupo,tipo,minimo,maximo,opcoes,disponivel) in COMPORTAMENTOS.items():
        valor=limpo.get(campo)
        if valor is None or any(e['campo']=='comportamentos.'+campo for e in erros): continue
        ruim=False
        if tipo in ('inteiro','decimal'): ruim=not minimo<=valor<=maximo
        elif tipo=='texto':
            ruim=not minimo<=len(valor.strip())<=maximo or any(ord(c)<32 and c!='\n' for c in valor)
            if campo=='fallback' and (len(valor.strip())<15 or not any(c.isalpha() for c in valor)): ruim=True
        elif tipo=='horarios':
            try: horarios.validar_sem_sobreposicao(valor)
            except Exception: ruim=True
        elif tipo=='lista_escolhas': ruim=len(set(valor))!=len(valor)
        if ruim: erros.append({'campo':'comportamentos.'+campo,'motivo':'valor'})
    return limpo,erros


async def _escopo(request,tenant,agente,conn=None):
    """Linha do escopo (tenant_config ou agente) e a de cima; 404 se a empresa ou o agente não existem."""
    repo=Repositorio(conn or request.app.state.db)
    empresa=await repo.tenant(tenant)
    if not empresa: raise HTTPException(404,'NOT_FOUND')
    config=await repo.obter(tenant,'tenant_config',tenant) or {}
    if agente is None: return repo,empresa,config,None
    linha=await repo.obter(tenant,'agentes',agente)
    if not linha: raise HTTPException(404,'NOT_FOUND')
    return repo,empresa,config,linha


def aplicar(definicao,rascunho):
    """Definição do escopo depois do rascunho: campo com valor substitui; campo nulo volta a herdar."""
    nova={'perfil':dict(definicao['perfil']),'comportamentos':dict(definicao['comportamentos'])}
    for parte in ('perfil','comportamentos'):
        for campo,valor in ((rascunho or {}).get(parte) or {}).items():
            if valor is None: nova[parte].pop(campo,None)
            else: nova[parte][campo]=valor
    return nova


def diferencas(antes,depois):
    saida=[]
    for parte,campos in (('perfil',A.PERFIL),('comportamentos',COMPORTAMENTOS)):
        for campo in campos:
            a,d=antes[parte].get(campo),depois[parte].get(campo)
            if a!=d: saida.append({'campo':f'{parte}.{campo}','antes':a,'depois':d})
    return saida


async def _efetivo(request,repo,tenant,agente,definicao=None):
    """Valores finais depois da herança e de onde veio cada um. `definicao` simula o escopo com outro conteúdo (rascunho)."""
    cfgs=Configuracoes(repo)
    if definicao is not None:
        # Simulação: troca, só nesta leitura, o que o escopo define.
        original=repo.obter
        async def obter(t,tabela,ident):
            linha=await original(t,tabela,ident)
            if linha and ((agente is None and tabela=='tenant_config') or (agente is not None and tabela=='agentes' and ident==agente)):
                linha={**linha,**{c:None for c in COMPORTAMENTOS},**definicao['comportamentos'],'perfil':definicao['perfil']}
            return linha
        repo.obter=obter
    try:
        if agente is None: cfg,origem,perfil,origem_perfil,versao=await cfgs.da_empresa(tenant)
        else:
            e=await cfgs.efetiva(tenant,agente);cfg,origem,perfil,origem_perfil,versao=e,e['origem_config'],e['perfil_atendimento'],e['origem_perfil'],e['perfil_versao']
    finally:
        if definicao is not None: repo.obter=original
    comportamentos={c:cfg.get(c) for c in list(COMPORTAMENTOS)+list(A.SO_PLATAFORMA)}
    return {'perfil':perfil,'comportamentos':comportamentos,
            'origem':{'perfil':origem_perfil,'comportamentos':{c:A.ORIGENS[origem.get(c,'plataforma')] for c in comportamentos}}},versao


async def _rascunho(conn,tenant,agente):
    r=await conn.fetchrow("SELECT * FROM atendimento_rascunhos WHERE tenant_id=$1 AND coalesce(agente_id,'')=coalesce($2,'')",tenant,agente)
    return {**dict(r),'conteudo':_json(r['conteudo'])} if r else None


async def _visao(request,tenant,agente,avisos=None):
    db=request.app.state.db
    repo,empresa,config,linha=await _escopo(request,tenant,agente)
    alvo=linha if agente is not None else config
    definicao=definicao_do_escopo(alvo);versao=alvo.get('perfil_versao') or 0
    ultima=await db.fetchrow("SELECT publicado_em,publicado_por FROM atendimento_versoes WHERE tenant_id=$1 AND coalesce(agente_id,'')=coalesce($2,'') ORDER BY versao DESC LIMIT 1",tenant,agente)
    efetivo,texto=await _efetivo(request,repo,tenant,agente)
    r=await _rascunho(db,tenant,agente);quem=papel(request)
    saida={'escopo':'agente' if agente is not None else 'empresa','segmento':(linha or {}).get('modelo_id') if agente is not None else empresa.get('segmento'),
           'versao_publicada':versao,'publicado_em':ultima['publicado_em'] if ultima else None,'publicado_por':ultima['publicado_por'] if ultima else None,
           'perfil_versao':texto,'publicado':definicao,'rascunho':None,'efetivo':efetivo,
           'esquema':A.esquema(PADROES,quem),'pode_editar':quem in ('admin','plataforma')}
    # Personalizado = definido neste escopo; o resto vem de cima (segmento, plataforma ou, no agente, da empresa).
    aqui='agente' if agente is not None else 'empresa'
    saida['personalizado']={'perfil':sorted(c for c,o in efetivo['origem']['perfil'].items() if o==aqui),
                            'comportamentos':sorted(c for c,o in efetivo['origem']['comportamentos'].items() if o==aqui and c in COMPORTAMENTOS)}
    if r:
        futuro=aplicar(definicao,r['conteudo']);previsto,_=await _efetivo(request,repo,tenant,agente,futuro)
        bloqueios,duvidas=A.analisar_perfil(futuro['perfil'])
        saida['rascunho']={'baseado_em':r['baseado_em'],'desatualizado':r['baseado_em']!=versao,'atualizado_em':r['atualizado_em'],'atualizado_por':r['atualizado_por'],
                           'perfil':r['conteudo'].get('perfil') or {},'comportamentos':r['conteudo'].get('comportamentos') or {},
                           'efetivo':previsto,'alteracoes':diferencas(definicao,futuro),'bloqueios':bloqueios,'avisos':duvidas}
    return saida


ESCOPOS=(('/tenants/{tenant_id}/atendimento',False),('/tenants/{tenant_id}/agentes/{agente_id}/atendimento',True))


def nas_duas(metodo,sufixo='',**opcoes):
    """Registra a mesma rota para a empresa e para o agente."""
    def decorador(funcao):
        for caminho,_ in ESCOPOS: getattr(router,metodo)(caminho+sufixo,**opcoes)(funcao)
        return funcao
    return decorador


@nas_duas('get')
async def ler(request:Request,tenant_id:str,agente_id:str|None=None):
    return await _visao(request,tenant_id,agente_id)


@nas_duas('put','/rascunho',dependencies=[Depends(exigir_admin)])
async def salvar_rascunho(request:Request,tenant_id:str,dados:Rascunho,agente_id:str|None=None,actor=Depends(painel)):
    """Salva só os campos enviados (nulo = voltar ao padrão de cima). Nada muda em produção."""
    perfil,erros=A.validar_perfil(dados.perfil or {})
    comportamentos,erros_c=validar_comportamentos(dados.comportamentos or {})
    if erros+erros_c: return erro(400,'VALIDATION_ERROR','Há campos inválidos.',{'campos':erros+erros_c})
    async with request.app.state.db.acquire() as conn,conn.transaction():
        repo,empresa,config,linha=await _escopo(request,tenant_id,agente_id,conn)
        atual=(linha if agente_id is not None else config).get('perfil_versao') or 0
        r=await _rascunho(conn,tenant_id,agente_id)
        conteudo={'perfil':{**((r or {}).get('conteudo',{}).get('perfil') or {}),**perfil},
                  'comportamentos':{**((r or {}).get('conteudo',{}).get('comportamentos') or {}),**comportamentos}}
        base=dados.baseado_em if dados.baseado_em is not None else (r['baseado_em'] if r else atual)
        await conn.execute("DELETE FROM atendimento_rascunhos WHERE tenant_id=$1 AND coalesce(agente_id,'')=coalesce($2,'')",tenant_id,agente_id)
        await conn.execute('INSERT INTO atendimento_rascunhos(tenant_id,agente_id,baseado_em,conteudo,atualizado_por) VALUES ($1,$2,$3,$4::jsonb,$5)',
                           tenant_id,agente_id,base,json.dumps(conteudo,ensure_ascii=False),actor[:100])
    return await _visao(request,tenant_id,agente_id)


@nas_duas('delete','/rascunho',dependencies=[Depends(exigir_admin)])
async def descartar_rascunho(request:Request,tenant_id:str,agente_id:str|None=None,actor=Depends(painel)):
    await _escopo(request,tenant_id,agente_id)
    await request.app.state.db.execute("DELETE FROM atendimento_rascunhos WHERE tenant_id=$1 AND coalesce(agente_id,'')=coalesce($2,'')",tenant_id,agente_id)
    return await _visao(request,tenant_id,agente_id)


async def _gravar(request,conn,repo,tenant,agente,empresa,antes,nova,origem,actor,nota,restaurada_de=None):
    """Escreve a definição nova nas colunas do escopo, revalida os agentes afetados e registra a versão."""
    tabela,ident=('agentes',agente) if agente is not None else ('tenant_config',tenant)
    await repo.alterar(tenant,tabela,ident,{c:nova['comportamentos'].get(c) for c in COMPORTAMENTOS})
    await conn.execute(f'UPDATE {tabela} SET perfil=$3::jsonb WHERE tenant_id=$1 AND {"id" if agente is not None else "tenant_id"}=$2',tenant,ident,json.dumps(nova['perfil'],ensure_ascii=False))
    # As mesmas conferências de sempre (ex.: teto de espera não menor que a espera) antes de confirmar.
    for a in await conn.fetch('SELECT * FROM agentes WHERE tenant_id=$1 AND ($2::text IS NULL OR id=$2)',tenant,agente):
        await request.app.state.admin.checar_agente(repo,empresa,registro(a))
    alteracoes=diferencas(antes,nova)
    n=await registrar_versao(conn,tenant,agente,nova,alteracoes,origem,actor,nota,restaurada_de)
    await auditar(conn,tenant,actor,'atendimento.restaurado' if origem=='restauracao' else 'atendimento.publicado',
                  (f'{agente}:' if agente else 'empresa:')+f'v{n}')
    return n


@nas_duas('post','/publicar',dependencies=[Depends(exigir_admin)])
async def publicar(request:Request,tenant_id:str,dados:Publicar,agente_id:str|None=None,actor=Depends(painel)):
    async with request.app.state.db.acquire() as conn,conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'atendimento:{tenant_id}:{agente_id or ""}')
        repo,empresa,config,linha=await _escopo(request,tenant_id,agente_id,conn)
        alvo=linha if agente_id is not None else config;atual=alvo.get('perfil_versao') or 0
        r=await _rascunho(conn,tenant_id,agente_id)
        if not r: return erro(409,'CONFLICT','Não há rascunho para publicar.',{'motivo':'sem_rascunho','versao_publicada':atual})
        if dados.baseado_em!=atual or r['baseado_em']!=atual:
            return erro(409,'CONFLICT','Outra versão foi publicada depois deste rascunho.',{'motivo':'versao_desatualizada','versao_publicada':atual})
        antes=definicao_do_escopo(alvo);nova=aplicar(antes,r['conteudo'])
        # Revalida tudo: as regras podem ter mudado desde que o rascunho foi salvo.
        _,e1=A.validar_perfil(nova['perfil']);_,e2=validar_comportamentos(nova['comportamentos'])
        if e1+e2: return erro(400,'VALIDATION_ERROR','Há campos inválidos.',{'campos':e1+e2})
        bloqueios,duvidas=A.analisar_perfil(nova['perfil'])
        # A confirmação vale para o que está mudando agora; trecho já confirmado numa versão anterior não volta a pedir.
        mudando={d['campo'] for d in diferencas(antes,nova)}
        duvidas=[d for d in duvidas if d['campo'].split('[')[0] in mudando]
        if bloqueios: return erro(400,'VALIDATION_ERROR','O texto parece instrução de sistema e não pode ser publicado.',{'motivo':'texto_bloqueado','bloqueios':bloqueios})
        if duvidas and not dados.confirmar_avisos:
            return erro(409,'CONFLICT','Confirme os trechos indicados para publicar.',{'motivo':'confirmacao_necessaria','avisos':duvidas})
        if not diferencas(antes,nova): return erro(409,'CONFLICT','O rascunho não muda nada.',{'motivo':'sem_alteracoes','versao_publicada':atual})
        await _gravar(request,conn,repo,tenant_id,agente_id,empresa,antes,nova,'publicacao',actor,(dados.nota or '').strip() or None)
        await conn.execute("DELETE FROM atendimento_rascunhos WHERE tenant_id=$1 AND coalesce(agente_id,'')=coalesce($2,'')",tenant_id,agente_id)
    request.app.state.configuracoes.invalidar(tenant_id)
    return await _visao(request,tenant_id,agente_id)


def _resumo(v):
    return {'versao':v['versao'],'publicado_em':v['publicado_em'],'publicado_por':v['publicado_por'],'nota':v['nota'],'origem':v['origem'],
            'restaurada_de':v['restaurada_de'],'campos_alterados':[a['campo'] for a in _json(v['alteracoes'])]}


@nas_duas('get','/versoes')
async def versoes(request:Request,tenant_id:str,agente_id:str|None=None,limite:int=20,cursor:int=0):
    await _escopo(request,tenant_id,agente_id)
    if not 1<=limite<=100 or cursor<0: raise HTTPException(400,'VALIDATION_ERROR')
    rows=await request.app.state.db.fetch("""SELECT * FROM atendimento_versoes WHERE tenant_id=$1 AND coalesce(agente_id,'')=coalesce($2,'')
        AND ($3=0 OR versao<$3) ORDER BY versao DESC LIMIT $4""",tenant_id,agente_id,cursor,limite+1)
    return {'itens':[_resumo(v) for v in rows[:limite]],'proximo_cursor':rows[limite-1]['versao'] if len(rows)>limite else None}


async def _versao(db,tenant,agente,n):
    v=await db.fetchrow("SELECT * FROM atendimento_versoes WHERE tenant_id=$1 AND coalesce(agente_id,'')=coalesce($2,'') AND versao=$3",tenant,agente,n)
    if not v: raise HTTPException(404,'NOT_FOUND')
    return v


@nas_duas('get','/versoes/{versao}')
async def uma_versao(request:Request,tenant_id:str,versao:int,agente_id:str|None=None):
    await _escopo(request,tenant_id,agente_id)
    v=await _versao(request.app.state.db,tenant_id,agente_id,versao);conteudo=_json(v['conteudo'])
    return {**_resumo(v),'perfil':conteudo.get('perfil') or {},'comportamentos':conteudo.get('comportamentos') or {},'alteracoes':_json(v['alteracoes'])}


@nas_duas('post','/versoes/{versao}/restaurar',dependencies=[Depends(exigir_admin)])
async def restaurar(request:Request,tenant_id:str,versao:int,dados:Restaurar|None=None,agente_id:str|None=None,actor=Depends(painel)):
    """Cria uma versão NOVA com o conteúdo da antiga. O histórico nunca é reescrito."""
    async with request.app.state.db.acquire() as conn,conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'atendimento:{tenant_id}:{agente_id or ""}')
        repo,empresa,config,linha=await _escopo(request,tenant_id,agente_id,conn)
        alvo=linha if agente_id is not None else config
        v=await _versao(conn,tenant_id,agente_id,versao);conteudo=_json(v['conteudo'])
        antes=definicao_do_escopo(alvo);nova={'perfil':conteudo.get('perfil') or {},'comportamentos':conteudo.get('comportamentos') or {}}
        if not diferencas(antes,nova): return erro(409,'CONFLICT','Esta versão já é a que está em vigor.',{'motivo':'sem_alteracoes','versao_publicada':alvo.get('perfil_versao') or 0})
        # Versão antiga é revalidada pelas regras de hoje; texto que hoje seria bloqueado não volta.
        _,e1=A.validar_perfil(nova['perfil']);_,e2=validar_comportamentos(nova['comportamentos']);bloqueios,_=A.analisar_perfil(nova['perfil'])
        if e1+e2 or bloqueios: return erro(400,'VALIDATION_ERROR','Esta versão não passa nas regras atuais.',{'campos':e1+e2,'bloqueios':bloqueios})
        nota=((dados.nota if dados else None) or '').strip() or f'Restaurada da versão {versao}'
        await _gravar(request,conn,repo,tenant_id,agente_id,empresa,antes,nova,'restauracao',actor,nota,versao)
    request.app.state.configuracoes.invalidar(tenant_id)
    return await _visao(request,tenant_id,agente_id)


@nas_duas('post','/previa')
async def previa(request:Request,tenant_id:str,dados:Previa|None=None,agente_id:str|None=None):
    """O perfil exatamente como irá no pacote do agente. Nada é gravado; qualquer papel pode ver."""
    fonte=dados.fonte if dados else 'rascunho'
    repo,empresa,config,linha=await _escopo(request,tenant_id,agente_id)
    alvo=linha if agente_id is not None else config
    r=await _rascunho(request.app.state.db,tenant_id,agente_id) if fonte=='rascunho' else None
    efetivo,versao=await _efetivo(request,repo,tenant_id,agente_id,aplicar(definicao_do_escopo(alvo),r['conteudo']) if r else None)
    return {'fonte':'rascunho' if r else 'publicado','perfil_versao':'rascunho' if r else versao,'perfil_atendimento':efetivo['perfil']}


@router.get('/segmentos')
async def segmentos(request:Request):
    """Modelos de atendimento da plataforma, com os padrões de perfil e de comportamento de cada um (versão mais recente)."""
    rows=await request.app.state.db.fetch("""SELECT DISTINCT ON (id) * FROM modelos_agente WHERE tenant_id IS NULL AND tipo='atendimento' ORDER BY id,versao DESC""")
    return {'itens':[_segmento(registro(r)) for r in rows]}


def _segmento(m):
    perfil,_=A.resolver_perfil(('segmento',m.get('perfil_padrao')))
    return {'id':m['id'],'nome':m['nome'],'versao':m['versao'],'modulos':m['modulos'],'perfil_padrao':A.bloco_do_pacote(perfil),
            'comportamento_padrao':{**{c:PADROES.get(c) for c in COMPORTAMENTOS},**{c:v for c,v in (m['comportamento_padrao'] or {}).items() if c in COMPORTAMENTOS}}}


@router.get('/segmentos/{segmento_id}')
async def segmento(request:Request,segmento_id:str):
    m=await request.app.state.db.fetchrow("SELECT * FROM modelos_agente WHERE id=$1 AND tenant_id IS NULL AND tipo='atendimento' ORDER BY versao DESC LIMIT 1",segmento_id)
    if not m: raise HTTPException(404,'NOT_FOUND')
    return _segmento(registro(m))
