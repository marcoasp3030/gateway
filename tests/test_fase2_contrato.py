"""Fase 2: contrato com o Skyone (Apêndice A) — pacote, Retorno, INV04 e INV10."""
import json,uuid
from datetime import datetime,timezone,timedelta
import httpx,pytest
from agentes import skyone
from agentes.erros import SilencioSemMotivo
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_skyone_adaptador import preparar_agente

CAMPOS_DO_PACOTE={'request_id','session_id','conversation_id','tenant_id','agente_id','modelo_id','instrucoes_agente',
    'canal','modo_resposta','tarefa','agora','tempo_desde_ultima','retomada','message','mensagens_do_lote','historico',
    'cliente','unidades','contexto_modulos','ultima_interacao','acoes_ja_executadas','acoes_recentes','aviso_do_gateway','calendario','agente','perfil_versao','perfil_atendimento',
    'fora_do_horario','proxima_abertura','equipe_disponivel','retorno_equipe','recursos','atendimento'}


def test_tempo_em_texto_e_baloes():
    h=lambda **k: skyone.ha_quanto_tempo(timedelta(**k))
    assert [h(seconds=5),h(seconds=60),h(minutes=2),h(minutes=59,seconds=59),h(hours=1),h(hours=23),h(days=1),h(days=3)]==[
        'há menos de 1 minuto','há 1 minuto','há 2 minutos','há 59 minutos','há 1 hora','há 23 horas','há 1 dia','há 3 dias']
    assert h(seconds=-30)=='há menos de 1 minuto'
    b=skyone.baloes
    assert b(['oi','  ','tudo bem?\n'])==['oi','tudo bem?']                       # lista: um item = um balão
    assert b('Olá!\n\nTemos temaki.\n \nQuer pedir?')==['Olá!','Temos temaki.','Quer pedir?']  # texto: parágrafos
    assert b('uma linha\noutra linha')==['uma linha\noutra linha'] and b({'body':'do corpo'})==['do corpo']
    assert b('')==[] and b(None)==[] and b([])==[] and b('  \n\n ')==[]
    assert b(123) is None and b(['ok',5]) is None and b({'x':1})==[]
    muitos=b([str(i) for i in range(14)])
    assert len(muitos)==skyone.MAX_BALOES and muitos[-1]=='9\n\n10\n\n11\n\n12\n\n13'   # nada é descartado


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_INV10_pacote_do_apendice_a():
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        conv='bykoji:canal:x'
        # Primeiro contato: sem mensagem anterior.
        atual=await conn.fetchval("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('bykoji',$1,'entrada','cliente','oi') RETURNING id",conv)
        p=await skyone.pacote(repo,'bykoji',conv,atual,'oi',cfg)
        assert set(p)==CAMPOS_DO_PACOTE
        assert p['tempo_desde_ultima'] is None and p['retomada'] is False and p['tarefa']=='responder' and p['modo_resposta']=='sync'
        assert p['canal']['id']=='canal' and p['canal']['tipo']=='api' and set(p['canal'])=={'id','tipo','publico'}
        assert p['mensagens_do_lote']==[{'enviada_em':p['agora'],'tipo':'texto','texto':'oi'}] and p['agora'].endswith('-03:00')
        assert p['cliente']=={'nome':None,'nome_origem':None,'unidade_preferida':None,'resumo_anterior':None,'opt_out_follow_up':False}
        assert isinstance(p['unidades'],list) and isinstance(p['contexto_modulos'],dict)
        await conn.execute("DELETE FROM mensagens WHERE conversation_id=$1",conv)

        agora=datetime.now(timezone.utc)
        async def msg(direcao,texto,quando,tipo='texto'):
            return await conn.fetchrow("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,enviada_em,tipo) VALUES ('bykoji',$1,$2,$3,$4,$5,$6) RETURNING id,conteudo,criado_em,enviada_em,tipo",
                conv,direcao,'cliente' if direcao=='entrada' else 'bot',texto,quando,tipo)
        await msg('entrada','pedido antigo',agora-timedelta(days=3,minutes=5))
        await msg('saida','resposta antiga',agora-timedelta(days=3))
        ct='ct_'+uuid.uuid4().hex
        await conn.execute("INSERT INTO contatos(id,tenant_id,telefone,nome,resumo) VALUES ($1,'bykoji','5511900000001','Ana','Costuma pedir temaki.')",ct)
        await conn.execute("UPDATE conversas SET contato_id=$2,unidade_preferida='Morumbi',nome='Ana W' WHERE conversation_id=$1",conv,ct)
        lote=[await msg('entrada','oi',agora-timedelta(seconds=20)),await msg('entrada','[localização: -23.6, -46.7]',agora-timedelta(seconds=10),'localizacao')]
        p=await skyone.pacote(repo,'bykoji',conv,lote[0]['id'],'oi\n[localização: -23.6, -46.7]',cfg,lote=lote)
        assert set(p)==CAMPOS_DO_PACOTE
        # Tempo explícito: da última mensagem anterior (3 dias) até a primeira do lote.
        assert p['tempo_desde_ultima']=='há 2 dias'   # 3 dias menos 20 s
        assert p['retomada'] is True
        assert [(m['tipo'],m['texto']) for m in p['mensagens_do_lote']]==[('texto','oi'),('localizacao','[localização: -23.6, -46.7]')]
        assert all(m['enviada_em'].endswith('-03:00') for m in p['mensagens_do_lote']+p['historico'])
        assert p['mensagens_do_lote'][0]['enviada_em']<p['mensagens_do_lote'][1]['enviada_em']<=p['agora']
        assert [h['content'] for h in p['historico']]==['pedido antigo','resposta antiga']   # o lote não entra no histórico
        assert p['cliente']=={'nome':'Ana','nome_origem':'perfil','unidade_preferida':'Morumbi','resumo_anterior':'Costuma pedir temaki.','opt_out_follow_up':False}
        assert p['message']=='oi\n[localização: -23.6, -46.7]' and p['session_id']==p['request_id']
        # Conversa em andamento: minutos, sem retomada. Exatamente 12 h ainda não é retomada.
        await conn.execute("UPDATE mensagens SET enviada_em=$1 WHERE conteudo='resposta antiga'",agora-timedelta(minutes=2,seconds=20))
        p=await skyone.pacote(repo,'bykoji',conv,lote[0]['id'],'oi',cfg,lote=lote)
        assert p['tempo_desde_ultima']=='há 2 minutos' and p['retomada'] is False
        await conn.execute("UPDATE mensagens SET enviada_em=$1 WHERE conteudo='resposta antiga'",agora-timedelta(hours=11,minutes=59))
        assert (await skyone.pacote(repo,'bykoji',conv,lote[0]['id'],'oi',cfg,lote=lote))['retomada'] is False
        await conn.execute("UPDATE mensagens SET enviada_em=$1 WHERE conteudo='resposta antiga'",agora-timedelta(hours=12,seconds=25))
        p=await skyone.pacote(repo,'bykoji',conv,lote[0]['id'],'oi',cfg,lote=lote)
        assert p['tempo_desde_ultima']=='há 12 horas' and p['retomada'] is True
        # Nenhum segredo nem campo de credencial no corpo (SEG13).
        texto=json.dumps(p)
        assert 'credencial-fixture' not in texto and 'skyone_auth' not in texto and 'webhook_url' not in texto
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_retorno_do_apendice_a(monkeypatch):
    async def validar(url,dominios): pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',validar)
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'Oi',cfg)
        base={'status':'success','tipo':'text','request_id':body['request_id'],'conversation_id':body['conversation_id']}
        async def retorno(**campos):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda q: httpx.Response(200,json={**base,**campos}))) as client:
                return await skyone.chamar(client,repo,cofre,'bykoji',cfg,body)
        assert await retorno(resposta=['balão 1','balão 2'],acao='nenhuma')=={'resposta':['balão 1','balão 2'],'acao':'nenhuma'}
        assert (await retorno(resposta='Olá!\n\nQuer pedir?',acao='nenhuma'))['resposta']==['Olá!','Quer pedir?']
        assert (await retorno(resposta={'body':'formato validado'},acao='nenhuma'))['resposta']==['formato validado']
        assert await retorno(resposta='',acao='silencio',motivo=' cliente só agradeceu ')=={'resposta':[],'acao':'silencio','motivo':'cliente só agradeceu'}
        # Silêncio declarado ignora texto que venha junto: nada é enviado.
        assert (await retorno(resposta='não enviar',acao='silencio',motivo='👍'))['resposta']==[]
        r=await retorno(resposta=['Pedido anotado!'],acao='encerrar',desfecho='pedido_feito',uso={'tokens_entrada':10,'tokens_saida':3})
        assert r=={'resposta':['Pedido anotado!'],'acao':'encerrar','desfecho':'pedido_feito','uso':{'tokens_entrada':10,'tokens_saida':3}}
        assert await retorno(resposta=[],acao='encerrar',desfecho='sem_interesse')=={'resposta':[],'acao':'encerrar','desfecho':'sem_interesse'}
        assert await retorno(resposta='Vou chamar um atendente.',acao='transferir_humano',motivo='reclamação')=={'resposta':['Vou chamar um atendente.'],'acao':'transferir_humano','motivo':'reclamação'}
        assert (await retorno(acao='transferir_humano'))=={'resposta':[],'acao':'transferir_humano'}
        # desfecho fora do formato não é gravado; campos fora do contrato não passam.
        r=await retorno(resposta='ok',acao='nenhuma',desfecho='Texto Livre!',extra='x',motivo=5)
        assert r=={'resposta':['ok'],'acao':'nenhuma'}
        # INV04: silêncio sem motivo e resposta vazia sem silêncio.
        for campos in ({'resposta':'','acao':'silencio'},{'resposta':'','acao':'silencio','motivo':'  '},{'resposta':'','acao':'nenhuma'},
                       {'resposta':[],'acao':'nenhuma'},{'acao':'nenhuma'},{'resposta':['  '],'acao':'pedido_fechado'}):
            with pytest.raises(SilencioSemMotivo): await retorno(**campos)
        for campos in ({'resposta':'ok','acao':'inventada'},{'resposta':'ok'},{'resposta':7,'acao':'nenhuma'},{'resposta':['a',1],'acao':'nenhuma'}):
            with pytest.raises(skyone.FalhaAgente) as erro: await retorno(**campos)
            assert not isinstance(erro.value,SilencioSemMotivo)
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
@pytest.mark.parametrize('duravel',[True,False])
def test_INV04_turno_grava_acao_motivo_e_desfecho(duravel):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        p=app.state.pipeline;roteiro=[];chamadas=[]
        async def agente(http,repo,cofre,tenant,cfg,payload):
            chamadas.append(payload);passo=roteiro.pop(0)
            if isinstance(passo,Exception): raise passo
            return passo
        p.chamar=agente
        async with client:
            a,can=await criar(client)
            await ativo(client,'alpha',a,can,debounce_s=1,max_wait_s=1)
            n=0
            async def turno(usuario,*passos):
                nonlocal n;n+=1;roteiro[:]=passos;chamadas.clear()
                r=await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':usuario,'texto':'obrigado','message_id':f'm{n}'})
                conv=r.json()['conversation_id']
                assert await (p.processar_duravel if duravel else p.processar)('alpha',conv) in ('concluido',None)
                t=await conn.fetchrow('SELECT status,erro,acao,motivo_silencio,desfecho,etapa FROM turnos WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1',conv)
                c=await conn.fetchrow('SELECT aguardando_motivo,bot_pausado_ate FROM conversas WHERE conversation_id=$1',conv)
                bot=[r[0] for r in await conn.fetch("SELECT conteudo FROM mensagens WHERE conversation_id=$1 AND origem='bot' ORDER BY id",conv)]
                assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND direcao='entrada' AND processada_em IS NULL",conv)==0
                return t,c,bot
            # Silêncio com motivo: nada é enviado, motivo gravado, conversa segue com o bot.
            t,c,bot=await turno('u1',{'resposta':[],'acao':'silencio','motivo':'cliente só agradeceu'})
            assert (t['status'],t['acao'],t['motivo_silencio'],t['desfecho'],t['etapa'])==('ok','silencio','cliente só agradeceu',None,'concluido')
            assert bot==[] and c['aguardando_motivo'] is None and c['bot_pausado_ate'] is None and len(chamadas)==1
            # Lista de balões: uma mensagem por item, na ordem.
            t,c,bot=await turno('u2',{'resposta':['balão 1','balão 2'],'acao':'nenhuma'})
            assert bot==['balão 1','balão 2'] and (t['acao'],t['motivo_silencio'])==('nenhuma',None)
            # Encerrar com desfecho.
            t,c,bot=await turno('u3',{'resposta':['Até logo!'],'acao':'encerrar','desfecho':'pedido_feito'})
            assert bot==['Até logo!'] and (t['acao'],t['desfecho'],t['status'])==('encerrar','pedido_feito','ok')
            # Transferir: envia o texto, pausa o bot e marca aguardando atendente; o motivo não é "silêncio".
            t,c,bot=await turno('u4',{'resposta':['Vou chamar um atendente.'],'acao':'transferir_humano','motivo':'reclamação'})
            assert bot==['Vou chamar um atendente.'] and (t['acao'],t['motivo_silencio'],t['status'])==('transferir_humano',None,'ok')
            assert c['aguardando_motivo']=='transferido_pelo_agente' and c['bot_pausado_ate'] is not None
            # INV04: silêncio sem motivo → reconsulta uma vez; na segunda o agente responde.
            t,c,bot=await turno('u5',SilencioSemMotivo(),{'resposta':['De nada!'],'acao':'nenhuma'})
            assert len(chamadas)==2 and chamadas[0]['request_id']==chamadas[1]['request_id'] and bot==['De nada!']
            assert (t['status'],t['erro'],t['acao'])==('ok',None,'nenhuma') and c['aguardando_motivo'] is None
            # INV04: persistindo → nada é enviado (nem o fallback), conversa vai para atendente.
            t,c,bot=await turno('u6',SilencioSemMotivo(),SilencioSemMotivo())
            assert len(chamadas)==2 and bot==[]
            assert (t['status'],t['erro'],t['acao'],t['motivo_silencio'])==('erro','SILENCE_WITHOUT_REASON','transferir_humano',None)
            assert c['aguardando_motivo']=='silencio_sem_motivo' and c['bot_pausado_ate'] is not None
            # Outras falhas continuam com fallback e sem reconsulta.
            t,c,bot=await turno('u7',skyone.FalhaAgente('x'))
            assert len(chamadas)==1 and len(bot)==1 and (t['status'],t['erro'],c['aguardando_motivo'])==('erro','AGENT_ERROR','erro_agente')
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
@pytest.mark.parametrize('duravel',[True,False])
def test_debounce_recomeca_a_cada_lote(duravel):
    """A marca da primeira mensagem do lote sai quando o lote é lido; senão a mensagem seguinte
    da conversa seria despachada na hora, sem esperar o cliente terminar de digitar."""
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        p=app.state.pipeline
        async def agente(http,repo,cofre,tenant,cfg,payload): return {'resposta':['ok'],'acao':'nenhuma'}
        p.chamar=agente
        async with client:
            a,can=await criar(client)
            await ativo(client,'alpha',a,can,debounce_s=4,max_wait_s=12)
            r=await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':'u','texto':'oi','message_id':'m1'})
            conv=r.json()['conversation_id'];marca=p.prefixo+':first:'+p.chave('alpha',conv)
            fila.dados[marca]='1700000000'   # como o agendamento deixa no Redis
            await (p.processar_duravel if duravel else p.processar)('alpha',conv)
            assert marca not in fila.dados
    banco(cenario)
