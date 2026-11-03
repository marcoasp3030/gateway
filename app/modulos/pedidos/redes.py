"""CARD-REDES (docs/ESPEC_CARD_REDES.md): Instagram, Facebook e telefone da empresa no cardápio público. Opcional, por empresa.

A empresa grava só o usuário, a página ou o número; o link é montado aqui, para ninguém pôr no cardápio um endereço de outro site.
Rota própria, fora da aparência: o PUT da aparência substitui tudo e um painel antigo apagaria as redes ao salvar as cores."""
import re
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends,Request
from fastapi.responses import JSONResponse
from admin.servico import auditar
from nucleo.autenticacao import painel
from nucleo.telefone import MENSAGEM as MENSAGEM_TELEFONE,telefone_br
from .aparencia import _exigir

router=APIRouter()
B='/admin/v1/tenants/{tenant}/cardapio/redes'
TIPOS=('instagram','facebook','telefone')
USUARIO_INSTAGRAM=re.compile(r'[A-Za-z0-9._]{1,30}')
PAGINA_FACEBOOK=re.compile(r'[A-Za-z0-9.\-/]{1,100}')
DOMINIOS={'instagram':{'instagram.com','www.instagram.com'},
          'facebook':{'facebook.com','www.facebook.com','m.facebook.com','fb.com','www.fb.com'}}


def _erro(linha,campo,motivo,mensagem=None):
    return JSONResponse({'erro':{'codigo':'VALIDATION_ERROR','mensagem':mensagem or 'Confira os campos informados.',
                                 'detalhes':{'campo':campo,'motivo':motivo,'linha':linha}}},
                        status_code=400,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def _caminho(tipo,valor):
    """Usuário ou página digitada solta, com @ ou como link do domínio da rede. None: link de outro site ou formato inválido."""
    v=valor.strip()
    if '://' in v or v.lower().startswith(('www.','instagram.','facebook.','m.facebook.','fb.')):
        partes=urlsplit(v if '://' in v else 'https://'+v)
        if partes.scheme not in ('http','https') or (partes.hostname or '').lower() not in DOMINIOS[tipo] or partes.port: return None
        v=partes.path
    v=v.strip('/')
    if tipo=='instagram':
        v=v.removeprefix('@')
        return v if USUARIO_INSTAGRAM.fullmatch(v) else None
    return v if PAGINA_FACEBOOK.fullmatch(v) and '..' not in v and '//' not in v else None


def normalizar(tipo,valor):
    """(valor gravado, None) ou (None, motivo)."""
    if not isinstance(valor,str) or not valor.strip(): return None,'vazio'
    if tipo=='telefone':
        n=telefone_br(valor)
        return (n,None) if n else (None,'telefone')
    n=_caminho(tipo,valor)
    return (n,None) if n else (None,'endereco_invalido')


def _telefone_legivel(n):
    d=n[2:]
    return f'({d[:2]}) {d[2:7]}-{d[7:]}' if len(d)==11 else f'({d[:2]}) {d[2:6]}-{d[6:]}'


def publico(tipo,valor):
    """Link e rótulo que a página mostra, montados só a partir do valor gravado."""
    if tipo=='instagram': return {'tipo':tipo,'url':f'https://instagram.com/{valor}','rotulo':'@'+valor}
    if tipo=='facebook': return {'tipo':tipo,'url':f'https://facebook.com/{valor}','rotulo':valor}
    return {'tipo':tipo,'url':f'tel:+{valor}','rotulo':_telefone_legivel(valor)}


async def _lista(db,tenant):
    return [dict(r) for r in await db.fetch('SELECT tipo,valor,ativo FROM cardapio_redes WHERE tenant_id=$1 ORDER BY ordem',tenant)]


def _visao(itens): return {'itens':[{**i,'url':publico(i['tipo'],i['valor'])['url']} for i in itens]}


async def da_empresa(db,tenant):
    """Redes ativas para a página pública, na ordem da empresa. [] sem nenhuma."""
    return [publico(i['tipo'],i['valor']) for i in await _lista(db,tenant) if i['ativo']]


@router.get(B,dependencies=[Depends(painel)])
async def ler(request:Request,tenant:str):
    await _exigir(request,tenant)
    return _visao(await _lista(request.app.state.db,tenant))


@router.put(B,dependencies=[Depends(painel)])
async def salvar(request:Request,tenant:str,dados:dict,actor=Depends(painel)):
    """{"itens": [...]}: substitui a lista; a ordem do corpo é a ordem mostrada ao cliente; [] apaga tudo."""
    await _exigir(request,tenant)
    if set(dados)!={'itens'} or not isinstance(dados['itens'],list) or len(dados['itens'])>len(TIPOS): return _erro(None,'itens','tipo')
    itens=[];vistos=set()
    for n,item in enumerate(dados['itens'],1):
        if not isinstance(item,dict) or not set(item)<={'tipo','valor','ativo'} or item.get('tipo') not in TIPOS: return _erro(n,'tipo','tipo_invalido')
        if item['tipo'] in vistos: return _erro(n,'tipo','tipo_repetido')
        if not isinstance(item.get('ativo',True),bool): return _erro(n,'ativo','tipo')
        valor,motivo=normalizar(item['tipo'],item.get('valor'))
        if motivo: return _erro(n,'valor',motivo,MENSAGEM_TELEFONE if motivo=='telefone' else None)
        vistos.add(item['tipo']);itens.append({'tipo':item['tipo'],'valor':valor,'ativo':item.get('ativo',True)})
    async with request.app.state.db.acquire() as conn,conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','cardapio_redes:'+tenant)
        antes=await _lista(conn,tenant)
        if antes!=itens:
            await conn.execute('DELETE FROM cardapio_redes WHERE tenant_id=$1',tenant)
            for ordem,i in enumerate(itens):
                await conn.execute('INSERT INTO cardapio_redes(tenant_id,tipo,valor,ativo,ordem,atualizado_por) VALUES ($1,$2,$3,$4,$5,$6)',
                                   tenant,i['tipo'],i['valor'],i['ativo'],ordem,actor)
            # Só os tipos e se estão ligados: a auditoria não guarda o conteúdo.
            await auditar(conn,tenant,actor,'cardapio.redes_salvas',tenant,
                          {'antes':[[i['tipo'],i['ativo']] for i in antes],'depois':[[i['tipo'],i['ativo']] for i in itens]})
    return _visao(itens)
