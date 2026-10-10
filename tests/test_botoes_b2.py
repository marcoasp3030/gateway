"""UX1, bloco B2: botões de resposta do agente — validação, envio (botão ou texto numerado), histórico e métricas."""
import json,time,uuid
from urllib.parse import urlsplit,parse_qs
import httpx,pytest
from agentes import skyone
from canais import uazapi
from nucleo import botoes as B
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_skyone_adaptador import preparar_agente
from test_uazapi_real import fx

O=lambda *pares: {'opcoes':[{'id':i,'titulo':t} for i,t in pares]}
SIM_NAO=O(('confirmar_pedido','Confirmar pedido'),('alterar','Alterar'))


def test_campo_botoes_do_retorno_so_o_que_e_do_contrato():
    r=lambda v,acao='nenhuma',textos=('Posso confirmar?',): B.do_retorno(v,acao,list(textos))
    assert r(SIM_NAO)==SIM_NAO
    # Ausente ou nulo = sem botões, sem erro. Sem balão ou com ação que não comporta escolha: descartado.
    assert r(None) is None and r({'opcoes':[]}) is None and r(SIM_NAO,textos=()) is None
    for acao in ('silencio','transferir_humano','encerrar'): assert r(SIM_NAO,acao) is None
    assert r(SIM_NAO,'pedido_fechado')==SIM_NAO
    # Opção com id vazio é descartada; campos a mais não passam.
    assert r({'opcoes':[{'id':'','titulo':'Sem id'},{'id':None,'titulo':'Nulo'},{'titulo':'Ausente'},{'id':' sim ','titulo':' Sim ','url':'https://x','cor':'azul'}],'extra':1})==O(('sim','Sim'))
    # Formato errado ou mais de 10: marcado como inválido (vira texto ou só o balão), nunca erro.
    for ruim in ('Sim|Não',['sim'],{'opcoes':'sim'},{'botoes':[]},{'opcoes':[{'id':'a','titulo':5}]},{'opcoes':['sim']}):
        assert r(ruim)['invalido'] is True,ruim
    assert r(O(*[(f'o{i}',f'Opção {i}') for i in range(11)]))=={'opcoes':[],'invalido':True}
    assert len(r(O(*[(f'o{i}',f'Opção {i}') for i in range(10)]))['opcoes'])==10


def test_tabela_3_1_forma_e_motivo():
    p=lambda b,canal='uazapi',ligado=True: B.planejar(b,canal,ligado)
    assert p(None)==(None,None,[]) and p(SIM_NAO)==('botao',None,SIM_NAO['opcoes'])
    assert p(O(('a','Um')))[0]=='botao' and p(O(('a','Um'),('b','Dois'),('c','Três')))[0]=='botao'
    # 4 a 10 → texto (lista só no B3); título com mais de 20 → texto com o título inteiro.
    quatro=O(('a','Um'),('b','Dois'),('c','Três'),('d','Quatro'))
    assert p(quatro)==('texto','acima_do_limite',quatro['opcoes'])
    assert p(O(('a','x'*20)))[0]=='botao'
    longo=O(('a','Quero falar com um atendente'),('b','Não'))
    assert p(longo)==('texto','titulo_longo',longo['opcoes'])
    # Inválidos: id fora do padrão, id repetido, título vazio, repetido (sem acento/maiúscula), com "|" ou quebra de linha.
    for ruim,titulos in ((O(('Confirmar!','Confirmar'),('b','Não')),['Confirmar','Não']),(O(('a','Sim'),('a','Não')),['Sim','Não']),
                         (O(('a',''),('b','Não')),['Não']),(O(('a','Não'),('b','NAO')),['Não']),(O(('a','Sim|x'),('b','Não')),['Sim/x','Não']),
                         (O(('a','Sim\nagora'),('b','Não')),['Sim agora','Não']),(O(('a'*41,'Sim')),['Sim'])):
        forma,motivo,ops=p(ruim);assert (forma,motivo,[o['titulo'] for o in ops])==('texto','invalido',titulos),ruim
    assert p({'opcoes':[],'invalido':True})==(None,'invalido',[]) and p(O(('a','  ')))==(None,'invalido',[])
    # Desligado ou canal sem botão → texto.
    assert p(SIM_NAO,ligado=False)[:2]==('texto','desligado')
    for canal in ('api','site','email','meta'): assert p(SIM_NAO,canal)[:2]==('texto','canal_sem_botao')
    # Inválido e limite vêm antes de desligado/canal (o motivo mais específico).
    assert p(quatro,'api',False)[1]=='acima_do_limite'
    assert B.numerado('Posso confirmar?',SIM_NAO['opcoes'])=='Posso confirmar?\n\n1. Confirmar pedido\n2. Alterar' and B.numerado('oi',[])=='oi'


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_parser_do_retorno_aceita_botoes_e_sem_botoes_continua_igual(monkeypatch):
    async def validar(url,dominios): pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',validar)
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'Oi',cfg)
        base={'status':'success','tipo':'text','request_id':body['request_id'],'conversation_id':body['conversation_id']}
        async def retorno(**campos):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda q: httpx.Response(200,json={**base,**campos}))) as client:
                return await skyone.chamar(client,repo,cofre,'bykoji',cfg,body)
        assert await retorno(resposta=['ok'],acao='nenhuma')=={'resposta':['ok'],'acao':'nenhuma'}
        assert await retorno(resposta=['ok'],acao='nenhuma',botoes=None)=={'resposta':['ok'],'acao':'nenhuma'}
        assert await retorno(resposta=['Resumo','Posso confirmar?'],acao='nenhuma',botoes=SIM_NAO)=={'resposta':['Resumo','Posso confirmar?'],'acao':'nenhuma','botoes':SIM_NAO}
        assert 'botoes' not in await retorno(resposta='Vou transferir.',acao='transferir_humano',motivo='pediu',botoes=SIM_NAO)
        assert 'botoes' not in await retorno(resposta='',acao='silencio',motivo='só agradeceu',botoes=SIM_NAO)
        assert (await retorno(resposta=['ok'],acao='nenhuma',botoes='lixo'))['botoes']=={'opcoes':[],'invalido':True}
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_envio_botao_texto_numerado_recusa_do_provedor_e_incerto_sem_duplicar(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async def livre(url): return None
        monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',livre)
        chamadas=[];respostas=[]
        def provedor(q):
            chamadas.append((q.url.path,json.loads(q.content)))
            status,corpo=respostas.pop(0) if respostas else (200,{'messageid':'ID'+uuid.uuid4().hex[:16].upper()})
            return httpx.Response(status,json=corpo)
        app.state.pipeline.http=httpx.AsyncClient(transport=httpx.MockTransport(provedor))
        P=app.state.pipeline
        async with client:
            a,can_api=await criar(client);b,_=await criar(client,'bravo')
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a,'credenciais':{'base_url':'https://instancia.exemplo.com','token':'tok-1'}})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal)
            p=fx('texto');p['message']['messageTimestamp']=int(time.time()*1000)
            conv=(await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=p)).json()['conversation_id']
            async def modo(**campos):
                assert (await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json=campos)).status_code==200
            cfg=lambda: app.state.configuracoes.efetiva('alpha',a)
            # Padrão da plataforma: desligado, herdado.
            c=await cfg();assert (c['botoes_interativos'],c['origem_config']['botoes_interativos'])==('off','plataforma')
            assert (await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'botoes_interativos':'talvez'})).status_code==400
            async def parte(texto,escolhas,rid=None,n=1):
                rid=rid or uuid.uuid4()
                r=await P._enviar_parte('alpha',conv,await cfg(),rid,n,texto,escolhas)
                m=await conn.fetchrow('SELECT * FROM mensagens WHERE tenant_id=$1 AND turno_request_id=$2 AND parte=$3','alpha',rid,n)
                return r,m,(json.loads(m['meta']) if m['meta'] else None),rid
            NUMERADO='Posso confirmar?\n\n1. Confirmar pedido\n2. Alterar'

            # 1) Envio de teste: nada sai; o histórico já traz as opções numeradas e a forma que teria saído.
            r,m,meta,_=await parte('Posso confirmar?',SIM_NAO)
            assert (r,m['conteudo'],m['entrega'],chamadas)==('enviada',NUMERADO,'enviada',[]) and meta['botoes']=={'opcoes':SIM_NAO['opcoes'],'forma':'texto','motivo':'desligado'}
            await modo(send_mode='live')
            # 2) Envio real com o recurso desligado: texto numerado por /send/text.
            r,m,meta,_=await parte('Posso confirmar?',SIM_NAO)
            assert chamadas==[('/send/text',{'number':'5511900000001','text':NUMERADO})] and meta['botoes']['motivo']=='desligado' and m['message_id'].startswith(canal+':ID')
            # Balão sem botões continua exatamente como antes (regressão).
            chamadas.clear();r,m,meta,_=await parte('Só texto',None)
            assert chamadas==[('/send/text',{'number':'5511900000001','text':'Só texto'})] and m['conteudo']=='Só texto' and meta is None

            # 3) Ligado no agente: /send/menu com botões de resposta, só "título|id".
            await modo(botoes_interativos='on');assert (await cfg())['origem_config']['botoes_interativos']=='agente'
            chamadas.clear();r,m,meta,_=await parte('Posso confirmar?',SIM_NAO)
            assert chamadas==[('/send/menu',{'number':'5511900000001','type':'button','text':'Posso confirmar?','choices':['Confirmar pedido|confirmar_pedido','Alterar|alterar']})]
            assert (r,m['conteudo'],m['entrega'])==('enviada',NUMERADO,'enviada') and meta['botoes']=={'opcoes':SIM_NAO['opcoes'],'forma':'botao','motivo':None}
            origem=m['message_id'].split(':',1)[1]
            # O clique nessa mensagem chega ao agente como atual, com o id entre as opções gravadas.
            clique=fx('clique_botao');clique['message'].update(messageTimestamp=int(time.time()*1000),quoted=origem,vote='Confirmar pedido',buttonOrListid='confirmar_pedido')
            clique['message']['content'].update(selectedID='confirmar_pedido',selectedDisplayText='Confirmar pedido');clique['message']['content']['contextInfo']['stanzaID']=origem
            assert (await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=clique)).status_code==200
            itens=[i for i in await P._pendentes(conn,'alpha',conv) if i['conteudo']=='Confirmar pedido']
            pac=await skyone.pacote(P.repo.__class__(conn),'alpha',conv,itens[0]['id'],'Confirmar pedido',await cfg(),lote=itens)
            assert pac['ultima_interacao']=={'tipo':'botao','id':'confirmar_pedido','titulo':'Confirmar pedido','atual':True}
            assert NUMERADO in [h['content'] for h in pac['historico'] if h['role']=='assistant']

            # 4) Cada linha da tabela em envio real: tudo vira texto numerado, com o motivo.
            for escolhas,motivo,texto in ((O(('a','Um'),('b','Dois'),('c','Três'),('d','Quatro')),'acima_do_limite','Qual?\n\n1. Um\n2. Dois\n3. Três\n4. Quatro'),
                    (O(('a','Quero falar com um atendente'),('b','Não')),'titulo_longo','Qual?\n\n1. Quero falar com um atendente\n2. Não'),
                    (O(('Ruim!','Sim'),('b','Não')),'invalido','Qual?\n\n1. Sim\n2. Não'),({'opcoes':[],'invalido':True},'invalido','Qual?')):
                chamadas.clear();r,m,meta,_=await parte('Qual?',escolhas)
                assert chamadas==[('/send/text',{'number':'5511900000001','text':texto})] and m['conteudo']==texto and meta['botoes']['motivo']==motivo and r=='enviada',motivo

            # 5) 4xx no /send/menu: o mesmo balão sai por /send/text com as opções numeradas; nada falha.
            chamadas.clear();respostas[:]=[(400,{'error':'invalid menu type'})]
            r,m,meta,_=await parte('Posso confirmar?',SIM_NAO)
            assert [c[0] for c in chamadas]==['/send/menu','/send/text'] and chamadas[1][1]['text']==NUMERADO
            assert (r,m['entrega'])==('enviada','enviada') and meta['botoes']['forma']=='texto' and meta['botoes']['motivo']=='provedor_recusou'
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id='alpha' AND acao='botoes.fallback'")>=6

            # 6) Erro incerto (5xx): não reenvia em texto (poderia duplicar); entrega incerta, como qualquer envio.
            chamadas.clear();respostas[:]=[(502,{'error':'bad gateway'})];rid=uuid.uuid4()
            with pytest.raises(ValueError): await P._enviar_parte('alpha',conv,await cfg(),rid,1,'Posso confirmar?',SIM_NAO)
            m=await conn.fetchrow('SELECT * FROM mensagens WHERE turno_request_id=$1',rid)
            assert [c[0] for c in chamadas]==['/send/menu'] and m['entrega']=='incerta' and json.loads(m['meta'])['botoes']['motivo']=='provedor_incerto'
            # Nova tentativa do mesmo turno não envia de novo.
            assert await P._enviar_parte('alpha',conv,await cfg(),rid,1,'Posso confirmar?',SIM_NAO)=='incerta' and len(chamadas)==1
            await conn.execute("UPDATE conversas SET aguardando_atendente_desde=NULL,aguardando_motivo=NULL WHERE conversation_id=$1",conv)
            # 408 também é incerto.
            chamadas.clear();respostas[:]=[(408,{})]
            with pytest.raises(ValueError): await P._enviar_parte('alpha',conv,await cfg(),uuid.uuid4(),1,'Posso confirmar?',SIM_NAO)
            assert [c[0] for c in chamadas]==['/send/menu']
            await conn.execute("UPDATE conversas SET aguardando_atendente_desde=NULL,aguardando_motivo=NULL WHERE conversation_id=$1",conv)

            # 7) Botões só no último balão do turno.
            rid=uuid.uuid4()
            await conn.execute("INSERT INTO turnos(tenant_id,conversation_id,agente_id,canal_id,request_id,n_mensagens,status,etapa,resposta) VALUES ('alpha',$1,$2,$3,$4,1,'ok','respondido',$5::jsonb)",
                conv,a,canal,rid,json.dumps({'resposta':['Resumo do pedido','Posso confirmar?'],'acao':'nenhuma','botoes':SIM_NAO}))
            chamadas.clear();turno=await conn.fetchrow('SELECT * FROM turnos WHERE request_id=$1',rid)
            assert await P._enviar_partes('alpha',conv,await cfg(),turno)==('ok',True)
            assert [(c[0],c[1]['text']) for c in chamadas]==[('/send/text','Resumo do pedido'),('/send/menu','Posso confirmar?')]

            # 8) Caminho sem fila durável: mesma regra.
            chamadas.clear();assert await P.enviar('alpha',conv,a,'Posso confirmar?',SIM_NAO) and chamadas[0][0]=='/send/menu'

            # 9) Canal sem botão (API), mesmo com o recurso ligado: texto numerado gravado, sem envio.
            await modo(send_mode='dry');conv_api=f'alpha:{can_api}:u1'
            assert (await client.patch(f'/admin/v1/tenants/alpha/canais/{can_api}',json={'ativo':True})).status_code==200
            await conn.execute("INSERT INTO conversas(conversation_id,canal,tenant_id,agente_id,canal_id,telefone) VALUES ($1,'api','alpha',$2,$3,'u1')",conv_api,a,can_api)
            chamadas.clear();rid=uuid.uuid4()
            assert await P._enviar_parte('alpha',conv_api,await cfg(),rid,1,'Posso confirmar?',SIM_NAO)=='enviada' and chamadas==[]
            m=await conn.fetchrow('SELECT conteudo,meta FROM mensagens WHERE turno_request_id=$1',rid)
            assert m['conteudo']==NUMERADO and json.loads(m['meta'])['botoes']['motivo']=='canal_sem_botao'

            # 10) Métricas por empresa e por agente; outra empresa não vê nada.
            mt=(await client.get('/admin/v1/tenants/alpha/metricas')).json()['botoes']
            assert mt['enviados']=={'botao':3,'lista':0} and mt['clicados']=={'atual':1,'antigo':0}
            assert mt['em_texto']['por_motivo']=={'desligado':2,'acima_do_limite':1,'titulo_longo':1,'invalido':2,'provedor_recusou':1,'provedor_incerto':2,'canal_sem_botao':1}
            assert mt['em_texto']['total']==10 and (await client.get('/admin/v1/tenants/alpha/metricas',params={'agente':a})).json()['botoes']==mt
            vazio={'enviados':{'botao':0,'lista':0},'em_texto':{'total':0,'por_motivo':{}},'clicados':{'atual':0,'antigo':0}}
            assert (await client.get('/admin/v1/tenants/bravo/metricas')).json()['botoes']==vazio
            # Herança: ligado na empresa vale para o agente que não define; o agente pode desligar.
            await modo(botoes_interativos=None)
            assert (await client.put('/admin/v1/tenants/alpha/config',json={'botoes_interativos':'on'})).status_code==200
            c=await cfg();assert (c['botoes_interativos'],c['origem_config']['botoes_interativos'])==('on','tenant')
            await modo(botoes_interativos='off');assert (await cfg())['botoes_interativos']=='off'
            assert (await app.state.configuracoes.efetiva('bravo',b))['botoes_interativos']=='off'
    banco(cenario)
