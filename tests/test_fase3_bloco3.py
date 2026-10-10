"""Fase 3, bloco 3: INV03 antes do envio, #bot, encerramento, conversa vazia, SEG07 por contato, LGPD04."""
import asyncio,json,logging,time
from urllib.parse import urlsplit,parse_qs
import httpx,pytest
import seguranca
from canais import uazapi
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_uazapi_real import fx

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


async def cenario_uazapi(conn,client,app,**cfg):
    a,_=await criar(client)
    r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a})
    canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
    await ativo(client,'alpha',a,canal,debounce_s=1,max_wait_s=1,**cfg)
    n=[0]
    async def webhook(nome='texto',**campos):
        n[0]+=1;p=fx(nome);p['message'].update(messageid=f'MSG{n[0]:05d}WEBHOOK',messageTimestamp=int(time.time()*1000));p['message'].update(campos)
        r=await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=p);assert r.status_code==200,r.text
        return r.json()
    return a,canal,webhook,f'alpha:{canal}:5511900000001'


def test_INV03_resposta_descartada_quando_o_atendente_assume_durante_o_agente():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async with client:
            a,canal,webhook,conv=await cenario_uazapi(conn,client,app)
            async def agente(http,repo,cofre,tenant,cfg,payload):
                # Enquanto o agente pensa, o atendente responde pelo celular da instância.
                await webhook('atendente_celular')
                return {'resposta':['resposta do bot'],'acao':'nenhuma'}
            p.chamar=agente
            await webhook('texto')
            assert await p.processar_duravel('alpha',conv)=='concluido'
            t=await conn.fetchrow('SELECT status,etapa,acao FROM turnos WHERE conversation_id=$1',conv)
            assert (t['status'],t['etapa'])==('ignorado','concluido')
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND origem='bot'",conv)==0
            acoes=[r[0] for r in await conn.fetch("SELECT acao FROM audit_log WHERE tenant_id='alpha' AND alvo=$1 ORDER BY id",conv)]
            assert acoes.count('resposta.descartada')==1 and 'mensagem.enviada' not in acoes
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND direcao='entrada' AND processada_em IS NULL",conv)==0
            # Conversa já pausada antes de chamar o agente: nem chama, e não há "descartada".
            chamou=[]
            async def nao_chamar(*a,**k): chamou.append(1);return {'resposta':['x'],'acao':'nenhuma'}
            p.chamar=nao_chamar
            await webhook('texto');await p.processar_duravel('alpha',conv)
            assert chamou==[] and await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='resposta.descartada'")==1
    banco(cenario)


def test_comando_bot_do_atendente_retoma_na_hora_e_nao_vai_para_o_agente():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        vistos=[]
        async def agente(http,repo,cofre,tenant,cfg,payload): vistos.append(payload);return {'resposta':['voltei'],'acao':'nenhuma'}
        p.chamar=agente
        async with client:
            a,canal,webhook,conv=await cenario_uazapi(conn,client,app)
            pausa=lambda: conn.fetchrow('SELECT bot_pausado_ate,aguardando_atendente_desde FROM conversas WHERE conversation_id=$1',conv)
            await webhook('atendente_celular');assert (await pausa())['bot_pausado_ate'] is not None
            await conn.execute("UPDATE conversas SET aguardando_atendente_desde=now(),aguardando_motivo='erro_agente' WHERE conversation_id=$1",conv)
            # Cliente escreve durante a pausa: fica gravado, sem resposta.
            await webhook('texto');await p.processar_duravel('alpha',conv);assert vistos==[]
            await webhook('texto',text='ainda aí?',content='ainda aí?')
            agendados=len(fila.agendados)
            # Variações de caixa e espaço valem; o comando retoma e tira o "aguardando".
            r=await webhook('atendente_celular',text='  #BOT ',content='  #BOT ')
            assert r.get('comando')=='bot' and tuple(await pausa())==(None,None)
            m=await conn.fetchrow("SELECT origem,tipo,processada_em FROM mensagens WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1",conv)
            assert (m['origem'],m['tipo'])==('humano','comando') and m['processada_em'] is not None
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='conversa.devolvida' AND actor='atendente' AND alvo=$1",conv)==1
            # Havia mensagem do cliente esperando: o bot é agendado na hora e responde; o #bot não vai no pacote.
            assert len(fila.agendados)==agendados+1
            await p.processar_duravel('alpha',conv)
            assert len(vistos)==1 and vistos[0]['message']=='ainda aí?' and '#bot' not in json.dumps(vistos[0]).lower()
            # Cliente escrevendo "#bot" é mensagem comum; texto que só contém #bot no meio não é comando.
            await webhook('texto',text='#bot',content='#bot')
            assert (await conn.fetchrow("SELECT origem,tipo FROM mensagens WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1",conv))['tipo']=='texto'
            await webhook('atendente_celular',text='vou ligar o #bot já',content='vou ligar o #bot já')
            assert (await pausa())['bot_pausado_ate'] is not None
            # Sem nada pendente, o comando só retoma, sem agendar.
            await p.processar_duravel('alpha',conv);agendados=len(fila.agendados)
            await webhook('atendente_celular',text='#bot',content='#bot')
            assert (await pausa())['bot_pausado_ate'] is None and len(fila.agendados)==agendados
    banco(cenario)


@pytest.mark.parametrize('duravel',[True,False])
def test_encerramento_com_desfecho_e_reabertura(duravel):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        roteiro=[]
        async def agente(http,repo,cofre,tenant,cfg,payload): return roteiro.pop(0)
        p.chamar=agente
        async with client:
            a,can=await criar(client);await ativo(client,'alpha',a,can,debounce_s=1,max_wait_s=1)
            n=0
            async def turno(texto,resultado):
                nonlocal n;n+=1;roteiro[:]=[resultado]
                conv=(await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':'u','texto':texto,'message_id':f'm{n}'})).json()['conversation_id']
                estado_ao_receber=await conn.fetchrow('SELECT encerrada_em,desfecho FROM conversas WHERE conversation_id=$1',conv)
                await (p.processar_duravel if duravel else p.processar)('alpha',conv)
                return conv,estado_ao_receber,await conn.fetchrow('SELECT encerrada_em,desfecho FROM conversas WHERE conversation_id=$1',conv)
            conv,_,c=await turno('quero um temaki',{'resposta':['Pedido anotado!'],'acao':'encerrar','desfecho':'pedido_feito'})
            assert c['encerrada_em'] is not None and c['desfecho']=='pedido_feito'
            item=[i for i in (await client.get('/admin/v1/tenants/alpha/conversas')).json()['itens'] if i['conversation_id']==conv][0]
            assert item['desfecho']=='pedido_feito' and item['encerrada_em'] and item['status']=='ativa'
            # Nova mensagem do cliente reabre na chegada; o turno seguinte segue normal.
            conv,ao_receber,c=await turno('esqueci a bebida',{'resposta':['Qual bebida?'],'acao':'nenhuma'})
            assert tuple(ao_receber)==(None,None) and tuple(c)==(None,None)
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='conversa.reaberta' AND alvo=$1",conv)==1
            # Encerrar sem desfecho também encerra.
            conv,_,c=await turno('era só isso',{'resposta':[],'acao':'encerrar'})
            assert c['encerrada_em'] is not None and c['desfecho'] is None
    banco(cenario)


def test_conversa_vazia_e_encerrada_pelo_job():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async def agente(http,repo,cofre,tenant,cfg,payload): return {'resposta':['Pode escrever?'],'acao':'nenhuma'}
        p.chamar=agente
        async with client:
            a,canal,webhook,conv=await cenario_uazapi(conn,client,app,encerrar_vazia_min=30)
            estado=lambda c=conv: conn.fetchrow('SELECT encerrada_em,desfecho FROM conversas WHERE conversation_id=$1',c)
            envelhecer=lambda c=conv,minutos=31: conn.execute("UPDATE mensagens SET criado_em=criado_em-make_interval(mins=>$2) WHERE conversation_id=$1",c,minutos)
            # Só um áudio que o gateway não lê; o bot pede para escrever e o cliente some.
            await webhook('audio_ptt');await p.processar_duravel('alpha',conv)
            assert await p.encerrar_vazias()==0 and (await estado())['encerrada_em'] is None     # ainda dentro do prazo
            await envelhecer(minutos=29);assert await p.encerrar_vazias()==0
            await envelhecer(minutos=2);assert await p.encerrar_vazias()==1
            assert (await estado())['desfecho']=='vazia' and await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='conversa.encerrada' AND alvo=$1",conv)==1
            assert await p.encerrar_vazias()==0     # não repete
            # Cliente volta com texto: reabre e deixa de ser candidata.
            await webhook('texto');assert tuple(await estado())==(None,None)
            await p.processar_duravel('alpha',conv);await envelhecer(minutos=40)
            assert await p.encerrar_vazias()==0
            # Não encerra: conversa com atendente, com mensagem pendente, pausada ou aguardando atendente.
            for nome,preparo in (('humano',"INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,criado_em) VALUES ('alpha',$1,'saida','humano','oi',now(),now()-interval '2 hours')"),
                                 ('pendente',None),('pausada',"UPDATE conversas SET bot_pausado_ate=now()+interval '1 hour' WHERE conversation_id=$1"),
                                 ('aguardando',"UPDATE conversas SET aguardando_atendente_desde=now(),aguardando_motivo='erro_agente' WHERE conversation_id=$1")):
                c2=(await webhook('imagem_sem_legenda',chatid=f'55119000{len(nome):05d}@s.whatsapp.net',sender_pn=''))['conversation_id']
                if nome!='pendente': await p.processar_duravel('alpha',c2)
                if preparo: await conn.execute(preparo,c2)
                await envelhecer(c2,60)
                assert await p.encerrar_vazias()==0 and (await estado(c2))['encerrada_em'] is None,nome
    banco(cenario)


def test_SEG07_contato_acima_do_limite_por_minuto():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async with client:
            a,can=await criar(client);await ativo(client,'alpha',a,can,max_msgs_por_minuto_contato=3)
            assert (await client.get(f'/admin/v1/tenants/alpha/agentes/{a}')).json()['configuracao_efetiva']['max_msgs_por_minuto_contato']==3
            n=0
            async def manda(usuario='u'):
                nonlocal n;n+=1
                r=await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':usuario,'texto':f'msg {n}','message_id':f'm{n}'})
                assert r.status_code==200,r.text;return r.json()
            for _ in range(3): assert not (await manda()).get('limitado')
            agendados=len(fila.agendados)
            # A 4ª em um minuto estoura: registra, alerta uma vez e para de agendar o agente.
            r=await manda();conv=r['conversation_id'];assert r['limitado'] and r['ignorado']
            assert (await manda())['limitado'] and len(fila.agendados)==agendados
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='contato.limite_excedido' AND alvo=$1",conv)==1
            c=await conn.fetchrow('SELECT limitado_ate,now() agora FROM conversas WHERE conversation_id=$1',conv)
            assert 4*60<(c['limitado_ate']-c['agora']).total_seconds()<=5*60
            # As mensagens ficam gravadas (no histórico), já marcadas como tratadas.
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND origem='cliente'",conv)==5
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND processada_em IS NULL",conv)==3
            # Outro contato não é afetado.
            assert not (await manda('outro')).get('limitado')
            # Passado o bloqueio (e o minuto), volta ao normal.
            await conn.execute("UPDATE conversas SET limitado_ate=now()-interval '1 second' WHERE conversation_id=$1",conv)
            await conn.execute("UPDATE mensagens SET criado_em=criado_em-interval '2 minutes' WHERE conversation_id=$1",conv)
            r=await manda();assert not r.get('limitado') and len(fila.agendados)==agendados+2
            for ruim in (0,601): assert (await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'max_msgs_por_minuto_contato':ruim})).status_code in (400,422)
    banco(cenario)


def test_LGPD04_telefone_mascarado_e_sem_conteudo_nos_logs(caplog):
    m=seguranca.mascarar
    assert m('GET /admin/v1/tenants/bykoji/conversas/bykoji%3Acan_49fce1f15a43f87f22739d60%3A5511942717046/mensagens')=='GET /admin/v1/tenants/bykoji/conversas/bykoji%3Acan_49fce1f15a43f87f22739d60%3A5511****7046/mensagens'
    assert m('conversa bykoji:can_1:5511942717046')=='conversa bykoji:can_1:5511****7046' and m('tel 1133334444')=='tel 1133****4444'
    # Números curtos (porta, status, ids pequenos) e trechos de identificadores ficam como estão.
    assert m('200 OK em 1234 ms porta 8000 can_49fce1f15a43f87f22739d60')=='200 OK em 1234 ms porta 8000 can_49fce1f15a43f87f22739d60'
    assert m('POST /webhooks/uazapi/can_1?s=SEGREDO123&x=1')=='POST /webhooks/uazapi/can_1?s=***&x=1'
    # O filtro vale para mensagem e argumentos de qualquer linha de log.
    registro=logging.LogRecord('uvicorn.access',logging.INFO,'',0,'%s - "%s %s"',('1.2.3.4','GET','/x/alpha:can:5511900000001/mensagens'),None)
    seguranca.FiltroSegredos().filter(registro);assert '5511900000001' not in registro.getMessage() and '5511****0001' in registro.getMessage()

    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async def agente(http,repo,cofre,tenant,cfg,payload): return {'resposta':['RESPOSTA-SECRETA-DO-BOT'],'acao':'nenhuma'}
        p.chamar=agente
        async with client:
            a,canal,webhook,conv=await cenario_uazapi(conn,client,app,max_msgs_por_minuto_contato=2)
            with caplog.at_level(logging.DEBUG):
                await webhook('texto',text='CONTEUDO-SECRETO-DO-CLIENTE',content='CONTEUDO-SECRETO-DO-CLIENTE')
                await p.processar_duravel('alpha',conv)
                await webhook('atendente_celular');await webhook('texto');await webhook('texto')   # pausa e estoura o limite: gera avisos
            texto='\n'.join(r.getMessage() for r in caplog.records if r.name.startswith('gateway'))
            assert 'limite de mensagens' in texto
            for proibido in ('CONTEUDO-SECRETO-DO-CLIENTE','RESPOSTA-SECRETA-DO-BOT','5511900000001','Cliente Teste'):
                assert proibido not in texto,proibido
    banco(cenario)
