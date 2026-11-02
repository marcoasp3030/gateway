"""Aparência do cardápio: quem altera, o que entra como imagem e como ela sai na página pública."""
import base64
import pytest
from modulos.pedidos import aparencia as AP
from modulos.pedidos import cardapio as CARD
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
T='bykoji'
B=f'/admin/v1/tenants/{T}/cardapio/aparencia'
GESTOR={'X-Role':'gestor','X-Actor':'gestor@exemplo.test'}
SUP={'X-Role':'supervisor','X-Actor':'sup@exemplo.test'}
FUNC={'X-Role':'funcionario','X-Actor':'func@exemplo.test'}
PNG=b'\x89PNG\r\n\x1a\n'+b'\x00'*64
JPG=b'\xff\xd8\xff\xe0'+b'\x00'*64
WEBP=b'RIFF\x24\x00\x00\x00WEBPVP8 '+b'\x00'*64
BASE={'cor_primaria':'#AA1122','cor_destaque':'#ffcc00','cor_fundo':'#fafafa'}


def b64(b): return base64.b64encode(b).decode()


def em_banco(cenario):
    async def tudo(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            await cenario(conn,client,conversa)
    banco(tudo)


async def enviar(client,tipo='logo',bruto=PNG):
    r=await client.post(B+'/imagem',headers=GESTOR,json={'tipo':tipo,'conteudo':b64(bruto)})
    assert r.status_code==201,r.text
    return r.json()['id']


def test_formato_vem_dos_bytes():
    assert (AP.formato(PNG),AP.formato(JPG),AP.formato(WEBP))==('image/png','image/jpeg','image/webp')
    for ruim in (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',b'<html><script>alert(1)</script>',b'GIF89a',b'RIFF\x00\x00\x00\x00WAVE',b''):
        assert AP.formato(ruim) is None


def test_so_o_gestor_altera():
    async def cenario(conn,client,conversa):
        for h in (SUP,FUNC):
            assert (await client.get(B,headers=h)).status_code==403
            assert (await client.put(B,headers=h,json=BASE)).status_code==403
            assert (await client.delete(B,headers=h)).status_code==403
            assert (await client.post(B+'/imagem',headers=h,json={'tipo':'logo','conteudo':b64(PNG)})).status_code==403
        assert (await client.get(B,headers=GESTOR)).json()=={'aparencia':None}
        r=await client.put(B,headers=GESTOR,json={**BASE,'nome_exibicao':'  Casa  ','slogan':'  '});assert r.status_code==200,r.text
        a=r.json()['aparencia'];assert (a['nome_exibicao'],a['slogan'],a['cor_primaria'],a['cantos'])==('Casa',None,'#aa1122','suave')
    em_banco(cenario)


def test_imagem_recusa_arquivo_que_nao_e_imagem():
    async def cenario(conn,client,conversa):
        for corpo in (b'<svg onload="alert(1)"/>',b'<html></html>'):
            r=await client.post(B+'/imagem',headers=GESTOR,json={'tipo':'logo','conteudo':b64(corpo)});assert r.status_code==400,r.text
        assert (await client.post(B+'/imagem',headers=GESTOR,json={'tipo':'logo','conteudo':'isto nao e base64!'})).status_code==400
        assert (await client.post(B+'/imagem',headers=GESTOR,json={'tipo':'icone','conteudo':b64(PNG)})).status_code in (400,422)
        r=await client.post(B+'/imagem',headers=GESTOR,json={'tipo':'banner','conteudo':b64(PNG+b'\x00'*AP.MAX_BYTES)});assert r.status_code==413
        assert await conn.fetchval('SELECT count(*) FROM cardapio_imagens')==0
    em_banco(cenario)


def test_cor_e_campos_fora_do_combinado_nao_entram():
    async def cenario(conn,client,conversa):
        for ruim in ({**BASE,'cor_primaria':'red'},{**BASE,'cor_fundo':'#fff;background:url(x)'},{**BASE,'cantos':'x'},{**BASE,'banner_foco':101},{**BASE,'css':'x'}):
            assert (await client.put(B,headers=GESTOR,json=ruim)).status_code in (400,422),ruim
        # Imagem apagada: o painel precisa saber qual, para pedir o envio de novo.
        r=await client.put(B,headers=GESTOR,json={**BASE,'logo_id':'a'*32});assert r.status_code==400 and r.json()['erro']['detalhes']['campo']=='logo_id'
        r=await client.put(B,headers=GESTOR,json={**BASE,'banner_id':'a'*32});assert r.status_code==400 and r.json()['erro']['detalhes']['campo']=='banner_id'
        # Imagem de outro tipo ou de outra empresa não serve.
        banner=await enviar(client,'banner')
        assert (await client.put(B,headers=GESTOR,json={**BASE,'logo_id':banner})).status_code==400
        await conn.execute("INSERT INTO tenants(id,nome) VALUES ('outra','Outra') ON CONFLICT DO NOTHING")
        alheia='b'*32
        await conn.execute("INSERT INTO cardapio_imagens(id,tenant_id,tipo,mime,conteudo,criado_por) VALUES ($1,'outra','logo','image/png',$2,'x')",alheia,PNG)
        assert (await client.put(B,headers=GESTOR,json={**BASE,'logo_id':alheia})).status_code==400
    em_banco(cenario)


def test_pagina_publica_recebe_a_aparencia_e_a_imagem_sai_segura(monkeypatch):
    monkeypatch.setenv('PAINEL_PUBLIC_URL','https://painel.exemplo.test');monkeypatch.setenv('API_KEY_PEPPER','p')
    async def cenario(conn,client,conversa):
        c=await conversa('cliente1');url,_=await CARD.garantir_link(conn,T,c);tok=url.split('#')[1]
        assert (await client.post('/api/v1/publico/cardapio/ler',json={'token':tok})).json()['aparencia'] is None
        logo=await enviar(client,'logo',WEBP);banner=await enviar(client,'banner',JPG)
        assert (await client.put(B,headers=GESTOR,json={**BASE,'nome_exibicao':'Casa','logo_id':logo,'banner_id':banner,'banner_foco':30,'titulos':'moderna','cantos':'retos'})).status_code==200
        a=(await client.post('/api/v1/publico/cardapio/ler',json={'token':tok})).json()['aparencia']
        assert a['logo_url']==f'/api/public/cardapio-imagem/{logo}' and a['banner_foco']==30 and 'logo_id' not in a and 'atualizado_por' not in a
        r=await client.get(f'/api/v1/publico/cardapio/imagem/{banner}')
        assert r.status_code==200 and r.content==JPG and r.headers['content-type']=='image/jpeg'
        assert r.headers['x-content-type-options']=='nosniff' and 'sandbox' in r.headers['content-security-policy']
        assert (await client.get('/api/v1/publico/cardapio/imagem/'+'0'*32)).status_code==404
        assert (await client.get('/api/v1/publico/cardapio/imagem/..%2f..%2fetc')).status_code==404
    em_banco(cenario)


def test_trocar_e_restaurar_limpam_as_imagens():
    async def cenario(conn,client,conversa):
        antiga=await enviar(client);await client.put(B,headers=GESTOR,json={**BASE,'logo_id':antiga})
        await conn.execute("UPDATE cardapio_imagens SET criado_em=now()-interval '2 hours'")
        nova=await enviar(client);assert (await client.put(B,headers=GESTOR,json={**BASE,'logo_id':nova})).status_code==200
        assert [r['id'] for r in await conn.fetch('SELECT id FROM cardapio_imagens')]==[nova]
        assert (await client.get(f'/api/v1/publico/cardapio/imagem/{antiga}')).status_code==404
        # Imagem enviada e nunca salva tem teto por empresa.
        await conn.execute("""INSERT INTO cardapio_imagens(id,tenant_id,tipo,mime,conteudo,criado_por)
            SELECT md5(g::text),$1,'logo','image/png',$2,'x' FROM generate_series(1,$3::int) g""",T,PNG,AP.MAX_SOLTAS)
        r=await client.post(B+'/imagem',headers=GESTOR,json={'tipo':'logo','conteudo':b64(PNG)});assert r.status_code==429
        assert (await client.delete(B,headers=GESTOR)).json()=={'aparencia':None}
        assert await conn.fetchval('SELECT count(*) FROM cardapio_imagens')==0
        assert await conn.fetchval('SELECT count(*) FROM cardapio_aparencia')==0
    em_banco(cenario)
