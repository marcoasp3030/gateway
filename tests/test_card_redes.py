"""CARD-REDES (docs/ESPEC_CARD_REDES.md): Instagram, Facebook e telefone opcionais da empresa no cardápio público.
Só gestor e acima; o gateway grava usuário/página/número e monta o link; a aparência não mexe nas redes."""
import pytest
from modulos.pedidos import cardapio as CARD
from modulos.pedidos import redes as R
from test_cardapio_aparencia import em_banco,GESTOR,SUP,FUNC,BASE,T

pytestmark=pytest.mark.skipif(not __import__('test_migrations').DSN,reason='Postgres de teste obrigatório')
B=f'/admin/v1/tenants/{T}/cardapio/redes'
MSG='Use DDD + número, ex.: 11912345678'


def test_normalizacao():
    for entrada in ('@casa.sushi','casa.sushi','https://instagram.com/casa.sushi','https://www.instagram.com/casa.sushi/?hl=pt-br','instagram.com/casa.sushi'):
        assert R.normalizar('instagram',entrada)==('casa.sushi',None),entrada
    for entrada in ('casa.sushi.sp','https://facebook.com/casa.sushi.sp','https://m.facebook.com/casa.sushi.sp/','fb.com/casa.sushi.sp','www.facebook.com/casa.sushi.sp'):
        assert R.normalizar('facebook',entrada)==('casa.sushi.sp',None),entrada
    for ruim in ('https://instagram.com.golpe.test/casa','https://golpe.test/instagram.com/casa','javascript:alert(1)','https://instagram.com/casa/reels',
                 '@casa sushi','@'+'a'*31,'https://instagram.com:8443/casa','ftp://instagram.com/casa','','   ',None,12):
        assert R.normalizar('instagram',ruim)[0] is None,ruim
    for ruim in ('https://facebook.com.golpe.test/casa','https://golpe.test/casa','https://facebook.com/../x','casa//x','casa sushi','http://fb.me/casa'):
        assert R.normalizar('facebook',ruim)[0] is None,ruim
    assert R.normalizar('telefone','(11) 3333-4444')==('551133334444',None) and R.normalizar('telefone','11 7333-4444')==(None,'telefone')
    assert R.publico('telefone','5511912345678')=={'tipo':'telefone','url':'tel:+5511912345678','rotulo':'(11) 91234-5678'}
    assert R.publico('telefone','551133334444')['rotulo']=='(11) 3333-4444'
    assert R.publico('instagram','casa.sushi')=={'tipo':'instagram','url':'https://instagram.com/casa.sushi','rotulo':'@casa.sushi'}


def test_so_gestor_salvar_ordem_recusas_e_auditoria():
    async def cenario(conn,client,conversa):
        for h in (SUP,FUNC):
            assert (await client.get(B,headers=h)).status_code==403
            r=await client.put(B,headers=h,json={'itens':[]});assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(403,'papel_insuficiente')
        assert (await client.get(B,headers=GESTOR)).json()=={'itens':[]}
        itens=[{'tipo':'telefone','valor':'(11) 3333-4444'},{'tipo':'instagram','valor':'@casa.sushi'},{'tipo':'facebook','valor':'https://facebook.com/casa.sushi.sp','ativo':False}]
        r=await client.put(B,headers=GESTOR,json={'itens':itens});assert r.status_code==200,r.text
        assert r.json()['itens']==[{'tipo':'telefone','valor':'551133334444','ativo':True,'url':'tel:+551133334444'},
                                   {'tipo':'instagram','valor':'casa.sushi','ativo':True,'url':'https://instagram.com/casa.sushi'},
                                   {'tipo':'facebook','valor':'casa.sushi.sp','ativo':False,'url':'https://facebook.com/casa.sushi.sp'}]
        assert (await client.get(B,headers=GESTOR)).json()==r.json()
        auditoria=lambda: conn.fetch("SELECT actor,dados FROM audit_log WHERE tenant_id=$1 AND acao='cardapio.redes_salvas' ORDER BY id",T)
        a=await auditoria();assert len(a)==1 and a[0]['actor']=='gestor@exemplo.test' and '3333' not in a[0]['dados'] and 'casa' not in a[0]['dados']
        # Mesmo conteúdo: nada muda, nada é auditado.
        assert (await client.put(B,headers=GESTOR,json={'itens':itens})).status_code==200 and len(await auditoria())==1
        # Recusas: nada é gravado; a resposta diz a linha e o motivo.
        for corpo,linha,campo,motivo in (
                ({'itens':[{'tipo':'instagram','valor':'https://golpe.test/casa'}]},1,'valor','endereco_invalido'),
                ({'itens':[{'tipo':'instagram','valor':'a'},{'tipo':'instagram','valor':'b'}]},2,'tipo','tipo_repetido'),
                ({'itens':[{'tipo':'tiktok','valor':'casa'}]},1,'tipo','tipo_invalido'),
                ({'itens':[{'tipo':'instagram','valor':'casa','url':'https://golpe.test'}]},1,'tipo','tipo_invalido'),
                ({'itens':[{'tipo':'facebook','valor':'casa','ativo':'sim'}]},1,'ativo','tipo'),
                ({'itens':[{'tipo':'instagram','valor':'  '}]},1,'valor','vazio'),
                ({'itens':{}},None,'itens','tipo'),({'itens':[],'x':1},None,'itens','tipo')):
            r=await client.put(B,headers=GESTOR,json=corpo);assert r.status_code==400,corpo
            assert r.json()['erro']['detalhes']=={'campo':campo,'motivo':motivo,'linha':linha},(corpo,r.json())
        r=await client.put(B,headers=GESTOR,json={'itens':[{'tipo':'telefone','valor':'11-9999'}]})
        assert (r.status_code,r.json()['erro']['mensagem'],r.json()['erro']['detalhes']['motivo'])==(400,MSG,'telefone')
        assert len((await client.get(B,headers=GESTOR)).json()['itens'])==3
        # Salvar e restaurar a aparência não mexe nas redes.
        assert (await client.put(f'/admin/v1/tenants/{T}/cardapio/aparencia',headers=GESTOR,json=BASE)).status_code==200
        assert (await client.delete(f'/admin/v1/tenants/{T}/cardapio/aparencia',headers=GESTOR)).status_code in (200,204)
        assert len((await client.get(B,headers=GESTOR)).json()['itens'])==3
        # [] apaga tudo.
        assert (await client.put(B,headers=GESTOR,json={'itens':[]})).json()=={'itens':[]} and len(await auditoria())==2
    em_banco(cenario)


def test_pagina_publica_mostra_so_as_ativas(monkeypatch):
    monkeypatch.setenv('PAINEL_PUBLIC_URL','https://painel.exemplo.test');monkeypatch.setenv('API_KEY_PEPPER','p')
    async def cenario(conn,client,conversa):
        c=await conversa('cliente-redes');url,_=await CARD.garantir_link(conn,T,c);tok=url.split('#')[1]
        ler=lambda: client.post('/api/v1/publico/cardapio/ler',json={'token':tok})
        assert (await ler()).json()['redes']==[]
        await client.put(B,headers=GESTOR,json={'itens':[{'tipo':'instagram','valor':'@casa.sushi'},{'tipo':'facebook','valor':'casa.sushi.sp','ativo':False},
                                                          {'tipo':'telefone','valor':'11 91234-5678'}]})
        esperado=[{'tipo':'instagram','url':'https://instagram.com/casa.sushi','rotulo':'@casa.sushi'},
                  {'tipo':'telefone','url':'tel:+5511912345678','rotulo':'(11) 91234-5678'}]
        assert (await ler()).json()['redes']==esperado
        # Link já usado: a tela "Este pedido já foi enviado" também recebe as redes.
        await conn.execute("UPDATE cardapio_links SET usado_em=now(),pedido_numero='12345' WHERE tenant_id=$1 AND conversation_id=$2",T,c)
        corpo=(await ler()).json();assert corpo['usado']=={'numero':'12345'} and corpo['redes']==esperado
    em_banco(cenario)
