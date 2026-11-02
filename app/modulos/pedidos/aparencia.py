"""Aparência do cardápio público: nome, frase, logo, banner, cores e estilo de cada empresa.

Imagem é conteúdo de terceiro numa página pública. O formato sai dos bytes, nunca do nome ou do tipo informado, e SVG não
entra: ele pode carregar script. A entrega é sempre com o tipo gravado, sem sniffing e com CSP que não deixa nada rodar.
Quem otimiza é o navegador de quem envia: decodificar imagem aqui colocaria esse risco no processo do gateway."""
import base64,secrets
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field
from admin.servico import auditar
from modulos import habilitado
from nucleo.autenticacao import painel
from nucleo.banco import Repositorio

router=APIRouter()
B='/admin/v1/tenants/{tenant}/cardapio/aparencia'
MAX_BYTES=2_000_000
# Imagens soltas (enviadas e nunca salvas) por empresa, para o envio repetido não encher o banco.
MAX_SOLTAS=20
COR=r'^#[0-9a-fA-F]{6}$'
ID_IMAGEM=r'^[0-9a-f]{32}$'


def formato(bruto):
    """Tipo real do arquivo pela assinatura; None para qualquer outra coisa (SVG, HTML, GIF)."""
    if bruto.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if bruto.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if bruto[:4]==b'RIFF' and bruto[8:12]==b'WEBP': return 'image/webp'
    return None


class Envio(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tipo:Literal['logo','banner']
    conteudo:str=Field(min_length=8,max_length=2_800_000)


class Aparencia(BaseModel):
    model_config=ConfigDict(extra='forbid')
    nome_exibicao:str|None=Field(None,max_length=60)
    slogan:str|None=Field(None,max_length=120)
    cor_primaria:str=Field(pattern=COR)
    cor_destaque:str=Field(pattern=COR)
    cor_fundo:str=Field(pattern=COR)
    cantos:Literal['retos','suave','arredondado']='suave'
    titulos:Literal['classica','moderna']='classica'
    logo_id:str|None=Field(None,pattern=ID_IMAGEM)
    banner_id:str|None=Field(None,pattern=ID_IMAGEM)
    banner_foco:int=Field(50,ge=0,le=100)


def _url(imagem): return f'/api/public/cardapio-imagem/{imagem}' if imagem else None


def retrato(r):
    return {'nome_exibicao':r['nome_exibicao'],'slogan':r['slogan'],'cor_primaria':r['cor_primaria'],'cor_destaque':r['cor_destaque'],
            'cor_fundo':r['cor_fundo'],'cantos':r['cantos'],'titulos':r['titulos'],'logo_id':r['logo_id'],'banner_id':r['banner_id'],
            'logo_url':_url(r['logo_id']),'banner_url':_url(r['banner_id']),'banner_foco':r['banner_foco'],
            'atualizado_em':r['atualizado_em'].isoformat()}


async def da_empresa(db,tenant):
    """Aparência para a página pública; None usa o visual padrão."""
    r=await db.fetchrow('SELECT * FROM cardapio_aparencia WHERE tenant_id=$1',tenant)
    if not r: return None
    corpo=retrato(r)
    for campo in ('logo_id','banner_id','atualizado_em'): corpo.pop(campo)
    return corpo


async def _exigir(request,tenant):
    await habilitado(Repositorio(request.app.state.db),tenant,'catalogo')


async def _limpar_soltas(conn,tenant,manter=()):
    """Tira imagens que nenhuma aparência usa, menos as recém-enviadas de quem ainda está editando (última hora)."""
    await conn.execute("""DELETE FROM cardapio_imagens i WHERE i.tenant_id=$1 AND i.id<>ALL($2::text[]) AND i.criado_em<now()-interval '1 hour'
        AND NOT EXISTS(SELECT 1 FROM cardapio_aparencia a WHERE a.logo_id=i.id OR a.banner_id=i.id)""",tenant,list(manter))


@router.get(B,dependencies=[Depends(painel)])
async def ler(request:Request,tenant:str):
    await _exigir(request,tenant)
    r=await request.app.state.db.fetchrow('SELECT * FROM cardapio_aparencia WHERE tenant_id=$1',tenant)
    return {'aparencia':retrato(r) if r else None}


@router.post(B+'/imagem',status_code=201,dependencies=[Depends(painel)])
async def enviar_imagem(request:Request,tenant:str,dados:Envio,actor=Depends(painel)):
    await _exigir(request,tenant)
    try: bruto=base64.b64decode(dados.conteudo,validate=True)
    except ValueError: raise HTTPException(400,{'campo':'conteudo'}) from None
    if len(bruto)>MAX_BYTES: raise HTTPException(413,'arquivo grande demais')
    mime=formato(bruto)
    if mime is None: raise HTTPException(400,{'campo':'conteudo','motivo':'formato'})
    db=request.app.state.db;imagem=secrets.token_hex(16)
    async with db.acquire() as conn,conn.transaction():
        await _limpar_soltas(conn,tenant)
        soltas=await conn.fetchval("""SELECT count(*) FROM cardapio_imagens i WHERE i.tenant_id=$1
            AND NOT EXISTS(SELECT 1 FROM cardapio_aparencia a WHERE a.logo_id=i.id OR a.banner_id=i.id)""",tenant)
        if soltas>=MAX_SOLTAS: raise HTTPException(429,'imagens demais')
        await conn.execute('INSERT INTO cardapio_imagens(id,tenant_id,tipo,mime,conteudo,criado_por) VALUES ($1,$2,$3,$4,$5,$6)',imagem,tenant,dados.tipo,mime,bruto,actor)
    return {'id':imagem,'url':_url(imagem),'tipo':dados.tipo}


async def _conferir_imagem(conn,tenant,imagem,tipo,campo):
    """A imagem precisa ser desta empresa e do tipo certo: o id vem do cliente."""
    if imagem and not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM cardapio_imagens WHERE id=$1 AND tenant_id=$2 AND tipo=$3)',imagem,tenant,tipo):
        raise HTTPException(400,{'campo':campo})


@router.put(B,dependencies=[Depends(painel)])
async def salvar(request:Request,tenant:str,dados:Aparencia,actor=Depends(painel)):
    await _exigir(request,tenant)
    nome=(dados.nome_exibicao or '').strip() or None;slogan=(dados.slogan or '').strip() or None
    async with request.app.state.db.acquire() as conn,conn.transaction():
        await _conferir_imagem(conn,tenant,dados.logo_id,'logo','logo_id')
        await _conferir_imagem(conn,tenant,dados.banner_id,'banner','banner_id')
        r=await conn.fetchrow('''INSERT INTO cardapio_aparencia(tenant_id,nome_exibicao,slogan,cor_primaria,cor_destaque,cor_fundo,cantos,titulos,logo_id,banner_id,banner_foco,atualizado_por)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (tenant_id) DO UPDATE SET nome_exibicao=$2,slogan=$3,cor_primaria=$4,cor_destaque=$5,cor_fundo=$6,cantos=$7,titulos=$8,
                logo_id=$9,banner_id=$10,banner_foco=$11,atualizado_por=$12,atualizado_em=now() RETURNING *''',
            tenant,nome,slogan,dados.cor_primaria.lower(),dados.cor_destaque.lower(),dados.cor_fundo.lower(),dados.cantos,dados.titulos,
            dados.logo_id,dados.banner_id,dados.banner_foco,actor)
        # A imagem trocada sai aqui. A enviada há menos de uma hora fica: pode ser de quem ainda está editando em outra aba.
        await _limpar_soltas(conn,tenant,[x for x in (dados.logo_id,dados.banner_id) if x])
        await auditar(conn,tenant,actor,'cardapio.aparencia_salva',tenant)
    return {'aparencia':retrato(r)}


@router.delete(B,dependencies=[Depends(painel)])
async def restaurar(request:Request,tenant:str,actor=Depends(painel)):
    """Volta ao visual padrão e apaga as imagens da empresa."""
    await _exigir(request,tenant)
    async with request.app.state.db.acquire() as conn,conn.transaction():
        await conn.execute('DELETE FROM cardapio_aparencia WHERE tenant_id=$1',tenant)
        await conn.execute('DELETE FROM cardapio_imagens WHERE tenant_id=$1',tenant)
        await auditar(conn,tenant,actor,'cardapio.aparencia_restaurada',tenant)
    return {'aparencia':None}


@router.get('/api/v1/publico/cardapio/imagem/{imagem}')
async def imagem_publica(request:Request,imagem:str):
    """Sem token: o id tem 128 bits e a imagem é pública por natureza. O cache longo é definido na rota do painel, que é a que o
    navegador enxerga; aqui vale o no-store geral do gateway."""
    if len(imagem)!=32 or any(c not in '0123456789abcdef' for c in imagem): raise HTTPException(404,'NOT_FOUND')
    r=await request.app.state.db.fetchrow('SELECT mime,conteudo FROM cardapio_imagens WHERE id=$1',imagem)
    if not r: raise HTTPException(404,'NOT_FOUND')
    return Response(bytes(r['conteudo']),media_type=r['mime'],headers={'X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"default-src 'none'; sandbox",'Content-Disposition':'inline'})
