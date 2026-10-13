"""AVS1 (docs/ESPEC_AVS1.md): "digitando…" pelo turno inteiro como padrão; texto de espera só no modo com texto e no meio de algo."""
import asyncio,json
from urllib.parse import urlsplit,parse_qs
import httpx,pytest
from canais import uazapi
from nucleo import pipeline as PL
from nucleo.configuracao import PADROES
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_uazapi_real import fx
from test_fila import banco_real,montar as montar_fila,limpar
from test_fase4_pedidos import montar as montar_pedidos

T,A='bykoji','bykoji_atendimento'
TEXTOS=['Já te respondo 😊','Só mais um instantinho','Um momento, por favor']


def test_parece_saudacao_e_padroes():
    s=lambda *textos,**m: PL.parece_saudacao([{'conteudo':t,'tipo':'texto',**m} for t in textos])
    for texto in ('Oi','oi!','Olá','Ola','Opa','Bom dia','boa tarde!','Oi, tudo bem?','E aí','oi 😊','👋','Olá, boa noite','Oi','Tem alguém aí?'):
        assert s(texto),texto
    assert s('Oi','Boa tarde')
    for texto in ('Oi, quero um temaki','Vocês abrem hoje?','Finalizar pedido','sim','13:00','Quero fazer um pedido','obrigado','Oi, meu pedido já saiu?'):
        assert not s(texto),texto
    # Toque em botão e anexo não são cumprimento.
    assert not s('Oi',meta={'interacao':{'tipo':'botao','id':'oi'}}) and not PL.parece_saudacao([{'conteudo':'[áudio]','tipo':'audio'}])
    assert (PADROES['aviso_espera_modo'],PADROES['aviso_espera_textos'],PADROES['slow_notice_s'])==('presenca',TEXTOS,30)
    assert 'verificando' not in ' '.join(TEXTOS).lower() and 'Estou vendo aqui pra você' not in TEXTOS


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_digitando_pelo_turno_inteiro_sem_texto_e_configuracao(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async def livre(url): return None
        monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',livre)
        monkeypatch.setattr(PL,'RENOVAR_DIGITANDO_S',0.4)
        p=app.state.pipeline;chamadas=[];demora=[1.5];resultado=[{'resposta':['pronto'],'acao':'nenhuma'}]
        def provedor(req):
            corpo=json.loads(req.content);chamadas.append((req.url.path,corpo.get('presence'),corpo.get('delay'),corpo.get('text')))
            return httpx.Response(200,json={'messageid':'3EB0ENVIO%012d'%len(chamadas)})
        p.http=httpx.AsyncClient(transport=httpx.MockTransport(provedor))
        async def lento(http,repo,cofre,tenant,cfg,payload):
            await asyncio.sleep(demora[0]);return dict(resultado[0])
        p.chamar=lento
        async with client:
            a,_=await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a,
                'credenciais':{'base_url':'https://instancia.exemplo.com','token':'tok-1'}})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal,debounce_s=1,max_wait_s=1,slow_notice_s=1)
            await conn.execute("UPDATE agentes SET send_mode='live' WHERE id=$1",a);app.state.configuracoes.invalidar('alpha')
            url=f'/admin/v1/tenants/alpha/agentes/{a}'
            n=[0]
            async def turno(texto='quero um temaki'):
                n[0]+=1;chamadas.clear();pl=fx('texto');pl['message'].update(messageTimestamp=__import__('time').time()*1000,text=texto,content=texto,messageid=f'3EB0ENTRADA{n[0]:010d}',id=f'x:3EB0ENTRADA{n[0]:010d}')
                conv=(await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=pl)).json()['conversation_id']
                assert await p.processar_duravel('alpha',conv)=='concluido';return conv
            # Padrão: "digitando…" renovado enquanto o agente processa (1,5 s com renovação a cada 0,4 s = pelo menos 3 pedidos), sem texto de espera.
            cfg=await app.state.configuracoes.efetiva('alpha',a);assert (cfg['aviso_espera_modo'],cfg['aviso_espera_textos'],cfg['origem_config']['aviso_espera_modo'])==('presenca',TEXTOS,'plataforma')
            conv=await turno()
            presencas=[c for c in chamadas if c[0]=='/message/presence'];envios=[c for c in chamadas if c[0]=='/send/text']
            assert len(presencas)>=3 and all(c[1:3]==('composing',25000) for c in presencas) and envios==[('/send/text',None,None,'pronto')]
            assert chamadas[-1][0]=='/send/text' and await conn.fetchval('SELECT bool_or(aviso_demora) FROM turnos WHERE conversation_id=$1',conv) is False
            # Resposta rápida: um pedido de "digitando" só.
            demora[0]=0;await turno();assert [c[0] for c in chamadas]==['/message/presence','/send/text']
            # Silêncio: nada é enviado e o "digitando" é retirado no fim.
            demora[0]=0.9;resultado[0]={'resposta':[],'acao':'silencio','motivo':'agradecimento_final'};await turno('obrigado')
            assert chamadas[-1][:2]==('/message/presence','paused') and not [c for c in chamadas if c[0]=='/send/text'] and len([c for c in chamadas if c[1]=='composing'])>=2
            # Modo off: nem "digitando" nem texto.
            resultado[0]={'resposta':['pronto'],'acao':'nenhuma'}
            assert (await client.patch(url,json={'aviso_espera_modo':'off'})).status_code==200
            await turno();assert [c[0] for c in chamadas]==['/send/text']
            # Configuração: valores aceitos e recusados.
            for ruim in ({'aviso_espera_modo':'sempre'},{'aviso_espera_textos':[]},{'aviso_espera_textos':['a']},{'aviso_espera_textos':['x'*161]},
                         {'aviso_espera_textos':['um texto']*7},{'aviso_espera_textos':'Um momento'},{'aviso_espera_textos':[1,2]}):
                assert (await client.patch(url,json=ruim)).status_code==400,ruim
            assert (await client.patch(url,json={'aviso_espera_modo':'presenca_e_texto_contextual','aviso_espera_textos':['  Só um segundo  ','Já volto']})).status_code==200
            cfg=await app.state.configuracoes.efetiva('alpha',a);assert (cfg['aviso_espera_modo'],cfg['aviso_espera_textos'],cfg['origem_config']['aviso_espera_textos'])==('presenca_e_texto_contextual',['Só um segundo','Já volto'],'agente')
            assert (await client.patch(url,json={'aviso_espera_modo':None,'aviso_espera_textos':None})).status_code==200
            cfg=await app.state.configuracoes.efetiva('alpha',a);assert (cfg['aviso_espera_modo'],cfg['aviso_espera_textos'])==('presenca',TEXTOS)
            # O painel recebe os dois campos no esquema dos comportamentos.
            esq=(await client.get(f'/admin/v1/tenants/alpha/agentes/{a}/atendimento')).json()['esquema']['comportamentos']
            assert esq['aviso_espera_modo']['opcoes']==['off','presenca','presenca_e_texto_contextual'] and esq['aviso_espera_modo']['grupo']=='mensagens' and esq['aviso_espera_textos']['tipo']=='lista_textos'
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
def test_texto_de_espera_so_no_meio_de_algo(tmp_path,monkeypatch):
    async def cenario(pool):
        app,c,r,p,pref=await montar_fila(pool,True,tmp_path)
        try:
            async with c:
                conversa,ids,api,_=await montar_pedidos(pool,c,app)
                async def modo(**campos): assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json=campos)).status_code==200,campos
                async def chegar(conv,texto): await pool.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ($1,$2,'entrada','cliente',$3)",T,conv,texto)
                saidas=lambda conv: pool.fetch("SELECT conteudo,parte FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' ORDER BY id",T,conv)
                envelhecer=lambda conv,s: pool.execute("UPDATE mensagens SET criado_em=criado_em-make_interval(secs=>$3) WHERE tenant_id=$1 AND conversation_id=$2",T,conv,float(s))
                roteiro=[]
                async def agente(http,repo,cofre,tenant,cfg,payload): return await roteiro.pop(0)(payload)
                p.chamar=agente
                def fala(texto,demora=1.6,antes=None,**campos):
                    async def passo(payload):
                        if antes: await antes(payload)
                        await asyncio.sleep(demora);return {'resposta':[texto],'acao':'nenhuma',**campos}
                    return passo
                async def turno(conv,cliente,resposta,**kw):
                    if cliente is not None: await chegar(conv,cliente)
                    roteiro[:]=[fala(resposta,**kw)];assert await p.processar_duravel(T,conv)=='concluido'
                    return [m['conteudo'] for m in await saidas(conv) if m['parte']==0]
                await modo(slow_notice_s=1)

                # Modo padrão (só "digitando"): mesmo no meio de um pedido e com o agente lento, nenhum texto de espera.
                c0=await conversa('p0');await turno(c0,None,'Qual temaki você quer?');await envelhecer(c0,180)
                assert await turno(c0,'o de salmão','Anotado. Mais alguma coisa?')==[]

                await modo(aviso_espera_modo='presenca_e_texto_contextual')
                # 1) Primeira mensagem de uma conversa (o bot nunca falou), saudação ou não: sem texto.
                c1=await conversa('a1');assert await turno(c1,None,'Oi! Qual temaki você quer?')==[]
                c2=await conversa('a2');await pool.execute("UPDATE mensagens SET conteudo='quero um temaki de salmão' WHERE conversation_id=$1",c2)
                assert await turno(c2,None,'Temos sim. Quantos?')==[]
                # 2) Logo depois de o bot falar (janela de 120 s): sem texto.
                assert await turno(c1,'o de salmão','Quantos você quer?')==[]
                # 3) Meio da conversa: o bot perguntou há 3 minutos, o cliente responde e o agente demora → sai UM texto, da lista aprovada.
                await envelhecer(c1,180);avisos=await turno(c1,'dois','Anotei 2. Mais alguma coisa?')
                assert len(avisos)==1 and avisos[0] in TEXTOS
                assert await pool.fetchval("SELECT aviso_demora FROM turnos WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1",c1) is True
                assert [m['parte'] for m in await saidas(c1)][-2:]==[0,1]              # o aviso vem antes da resposta, no mesmo turno
                # 4) Outro turno lento menos de 5 minutos depois do aviso: sem texto. Passados os 5 minutos: sai, e não repete o anterior.
                await envelhecer(c1,150);assert len(await turno(c1,'uma coca','Anotado. Mais alguma coisa?'))==1
                vistos=[avisos[0]]
                for _ in range(3):
                    await envelhecer(c1,320);avisos=await turno(c1,'mais uma','Certo. Algo mais?')
                    assert avisos[-1] in TEXTOS and avisos[-1]!=vistos[-1];vistos.append(avisos[-1])
                # 5) Saudação e agradecimento no meio da conversa: sem texto.
                quantos=len(await turno(c1,None,'x',demora=0)) if False else len([m for m in await saidas(c1) if m['parte']==0])
                await envelhecer(c1,320);assert len(await turno(c1,'Oi','Oi! Posso ajudar em algo mais?'))==quantos
                await envelhecer(c1,320);assert len(await turno(c1,'obrigado','Por nada! Algo mais?'))==quantos
                # 6) Retomada: o bot falou há mais de 10 minutos → sem texto, mesmo com pergunta pendente.
                await envelhecer(c1,700);assert len(await turno(c1,'quero mais um temaki','Claro. Qual?'))==quantos
                # 7) Resposta rápida nunca tem texto.
                await envelhecer(c1,320);assert len(await turno(c1,'o de atum','Anotado. Algo mais?',demora=0))==quantos

                # 8) A última fala do bot não foi pergunta nem opções e não há carrinho nem Skill: sem texto.
                c3=await conversa('a3');await turno(c3,None,'Estamos abertos até as 22h.');await envelhecer(c3,180)
                assert await turno(c3,'e amanhã','Amanhã também.')==[]
                # Com item no carrinho, a mesma situação é "pedido em andamento": sai o texto.
                assert (await api('POST',f'/carrinho/{c3}/itens',json={'item_id':ids['GUE-0001']})).status_code==200
                await envelhecer(c3,320);assert len(await turno(c3,'pode ser para retirar','Combinado.'))==1
                # Skill chamada neste turno (busca em curso) também conta, sem carrinho e sem pergunta.
                c4=await conversa('a4');await turno(c4,None,'Estamos abertos até as 22h.');await envelhecer(c4,180)
                async def busca(payload): await api('GET','/catalogo/busca',params={'conversation_id':payload['conversation_id'],'q':'edamame'})
                assert len(await turno(c4,'tem edamame','Temos, R$ 34.',antes=busca))==1
                # 9) Texto próprio do agente: sai o texto configurado.
                await modo(aviso_espera_textos=['Só um segundo, por favor']);c5=await conversa('a5');await turno(c5,None,'Qual você quer?');await envelhecer(c5,180)
                assert await turno(c5,'o primeiro','Anotado.')==['Só um segundo, por favor']
                # 10) Turno refeito nunca avisa.
                await modo(refazer_turno='on',aviso_espera_textos=None);c6=await conversa('a6');await turno(c6,None,'Qual você quer?');await envelhecer(c6,180)
                async def chega(payload): await chegar(payload['conversation_id'],'e uma coca')
                await chegar(c6,'o de salmão');roteiro[:]=[fala('descartada',0,chega),fala('Anotei os dois.',1.6)];assert await p.processar_duravel(T,c6)=='concluido'
                assert [m['parte'] for m in await saidas(c6) if m['parte']==0]==[]
                ts=await pool.fetch("SELECT status,aviso_demora FROM turnos WHERE conversation_id=$1 ORDER BY id DESC LIMIT 2",c6);assert [(t['status'],t['aviso_demora']) for t in ts]==[('ok',False),('refeito',False)]
                m=(await c.get(f'/admin/v1/tenants/{T}/metricas')).json();assert m['avisos_de_espera_enviados']==await pool.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND parte=0",T)>=7
        finally:
            await limpar(r,pref)
    banco_real(cenario)
