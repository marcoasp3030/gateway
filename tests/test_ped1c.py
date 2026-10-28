"""PED1-c: nome de exibição (cadastro > informado > perfil > telefone), E.164, canal, estado único, última mensagem,
pedido aberto, PATCH do nome de cadastro, nome_cliente do agente e filtro de conversas encerradas."""
import json
import httpx,pytest
from agentes import skyone
from nucleo import contatos as nc
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_fase4_avisos import montar,CLIENTE
from test_skyone_adaptador import preparar_agente

C='/admin/v1/tenants/bykoji/conversas'
K='/admin/v1/tenants/bykoji/contatos'
# PAPEL1-a (decisão 5): contatos são do supervisor para cima; o membro (funcionário) recebe 403.
MEMBRO={'X-Actor':'joana@loja.com','X-Role':'supervisor'}


def test_validacao_do_nome_e_telefone():
    v=nc.validar_nome
    assert v('  Marco   Antonio ')==('Marco Antonio',None) and v("D'Ávila-Souza")==("D'Ávila-Souza",None) and v('Zé')==('Zé',None)
    assert v('M')==(None,'tamanho') and v('x'*61)==(None,'tamanho') and v('   ')==(None,'tamanho') and v(None)==(None,'tamanho') and v(123)==(None,'tamanho')
    for fone in ('11947281513','+55 11 94728-1513','Marco 11 94728 1513','(11) 9472-8151'): assert v(fone)==(None,'telefone'),fone
    for ruim in ('Marco 😀','Ana ❤️','Ana\u200dBia','Ana\x00'): assert v(ruim)==(None,'caractere'),ruim
    assert v('..')==(None,'letras') and v('A1')==(None,'letras') and v('Loja 2')==('Loja 2',None)
    assert nc.e164('5511947281513','uazapi')=='+5511947281513' and nc.e164('5511947281513@s.whatsapp.net','uazapi')=='+5511947281513'
    assert nc.e164('5511947281513','api')=='+5511947281513' and nc.e164('marco','api') is None and nc.e164('123','uazapi') is None and nc.e164('ana123456789','api') is None
    assert nc.formatado('+5511947281513')=='+55 11 94728-1513' and nc.formatado('+551133334444')=='+55 11 3333-4444' and nc.formatado('+14155550100')=='+14155550100' and nc.formatado(None) is None
    assert nc.nome_de_perfil('  Marco  A. ')=='Marco A.' and nc.nome_de_perfil('') is None and nc.nome_de_perfil(None) is None and len(nc.nome_de_perfil('a'*300))==100


def test_prioridade_do_nome_em_todas_as_combinacoes():
    nomes={'cadastro':'Cadastro','informado':'Informado','perfil':'Perfil'}
    for mascara in range(8):
        ativos=[o for i,o in enumerate(nc.PRIORIDADE) if mascara>>i&1]
        linha={'nome_'+o:nomes[o] for o in ativos}
        e=nc.exibicao(linha,'5511947281513','uazapi')
        esperado=next((o for o in nc.PRIORIDADE if o in ativos),None)
        if esperado: assert (e['nome_exibicao'],e['nome_origem'])==(nomes[esperado],esperado),ativos
        else: assert (e['nome_exibicao'],e['nome_origem'])==('+55 11 94728-1513','telefone')
        assert e['nomes']=={o:(nomes[o] if o in ativos else None) for o in nc.PRIORIDADE} and e['telefone_e164']=='+5511947281513'
        # Para o agente, o pedido e o aviso: só nome de verdade, nunca o telefone.
        assert nc.nome_do_cliente(linha)==((nomes[esperado],esperado) if esperado else (None,None))
    # Perfil que não é nome ("." ou emoji) não é exibido; a coluna antiga `nome` vale como perfil.
    assert nc.exibicao({'nome_perfil':'.'},'5511947281513','uazapi')['nome_origem']=='telefone'
    assert nc.exibicao({'nome':'Antigo'},'5511947281513','uazapi')['nome_origem']=='perfil'
    assert nc.exibicao({},'marco','api')==({'nome_exibicao':'marco','nome_origem':'identificador','nomes':{'cadastro':None,'informado':None,'perfil':None},'telefone_e164':None,'telefone_formatado':None})
    anon=nc.exibicao({'nome_cadastro':'X','anonimizado_em':'2026-10-11'},'5511947281513','uazapi')
    assert anon=={'nome_exibicao':'Contato anonimizado','nome_origem':'anonimizado','nomes':{'cadastro':None,'informado':None,'perfil':None},'telefone_e164':None,'telefone_formatado':None}
    assert nc.nome_do_cliente({'nome_cadastro':'X','anonimizado_em':'2026-10-11'})==(None,None)
    assert nc.canal_info('can_1','uazapi','Loja',False,False)=={'id':'can_1','tipo':'uazapi','nome':'Loja','icone':'whatsapp','teste':False}
    assert nc.canal_info('can_2','api','Site',False,True)['teste'] is True and nc.canal_info('can_3','site','S',True,False)=={'id':'can_3','tipo':'site','nome':'S','icone':'globe','teste':True}
    assert nc.canal_info(None,None,None,False,False) is None


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_nome_cliente_no_retorno_do_agente(monkeypatch):
    async def livre(url,dominios): return None
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',livre)
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        atual=await conn.fetchval("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('bykoji','bykoji:canal:x','entrada','cliente','oi') RETURNING id")
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',atual,'oi',cfg)
        assert body['cliente']['nome'] is None and body['cliente']['nome_origem'] is None
        async def retorno(**extra):
            def responder(req):
                p=json.loads(req.content)
                return httpx.Response(200,json={'resposta':['Prazer!'],'acao':'nenhuma','status':'success','tipo':'text',
                                                'request_id':p['request_id'],'conversation_id':p['conversation_id'],**extra})
            async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
                return await skyone.chamar(client,repo,cofre,'bykoji',cfg,body)
        assert (await retorno(nome_cliente='  Marco   Antonio '))['nome_cliente']=='Marco Antonio'
        # Inválido ou ausente: ignorado, o turno segue normal (nunca derruba a resposta).
        for ruim in ('M','x'*61,'11947281513','Marco 😀','..',123,['Marco'],{'nome':'Marco'},''):
            r=await retorno(nome_cliente=ruim);assert r['resposta']==['Prazer!'] and 'nome_cliente' not in r,ruim
        assert 'nome_cliente' not in await retorno() and 'nome_cliente' not in await retorno(nome_cliente=None)
        # Gravado como "informado": não toca no cadastro, e o pacote seguinte já manda o nome com a origem.
        ct='ct_teste1'
        await conn.execute("INSERT INTO contatos(id,tenant_id,telefone,nome_perfil) VALUES ($1,'bykoji','5511900000077','Perfil Zap')",ct)
        await conn.execute("UPDATE conversas SET contato_id=$1 WHERE conversation_id='bykoji:canal:x'",ct)
        assert (await skyone.pacote(repo,'bykoji','bykoji:canal:x',atual,'oi',cfg))['cliente']['nome']=='Perfil Zap'
        assert await nc.gravar_informado(conn,'bykoji','bykoji:canal:x','Marco Antonio') is True
        assert await nc.gravar_informado(conn,'bykoji','bykoji:canal:x','Marco Antonio') is False   # sem mudança
        c=(await skyone.pacote(repo,'bykoji','bykoji:canal:x',atual,'oi',cfg))['cliente']
        assert (c['nome'],c['nome_origem'])==('Marco Antonio','informado')
        await conn.execute("UPDATE contatos SET nome_cadastro='Marco Cadastro' WHERE id=$1",ct)
        assert await nc.gravar_informado(conn,'bykoji','bykoji:canal:x','Outro Nome') is True
        r=await conn.fetchrow('SELECT nome_cadastro,nome_informado FROM contatos WHERE id=$1',ct)
        assert (r['nome_cadastro'],r['nome_informado'])==('Marco Cadastro','Outro Nome')
        c=(await skyone.pacote(repo,'bykoji','bykoji:canal:x',atual,'oi',cfg))['cliente'];assert (c['nome'],c['nome_origem'])==('Marco Cadastro','cadastro')
        # Contato anonimizado nunca recebe nome de volta; outra empresa não alcança a conversa.
        await conn.execute("UPDATE contatos SET anonimizado_em=now(),nome_cadastro=NULL,nome_informado=NULL,nome_perfil=NULL WHERE id=$1",ct)
        assert await nc.gravar_informado(conn,'bykoji','bykoji:canal:x','Marco') is False
        assert await nc.gravar_informado(conn,'outra','bykoji:canal:x','Marco') is False
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_conversas_e_contatos_no_painel(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch,whatsapp=None)
            async def item(conv,**params):
                r=await client.get(C,params=params);assert r.status_code==200,r.text
                return next(i for i in r.json()['itens'] if i['conversation_id']==conv)
            # Perfil do WhatsApp atualizado a cada mensagem do cliente; a do atendente (fromMe) não mexe no nome.
            conv=(await webhook(CLIENTE,'oi',senderName='Marco A.'))['conversation_id']
            await webhook(CLIENTE,'tudo bem?',senderName='Marco Antonio')
            await webhook(CLIENTE,'Olá, aqui é a loja',fromMe=True,senderName='Loja Bykoji')
            ct=await conn.fetchrow("SELECT * FROM contatos WHERE tenant_id='bykoji' AND telefone=$1",CLIENTE)
            assert ct['nome_perfil']=='Marco Antonio' and ct['nome_cadastro'] is None and ct['nome_informado'] is None
            i=await item(conv)
            assert (i['nome_exibicao'],i['nome_origem'])==('Marco Antonio','perfil') and i['nomes']=={'cadastro':None,'informado':None,'perfil':'Marco Antonio'}
            assert i['telefone_e164']=='+'+CLIENTE and i['telefone_formatado']=='+55 11 90000-0001' and i['contato_id']==ct['id']
            assert i['canal_info']=={'id':canal,'tipo':'uazapi','nome':'WhatsApp','icone':'whatsapp','teste':False}
            assert i['ultima']['texto']=='Olá, aqui é a loja' and (i['ultima']['direcao'],i['ultima']['origem'],i['ultima']['tipo'])==('saida','humano','texto') and i['ultima']['em']
            # Campos antigos continuam (nada quebra).
            for antigo in ('nome','telefone','status','ultima_mensagem','ultima_em','aguardando_motivo','bot_pausado_ate'): assert antigo in i
            # Informado (agente) vence o perfil; cadastro (painel, qualquer papel) vence os dois; null volta ao informado.
            assert await nc.gravar_informado(conn,'bykoji',conv,'Marquinho')
            assert ((await item(conv))['nome_exibicao'],(await item(conv))['nome_origem'])==('Marquinho','informado')
            assert (await client.patch(f'{K}/{ct["id"]}',json={'nome':'Ana'},headers={'X-Role':'membro'})).status_code==403
            r=await client.patch(f'{K}/{ct["id"]}',json={'nome':'  Marco   Silva '},headers=MEMBRO);assert r.status_code==200,r.text
            assert (r.json()['nome_exibicao'],r.json()['nome_origem'])==('Marco Silva','cadastro') and r.json()['nomes']=={'cadastro':'Marco Silva','informado':'Marquinho','perfil':'Marco Antonio'}
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id='bykoji' AND acao='contato.nome_cadastro' AND alvo=$1 AND actor='joana@loja.com'",ct['id'])==1
            await nc.gravar_informado(conn,'bykoji',conv,'Outro')
            assert ((await item(conv))['nome_exibicao'])=='Marco Silva' and await conn.fetchval('SELECT nome_cadastro FROM contatos WHERE id=$1',ct['id'])=='Marco Silva'
            # Busca procura nos três nomes.
            assert [x['conversation_id'] for x in (await client.get(C,params={'busca':'silva'})).json()['itens']]==[conv]
            assert [x['conversation_id'] for x in (await client.get(C,params={'busca':'outro'})).json()['itens']]==[conv]
            assert (await client.get(C,params={'busca':'ninguem'})).json()['itens']==[]
            # PATCH inválido, sem X-Actor, contato de outra empresa, inexistente.
            for ruim,motivo in (('M','tamanho'),('11 94728-1513','telefone'),('Ana 😀','caractere')):
                r=await client.patch(f'{K}/{ct["id"]}',json={'nome':ruim},headers=MEMBRO);assert r.status_code==400 and r.json()['erro']['detalhes']=={'campo':'nome','motivo':motivo},ruim
            assert (await client.patch(f'{K}/{ct["id"]}',json={},headers=MEMBRO)).status_code==400
            assert (await client.patch(f'{K}/{ct["id"]}',json={'nome':'Ana','extra':1},headers=MEMBRO)).status_code==400
            assert (await client.patch(f'{K}/{ct["id"]}',json={'nome':'Ana'},headers={'X-Actor':''})).status_code==400
            await criar(client)
            assert (await client.patch(f'/admin/v1/tenants/alpha/contatos/{ct["id"]}',json={'nome':'Invasor'},headers=MEMBRO)).status_code==404
            assert (await client.patch(f'{K}/ct_inexistente',json={'nome':'Ana'},headers=MEMBRO)).status_code==404
            assert await conn.fetchval('SELECT nome_cadastro FROM contatos WHERE id=$1',ct['id'])=='Marco Silva'
            r=await client.patch(f'{K}/{ct["id"]}',json={'nome':None},headers=MEMBRO);assert (r.json()['nome_exibicao'],r.json()['nome_origem'])==('Outro','informado')
            await client.patch(f'{K}/{ct["id"]}',json={'nome':'Marco Silva'},headers=MEMBRO)
            # Pedido aberto e unidade escolhida; o pedido nasce com o nome de exibição (nunca o telefone).
            c2,numero=await pedido()
            assert c2==conv
            i=await item(conv)
            assert i['pedido_aberto']=={'numero':numero,'status':'aguardando_loja'} and (i['unidade_preferida'],i['unidade_preferida_nome'])==('patio_guedala','Pátio Guedala')
            assert await conn.fetchval("SELECT cliente_nome FROM pedidos WHERE numero=$1",numero)=='Marco Silva'
            # {nome_cliente} no aviso usa o nome de hoje.
            await live()
            assert (await client.put('/admin/v1/tenants/bykoji/pedidos/avisos/confirmado',json={'texto':'Oi {nome_cliente}! Pedido {numero} ok.'},headers={'X-Actor':'ana','X-Role':'admin'})).status_code==200
            await client.patch(f'{K}/{ct["id"]}',json={'nome':'Joana Lima'},headers=MEMBRO)
            assert (await client.post(f'/admin/v1/tenants/bykoji/pedidos/{numero}/status',json={'status':'confirmado'},headers=MEMBRO)).status_code==200
            assert await conn.fetchval("SELECT texto FROM avisos_internos WHERE tipo='pedido_cliente' AND referencia=$1",numero+':confirmado')==f'Oi Joana! Pedido {numero} ok.'
            assert (await client.post(f'/admin/v1/tenants/bykoji/pedidos/{numero}/status',json={'status':'cancelado','motivo':'teste'},headers=MEMBRO)).status_code==200
            assert (await item(conv))['pedido_aberto'] is None
            # Estado único nos quatro casos, com o filtro (inclusive o de encerradas).
            await conn.execute("UPDATE conversas SET bot_pausado_ate=NULL,aguardando_atendente_desde=NULL,aguardando_motivo=NULL,encerrada_em=NULL WHERE conversation_id=$1",conv)
            outra=(await webhook('5511900000002','oi',senderName='.'))['conversation_id']
            o=await item(outra);assert (o['nome_exibicao'],o['nome_origem'])==('+55 11 90000-0002','telefone')
            async def estado(sql=None):
                await conn.execute("UPDATE conversas SET bot_pausado_ate=NULL,aguardando_atendente_desde=NULL,aguardando_motivo=NULL,encerrada_em=NULL WHERE conversation_id=$1",outra)
                if sql: await conn.execute(f"UPDATE conversas SET {sql} WHERE conversation_id=$1",outra)
                return (await item(outra))['estado']
            assert await estado()=='bot' and await estado("bot_pausado_ate=now()-interval '1 minute'")=='bot'
            assert await estado("bot_pausado_ate=now()+interval '1 hour'")=='atendente'
            assert await estado("bot_pausado_ate=now()+interval '1 hour',aguardando_atendente_desde=now(),aguardando_motivo='erro_agente'")=='aguardando_atendente'
            assert await estado("bot_pausado_ate=now()+interval '1 hour',aguardando_atendente_desde=now(),aguardando_motivo='erro_agente',encerrada_em=now()")=='encerrada'
            assert [x['conversation_id'] for x in (await client.get(C,params={'estado':'encerrada'})).json()['itens']]==[outra]
            assert [x['conversation_id'] for x in (await client.get(C,params={'estado':'bot'})).json()['itens']]==[conv]
            assert {x['conversation_id'] for x in (await client.get(C,params={'estado':'bot,encerrada'})).json()['itens']}=={conv,outra}
            assert (await client.get(C,params={'estado':'atendente'})).json()['itens']==[]
            for ruim in ('fechada','bot,'+'x','ENCERRADA'): assert (await client.get(C,params={'estado':ruim})).status_code==400,ruim
            # ordem=recentes: última atividade primeiro, cursor próprio; a ordem padrão não muda.
            await conn.execute("UPDATE conversas SET atualizado_em=now()+interval '1 minute' WHERE conversation_id=$1",outra)
            p1=(await client.get(C,params={'ordem':'recentes','limite':1})).json()
            assert [x['conversation_id'] for x in p1['itens']]==[outra] and '|' in p1['proximo_cursor']
            p2=(await client.get(C,params={'ordem':'recentes','limite':1,'cursor':p1['proximo_cursor']})).json()
            assert [x['conversation_id'] for x in p2['itens']]==[conv] and p2['proximo_cursor'] is None
            assert [x['conversation_id'] for x in (await client.get(C)).json()['itens']]==sorted([conv,outra])
            for ruim in ('lixo','2026-10-11T00:00:00|','2026-10-11T00:00:00|x'): assert (await client.get(C,params={'ordem':'recentes','cursor':ruim})).status_code==400,ruim
            assert (await client.get(C,params={'ordem':'antiga'})).status_code==400
            # Contatos: nome pronto, telefones, quantidade e última conversa.
            lista={x['id']:x for x in (await client.get(K)).json()['itens']}
            x=lista[ct['id']]
            assert (x['nome_exibicao'],x['nome_origem'],x['telefone_e164'],x['telefone_formatado'],x['conversas'])==('Joana Lima','cadastro','+'+CLIENTE,'+55 11 90000-0001',1) and x['ultima_conversa_em']
            assert x['telefone']==CLIENTE and 'nome' in x and x['nomes']['perfil']=='Cliente Teste'   # o pedido veio com outro senderName
            assert [y['id'] for y in (await client.get(K,params={'busca':'joana'})).json()['itens']]==[ct['id']]
            # Isolamento: outra empresa não vê as conversas nem os contatos.
            assert (await client.get('/admin/v1/tenants/alpha/conversas')).json()['itens']==[] and (await client.get('/admin/v1/tenants/alpha/contatos')).json()['itens']==[]
            # Anonimizar: nomes e telefones nulos, "Contato anonimizado", PATCH recusado, linha do tempo sem "por".
            r=await client.request('DELETE',f'{K}/{ct["id"]}',json={'confirmacao':CLIENTE});assert r.status_code==200,r.text
            anon=next(i for i in (await client.get(C)).json()['itens'] if i['contato_id']==ct['id'])
            assert (anon['nome_exibicao'],anon['nome_origem'],anon['telefone_e164'],anon['telefone'],anon['nome'])==('Contato anonimizado','anonimizado',None,None,None)
            assert anon['nomes']=={'cadastro':None,'informado':None,'perfil':None}
            linha=await conn.fetchrow('SELECT nome,nome_cadastro,nome_informado,nome_perfil,nome_cadastro_por FROM contatos WHERE id=$1',ct['id'])
            assert all(v is None for v in linha.values())
            assert (await client.patch(f'{K}/{ct["id"]}',json={'nome':'Volta'},headers=MEMBRO)).status_code==409
            assert await conn.fetchval("SELECT count(*) FROM pedido_eventos e JOIN pedidos p ON p.id=e.pedido_id WHERE p.numero=$1 AND (e.por<>'anonimizado' OR e.motivo IS NOT NULL)",numero)==0
            assert 'Joana' not in str((await client.get(C)).json()) and CLIENTE not in str((await client.get(K)).json())
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_canal_de_teste_e_simulacao():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,ca=await criar(client);await ativo(client,'alpha',a,ca)
            r=await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':ca,'identificador':'marco','texto':'olá'});assert r.status_code==200
            sim=r.json()['conversation_id']
            i=(await client.get('/admin/v1/tenants/alpha/conversas')).json()['itens'][0]
            assert i['conversation_id']==sim and i['canal_info']=={'id':ca,'tipo':'api','nome':'C','icone':'code','teste':True}
            assert (i['nome_exibicao'],i['nome_origem'],i['telefone_e164'])==('marco','identificador',None)
            # Mesma conversa por API de verdade continua marcada; empresa de teste marca tudo.
            await conn.execute("UPDATE conversas SET simulada=false WHERE conversation_id=$1",sim)
            assert (await client.get('/admin/v1/tenants/alpha/conversas')).json()['itens'][0]['canal_info']['teste'] is False
            await conn.execute("UPDATE tenants SET tenant_teste=true WHERE id='alpha'")
            assert (await client.get('/admin/v1/tenants/alpha/conversas')).json()['itens'][0]['canal_info']['teste'] is True
    banco(cenario)
