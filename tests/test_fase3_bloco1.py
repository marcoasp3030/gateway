"""Fase 3, bloco 1: debounce adaptativo, aviso de demora e "digitando"."""
import asyncio,json
from urllib.parse import urlsplit,parse_qs
import httpx,pytest
from canais import uazapi
from nucleo.pipeline import debounce_da_mensagem,parece_fechamento
from nucleo.configuracao import PADROES
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_uazapi_real import fx

CFG={'debounce_s':4,'debounce_curto_s':1.5,'debounce_palavras':6}


def test_debounce_da_mensagem():
    d=lambda texto,cfg=CFG,**m: debounce_da_mensagem(cfg,{'texto':texto,**m})
    # Parece completa: pontuação de fim de frase (mesmo com emoji ou aspas depois) ou mais de 6 palavras.
    for texto in ('vocês entregam no Morumbi?','Quero um temaki de salmão.','obrigado!','tudo bem? 🙂','"pode ser?"',
                  'quero dois temakis e uma coca zero por favor'):
        assert d(texto)==1.5,texto
    # UX2 (P1): reticências deixaram de contar como frase completa (a frase continua); sem debounce_incompleta_s, espera o normal.
    assert d('então…')==4
    # Curta ou picada: espera o debounce normal.
    for texto in ('oi','quero','um temaki','de salmão','Sim','rua das flores 123','um dois três quatro cinco seis','',None):
        assert d(texto)==4,texto
    # Mídia, localização e reação não encurtam, mesmo com ponto no texto gerado pelo gateway.
    assert d('[não foi possível ler áudio de 6 s: leitura deste tipo de anexo ainda não está disponível.]',tipo='audio')==4
    assert d('Foto do pedido?',tipo='imagem')==4 and d('[localização: -23.5, -46.6]',tipo='localizacao')==4
    # O curto nunca aumenta a espera de quem configurou debounce menor.
    assert d('oi?',{**CFG,'debounce_s':1})==1 and d('oi',{**CFG,'debounce_s':1})==1
    assert d('um dois três',{**CFG,'debounce_palavras':2})==1.5
    assert PADROES['debounce_curto_s']==1.5 and PADROES['debounce_palavras']==6 and PADROES['slow_notice_s']==20


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_debounce_adaptativo_no_agendamento_e_configuracao():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,can=await criar(client)
            await ativo(client,'alpha',a,can)
            cfg=(await client.get(f'/admin/v1/tenants/alpha/agentes/{a}')).json()['configuracao_efetiva']
            assert (cfg['debounce_s'],cfg['debounce_curto_s'],cfg['debounce_palavras'],cfg['max_wait_s'],cfg['slow_notice_s'])==(4,1.5,6,12,20)
            n=0
            async def manda(texto,mid=None):
                nonlocal n;n+=1
                r=await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':'u','texto':texto,'message_id':mid or f'm{n}'})
                assert r.status_code==200,r.text
                return fila.agendados[-1][-2:]   # (espera, teto) passados ao agendamento
            # Mensagens picadas esperam 4 s; a que fecha a frase encurta para 1,5 s. O teto não muda.
            assert await manda('quero')==(4,12) and await manda('um temaki')==(4,12) and await manda('de salmão, pode ser?')==(1.5,12)
            # Reenvio da mesma mensagem (webhook duplicado) só repara o agendamento, com a espera normal.
            assert await manda('de salmão, pode ser?','m3')==(4,12)
            # Configurável por agente e por empresa; nulo volta a herdar.
            assert (await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'debounce_curto_s':2.5,'debounce_palavras':2})).status_code==200
            assert await manda('tudo certo?')==(2.5,12) and await manda('um dois três')==(2.5,12) and await manda('um dois')==(4,12)
            assert (await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'debounce_curto_s':None,'debounce_palavras':None})).status_code==200
            assert (await client.put('/admin/v1/tenants/alpha/config',json={'debounce_curto_s':1})).status_code==200
            assert await manda('tudo certo?')==(1,12)
            for ruim in ({'debounce_curto_s':0.1},{'debounce_curto_s':16},{'debounce_palavras':0},{'debounce_palavras':51},{'debounce_curto_s':'x'}):
                assert (await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json=ruim)).status_code in (400,422),ruim
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_digitando_enquanto_o_agente_processa(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async def livre(url): return None
        monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',livre)
        p=app.state.pipeline;chamadas=[];presenca_falha=False;roteiro=[];envios=[]
        def provedor(req):
            corpo=json.loads(req.content)
            chamadas.append((req.url.path,corpo.get('presence'),corpo.get('delay'),corpo.get('text')))
            if req.url.path=='/message/presence' and presenca_falha: return httpx.Response(500,json={'error':'x'})
            envios.append(1)   # id único por envio, como no provedor real
            return httpx.Response(200,json={'messageid':'3EB0ENVIO%012d'%len(envios),'response':'ok'})
        p.http=httpx.AsyncClient(transport=httpx.MockTransport(provedor))
        async def agente(http,repo,cofre,tenant,cfg,payload):
            await asyncio.sleep(0.05);return roteiro.pop(0)
        p.chamar=agente
        async with client:
            a,_=await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a,
                'credenciais':{'base_url':'https://instancia.exemplo.com','token':'tok-1'}})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal,debounce_s=1,max_wait_s=1)
            n=0
            async def turno(resultado):
                nonlocal n;n+=1;roteiro[:]=[resultado];chamadas.clear()
                pl=fx('texto');pl['message'].update(messageid=f'MSGDOCLIENTE{n:04d}',messageTimestamp=__import__('time').time()*1000)
                conv=(await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=pl)).json()['conversation_id']
                assert await p.processar_duravel('alpha',conv)=='concluido'
                return await conn.fetchval('SELECT status FROM turnos WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1',conv)
            # Envio de teste (dry): nenhuma chamada ao canal, nem "digitando".
            assert await turno({'resposta':['oi'],'acao':'nenhuma'})=='ok' and chamadas==[]
            await conn.execute("UPDATE agentes SET send_mode='live' WHERE id=$1",a);app.state.configuracoes.invalidar('alpha')
            # Envio real: "digitando" antes da resposta; o envio da mensagem encerra no canal.
            assert await turno({'resposta':['balão 1','balão 2'],'acao':'nenhuma'})=='ok'
            assert chamadas==[('/message/presence','composing',25000,None),('/send/text',None,None,'balão 1'),('/send/text',None,None,'balão 2')]
            # Silêncio: nada é enviado, então o "digitando" é retirado.
            assert await turno({'resposta':[],'acao':'silencio','motivo':'agradecimento'})=='ok'
            assert chamadas==[('/message/presence','composing',25000,None),('/message/presence','paused',None,None)]
            # Falha no "digitando" não atrapalha a resposta.
            presenca_falha=True
            assert await turno({'resposta':['segue normal'],'acao':'nenhuma'})=='ok'
            assert [c for c in chamadas if c[0]=='/send/text']==[('/send/text',None,None,'segue normal')]
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_aviso_de_demora_uma_vez_e_digitando_de_novo(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async def livre(url): return None
        monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',livre)
        p=app.state.pipeline;chamadas=[]
        def provedor(req):
            corpo=json.loads(req.content);chamadas.append((req.url.path,corpo.get('presence'),corpo.get('text')))
            return httpx.Response(200,json={'messageid':'3EB0ENVIO%012d'%len(chamadas)})
        p.http=httpx.AsyncClient(transport=httpx.MockTransport(provedor))
        async def lento(http,repo,cofre,tenant,cfg,payload):
            await asyncio.sleep(1.4);return {'resposta':['pronto'],'acao':'nenhuma'}
        p.chamar=lento
        async with client:
            a,_=await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a,
                'credenciais':{'base_url':'https://instancia.exemplo.com','token':'tok-1'}})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal,debounce_s=1,max_wait_s=1,slow_notice_s=1,aviso_demora='Só um instante.')
            await conn.execute("UPDATE agentes SET send_mode='live' WHERE id=$1",a);app.state.configuracoes.invalidar('alpha')
            pl=fx('texto');pl['message'].update(messageTimestamp=__import__('time').time()*1000)
            conv=(await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=pl)).json()['conversation_id']
            assert await p.processar_duravel('alpha',conv)=='concluido'
            assert chamadas==[('/message/presence','composing',None),('/send/text',None,'Só um instante.'),
                              ('/message/presence','composing',None),('/send/text',None,'pronto')]
            assert await conn.fetchval('SELECT aviso_demora FROM turnos WHERE conversation_id=$1',conv) is True
    banco(cenario)


def test_parece_fechamento():
    f=lambda *textos,tipo='texto': parece_fechamento([{'conteudo':t,'tipo':tipo} for t in textos])
    for lote in (('obrigado',),('Obrigada!',),('ok',),('valeu',),('blz',),('👍',),('🙏🙏',),('Ok, valeu!',),('tá bom, obrigado',),
                 ('muito obrigado',),('show',),('perfeito, até mais',),('obg',),('ok','obrigado'),('é isso, valeu',),('nada, só isso',)):
        assert f(*lote),lote
    # Pergunta, pedido, saudação ou qualquer palavra fora da lista: não é fechamento.
    for lote in (('obrigado, e a taxa?',),('ok?',),('oi',),('bom dia',),('quero um temaki',),('ok, pode mandar dois',),('sim, quero',),('sim',),('pode',),('tudo bem',),
                 ('obrigado mas ainda não chegou',),('valeu','qual o prazo'),('obrigado obrigado obrigado obrigado obrigado obrigado obrigado',),('',)):
        assert not f(*lote) or lote==('',),lote
    assert f('') is True   # só pontuação/vazio: nada a responder
    assert not f('obrigado',tipo='audio') and not parece_fechamento([{'conteudo':'ok','tipo':'texto'},{'conteudo':'[localização: 1, 2]','tipo':'localizacao'}])


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
@pytest.mark.parametrize('duravel',[True,False])
def test_fechamento_nao_recebe_aviso_de_demora(duravel):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        p=app.state.pipeline;roteiro=[]
        async def lento(http,repo,cofre,tenant,cfg,payload):
            await asyncio.sleep(1.3);return roteiro.pop(0)
        p.chamar=lento
        async with client:
            a,can=await criar(client)
            await ativo(client,'alpha',a,can,debounce_s=1,max_wait_s=1,slow_notice_s=1,aviso_demora='Só um instante.')
            n=0
            async def turno(usuario,texto,resultado):
                nonlocal n;n+=1;roteiro[:]=[resultado]
                conv=(await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':usuario,'texto':texto,'message_id':f'm{n}'})).json()['conversation_id']
                await (p.processar_duravel if duravel else p.processar)('alpha',conv)
                t=await conn.fetchrow('SELECT aviso_demora,acao,motivo_silencio FROM turnos WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1',conv)
                return t,[r[0] for r in await conn.fetch("SELECT conteudo FROM mensagens WHERE conversation_id=$1 AND origem='bot' ORDER BY id",conv)]
            # Fechamento + silêncio: o cliente não recebe nada, nem o aviso.
            t,bot=await turno('u1','obrigado',{'resposta':[],'acao':'silencio','motivo':'agradecimento_final'})
            assert bot==[] and not t['aviso_demora'] and (t['acao'],t['motivo_silencio'])==('silencio','agradecimento_final')
            # Fechamento em que o agente decide responder: sai só a resposta, sem aviso antes.
            t,bot=await turno('u2','valeu!',{'resposta':['Por nada!'],'acao':'nenhuma'})
            assert bot==['Por nada!'] and not t['aviso_demora']
            # Pergunta com agente lento: o aviso continua saindo, uma vez.
            t,bot=await turno('u3','vocês entregam no Morumbi?',{'resposta':['Sim!'],'acao':'nenhuma'})
            assert bot==['Só um instante.','Sim!'] and t['aviso_demora']
    banco(cenario)
