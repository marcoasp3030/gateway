"""UX1, bloco B3: lista — validação, envio (lista ou texto numerado), descrição das unidades pelo gateway e leitura do clique."""
import json,time,uuid
from datetime import datetime
from urllib.parse import urlsplit,parse_qs
from zoneinfo import ZoneInfo
import httpx,pytest
from agentes import skyone
from canais import uazapi
from nucleo import botoes as B,horarios as H
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_skyone_adaptador import preparar_agente
from test_uazapi_real import fx

O=lambda *trios: {'opcoes':[{'id':t[0],'titulo':t[1],**({'descricao':t[2]} if len(t)>2 else {})} for t in trios]}
QUATRO=O(('a','Um'),('b','Dois'),('c','Três'),('d','Quatro'))


def test_contrato_e_tabela_da_lista():
    r=lambda v: B.do_retorno(v,'nenhuma',['Qual?'])
    p=lambda b,canal='uazapi',ligado=True: B.planejar(b,canal,ligado)
    # O Retorno guarda descrição e título do botão da lista; sem eles, igual ao B2.
    com=r({'titulo_lista':' Ver  unidades ','opcoes':[{'id':'morumbi','titulo':'Morumbi','descricao':' Aberta  até 22h30 ','cor':'azul'},{'id':'b','titulo':'B','descricao':''},{'id':'c','titulo':'C','descricao':None}]})
    assert com=={'opcoes':[{'id':'morumbi','titulo':'Morumbi','descricao':'Aberta até 22h30'},{'id':'b','titulo':'B'},{'id':'c','titulo':'C'}],'titulo_lista':'Ver unidades'}
    assert r({'opcoes':[{'id':'a','titulo':'A','descricao':5}]})['invalido'] is True
    # 4 a 10 opções → lista; 1 a 3 sem descrição → botão (como antes); qualquer quantidade com descrição → lista.
    assert p(QUATRO)==('lista',None,QUATRO['opcoes']) and p(O(*[(f'o{i}',f'Opção {i}') for i in range(10)]))[0]=='lista'
    assert p(O(('a','Um'),('b','Dois')))[0]=='botao'
    duas=O(('a','Um','primeira'),('b','Dois'));assert p(duas)==('lista',None,duas['opcoes'])
    # Limites da lista: título até 24 (botão segue 20), descrição até 72.
    assert p(O(('a','x'*24),('b','B'),('c','C'),('d','D')))[0]=='lista' and p(O(('a','x'*25),('b','B'),('c','C'),('d','D')))[:2]==('texto','titulo_longo')
    assert p(O(('a','x'*21),('b','B')))[:2]==('texto','titulo_longo')
    assert p(O(('a','A','d'*72),('b','B')))[0]=='lista' and p(O(('a','A','d'*73),('b','B')))[:2]==('texto','descricao_longa')
    # Planos B: desligado, canal sem lista, inválido (id repetido), 11 opções.
    assert p(QUATRO,ligado=False)[:2]==('texto','desligado') and p(QUATRO,'api')[:2]==('texto','canal_sem_botao')
    assert p(O(('a','Um'),('a','Dois'),('c','Três'),('d','Quatro')))[:2]==('texto','invalido')
    assert r(O(*[(f'o{i}',f'Opção {i}') for i in range(11)]))=={'opcoes':[],'invalido':True}
    # "|" na descrição não quebra o formato do provedor.
    assert p(O(('a','A','x|y'),('b','B')))[2][0]['descricao']=='x/y'
    # Texto numerado e histórico levam a descrição.
    assert B.numerado('Qual unidade?',duas['opcoes'])=='Qual unidade?\n\n1. Um — primeira\n2. Dois'
    # Botão que abre a lista: o do agente se couber em 20; senão o padrão.
    t=B.titulo_da_lista
    assert t({'titulo_lista':'Ver unidades'})=='Ver unidades' and t({})=='Ver opções' and t(None)=='Ver opções' and t({'titulo_lista':'x'*21})=='Ver opções' and t({'titulo_lista':'a|b'})=='Ver opções'
    # Descrição da unidade pelo gateway: substitui a do agente só nas opções cujo id é unidade.
    assert B.com_descricoes(O(('morumbi','Morumbi','sempre aberta'),('outra','Outra','do agente')),{'morumbi':'Fechada'})==O(('morumbi','Morumbi','Fechada'),('outra','Outra','do agente'))
    assert B.candidata_a_lista(QUATRO) and B.candidata_a_lista(duas) and not B.candidata_a_lista(O(('a','Um'),('b','Dois'))) and not B.candidata_a_lista(None)


def test_resumo_da_situacao_da_unidade():
    z='America/Sao_Paulo';agora=datetime(2026,10,10,21,0,tzinfo=ZoneInfo(z))
    s=lambda **k: {'aberta_agora':False,'motivo':None,'fecha_as':None,'proxima_abertura':None,**k}
    r=lambda **k: H.resumo(s(**k),z,agora)
    assert r(aberta_agora=True,fecha_as='2026-10-10T22:30:00-03:00')=='Aberta até 22h30' and r(aberta_agora=True,fecha_as='2026-10-11T01:00:00-03:00')=='Aberta até amanhã 1h'
    assert r(aberta_agora=True)=='Aberta' and r(aberta_agora=None)=='Horário a confirmar'
    assert r(proxima_abertura='2026-10-11T12:00:00-03:00')=='Fechada · abre amanhã 12h' and r(proxima_abertura='2026-10-13T11:30:00-03:00')=='Fechada · abre 13/10 11h30' and r()=='Fechada'
    assert r(motivo='pausa')=='Pedidos pausados' and r(motivo='pausa',proxima_abertura='2026-10-10T22:00:00-03:00')=='Pedidos pausados · volta 22h' and r(motivo='inativa')=='Indisponível'
    assert all(len(x)<=B.MAX_DESCRICAO for x in (r(motivo='pausa',proxima_abertura='2026-10-13T11:30:00-03:00'),r(proxima_abertura='2026-10-13T11:30:00-03:00')))


def test_clique_real_em_lista_vira_texto_com_o_titulo_da_opcao():
    p=fx('clique_lista');n=uazapi.normalizar(p)
    assert (n['tipo'],n['texto'],n['from_me'])==('texto','Pátio Guedala',False)
    assert n['meta']['interacao']=={'tipo':'lista','id':'patio_guedala','mensagem':p['message']['quoted']}
    # Sem os campos de atalho, lê do conteúdo (id da linha, título e mensagem citada).
    q=fx('clique_lista');q['message'].update(vote='',buttonOrListid='',quoted='')
    n=uazapi.normalizar(q);assert n['texto']=='Pátio Guedala' and n['meta']['interacao']=={'tipo':'lista','id':'patio_guedala','mensagem':p['message']['quoted']}
    # Toque feito no aparelho da própria instância (como no teste de 11/10): ignorado — não vira mensagem de atendente nem pausa o bot.
    q=fx('clique_lista');q['message'].update(fromMe=True,sender_pn=q['owner']+'@s.whatsapp.net');assert uazapi.normalizar(q) is None
    b=fx('clique_botao');b['message']['fromMe']=True;assert uazapi.normalizar(b) is None
    # O clique em botão continua igual.
    assert uazapi.normalizar(fx('clique_botao'))['meta']['interacao']['tipo']=='botao'


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_envio_de_lista_descricao_das_unidades_plano_b_e_incerto(monkeypatch):
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
        app.state.pipeline.http=httpx.AsyncClient(transport=httpx.MockTransport(provedor));P=app.state.pipeline
        async with client:
            a,_=await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a,'credenciais':{'base_url':'https://instancia.exemplo.com','token':'tok-1'}})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal)
            w=fx('texto');w['message']['messageTimestamp']=int(time.time()*1000)
            conv=(await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=w)).json()['conversation_id']
            async def modo(**campos): assert (await client.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json=campos)).status_code==200
            cfg=lambda: app.state.configuracoes.efetiva('alpha',a)
            async def parte(texto,escolhas,n=1):
                rid=uuid.uuid4();chamadas.clear()
                r=await P._enviar_parte('alpha',conv,await cfg(),rid,n,texto,escolhas)
                m=await conn.fetchrow('SELECT * FROM mensagens WHERE tenant_id=$1 AND turno_request_id=$2 AND parte=$3','alpha',rid,n)
                return r,m,(json.loads(m['meta']) if m['meta'] else None)
            NUM='Escolha:\n\n1. Um\n2. Dois\n3. Três\n4. Quatro'
            # 1) Desligado (padrão), envio real: 4 opções saem como texto numerado.
            await modo(send_mode='live')
            r,m,meta=await parte('Escolha:',QUATRO);assert chamadas==[('/send/text',{'number':'5511900000001','text':NUM})] and (meta['botoes']['forma'],meta['botoes']['motivo'])==('texto','desligado')
            # 2) Ligado: /send/menu com type "list", o botão padrão e uma seção.
            await modo(botoes_interativos='on')
            r,m,meta=await parte('Escolha:',QUATRO)
            assert chamadas==[('/send/menu',{'number':'5511900000001','type':'list','text':'Escolha:','listButton':'Ver opções','choices':['[Opções]','Um|a','Dois|b','Três|c','Quatro|d']})]
            assert (r,m['conteudo'],m['entrega'])==('enviada',NUM,'enviada') and meta['botoes']=={'opcoes':QUATRO['opcoes'],'forma':'lista','motivo':None} and m['message_id'].startswith(canal+':ID')
            # 3) Menos de 4 opções, mas com descrição do agente e título do botão: lista com "título|id|descrição".
            sabores={'titulo_lista':'Ver sabores','opcoes':[{'id':'salmao','titulo':'Salmão','descricao':'O mais pedido'},{'id':'atum','titulo':'Atum'}]}
            r,m,meta=await parte('Qual sabor?',sabores)
            assert chamadas[0][1]=={'number':'5511900000001','type':'list','text':'Qual sabor?','listButton':'Ver sabores','choices':['[Opções]','Salmão|salmao|O mais pedido','Atum|atum']}
            assert m['conteudo']=='Qual sabor?\n\n1. Salmão — O mais pedido\n2. Atum' and meta['botoes']['forma']=='lista'
            # 4) Opção cujo id é código de unidade da empresa: a descrição é a situação real, preenchida pelo gateway (a do agente é trocada).
            for nome,codigo in (('Morumbi','morumbi'),('Pátio Guedala','patio_guedala')): assert (await client.post('/admin/v1/tenants/alpha/unidades',json={'nome':nome,'codigo':codigo})).status_code==201
            esperado=(await H.resumos_das_unidades(conn,'alpha',['morumbi','patio_guedala','nao_e_unidade']));assert set(esperado)=={'morumbi','patio_guedala'} and all(esperado.values())
            unidades={'titulo_lista':'Ver unidades','opcoes':[{'id':'morumbi','titulo':'Morumbi','descricao':'Aberta 24 horas (inventado)'},{'id':'patio_guedala','titulo':'Pátio Guedala'},
                      {'id':'einstein','titulo':'Einstein','descricao':'do agente'},{'id':'tanto_faz','titulo':'Tanto faz'}]}
            r,m,meta=await parte('Em qual unidade?',unidades)
            assert chamadas[0][1]['choices']==['[Opções]',f'Morumbi|morumbi|{esperado["morumbi"]}',f'Pátio Guedala|patio_guedala|{esperado["patio_guedala"]}','Einstein|einstein|do agente','Tanto faz|tanto_faz']
            assert 'inventado' not in m['conteudo'] and f'1. Morumbi — {esperado["morumbi"]}' in m['conteudo'] and [o.get('descricao') for o in meta['botoes']['opcoes']]==[esperado['morumbi'],esperado['patio_guedala'],'do agente',None]
            # Com 3 opções de unidade e sem descrição do agente continuam botões (o gateway não transforma botão em lista).
            r,m,meta=await parte('Qual?',{'opcoes':[{'id':'morumbi','titulo':'Morumbi'},{'id':'patio_guedala','titulo':'Pátio Guedala'}]})
            assert chamadas[0][1]['type']=='button' and chamadas[0][1]['choices']==['Morumbi|morumbi','Pátio Guedala|patio_guedala']
            # A unidade de outra empresa não empresta descrição.
            await criar(client,'bravo');assert await H.resumos_das_unidades(conn,'bravo',['morumbi'])=={}
            # 5) Provedor recusa a lista (4xx): o mesmo balão sai em texto numerado, com as descrições; nada falha.
            respostas[:]=[(400,{'error':'list not supported'})]
            r,m,meta=await parte('Qual sabor?',sabores)
            assert [c[0] for c in chamadas]==['/send/menu','/send/text'] and chamadas[1][1]['text']=='Qual sabor?\n\n1. Salmão — O mais pedido\n2. Atum'
            assert r=='enviada' and (meta['botoes']['forma'],meta['botoes']['motivo'])==('texto','provedor_recusou')
            # 6) Falha incerta (5xx): não reenvia nem em texto; a parte fica incerta.
            respostas[:]=[(500,{'error':'x'})]
            with pytest.raises(Exception): await parte('Escolha:',QUATRO)
            m=await conn.fetchrow("SELECT entrega,meta FROM mensagens WHERE tenant_id='alpha' ORDER BY id DESC LIMIT 1")
            assert [c[0] for c in chamadas]==['/send/menu'] and m['entrega']=='incerta' and json.loads(m['meta'])['botoes']['motivo']=='provedor_incerto'
            await conn.execute("UPDATE conversas SET bot_pausado_ate=NULL,aguardando_atendente_desde=NULL,aguardando_motivo=NULL WHERE conversation_id=$1",conv)
            # 7) Título de 25 ou descrição de 73: texto numerado com tudo inteiro.
            r,m,meta=await parte('Escolha:',O(('a','x'*25),('b','B'),('c','C'),('d','D')));assert chamadas[0][0]=='/send/text' and meta['botoes']['motivo']=='titulo_longo'
            r,m,meta=await parte('Escolha:',O(('a','A','d'*73),('b','B')));assert chamadas[0][0]=='/send/text' and meta['botoes']['motivo']=='descricao_longa' and 'd'*73 in chamadas[0][1]['text']
            # 8) O cliente escolhe na lista: uma mensagem de texto com o título, ligada à lista enviada; conta nas métricas.
            await conn.execute("UPDATE mensagens SET message_id=$1 WHERE id=(SELECT id FROM mensagens WHERE tenant_id='alpha' AND meta->'botoes'->>'forma'='lista' AND conteudo LIKE 'Em qual unidade?%' LIMIT 1)",f'{canal}:LISTA01')
            k=fx('clique_lista');k['message'].update(quoted='LISTA01',messageTimestamp=int(time.time()*1000));k['message']['content']['contextInfo']['stanzaID']='LISTA01'
            assert (await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=k)).status_code==200
            e=await conn.fetchrow("SELECT conteudo,tipo,origem,meta FROM mensagens WHERE tenant_id='alpha' AND direcao='entrada' ORDER BY id DESC LIMIT 1")
            assert (e['conteudo'],e['tipo'],e['origem'])==('Pátio Guedala','texto','cliente') and json.loads(e['meta'])['interacao']=={'tipo':'lista','id':'patio_guedala','mensagem':'LISTA01'}
            met=(await client.get('/admin/v1/tenants/alpha/metricas')).json()['botoes']
            assert met['enviados']['lista']==3 and met['enviados']['botao']==1 and met['clicados']['atual']+met['clicados']['antigo']==1
            assert met['em_texto']['por_motivo']=={'desligado':1,'provedor_recusou':1,'provedor_incerto':1,'titulo_longo':1,'descricao_longa':1}
            # O mesmo toque feito no aparelho da instância não vira mensagem nem pausa o bot.
            k=fx('clique_lista');k['message'].update(fromMe=True,messageid='OUTRO0001',quoted='LISTA01',messageTimestamp=int(time.time()*1000))
            antes=await conn.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id='alpha'")
            assert (await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=k)).json().get('ignorado')
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id='alpha'")==antes and await conn.fetchval("SELECT bot_pausado_ate FROM conversas WHERE conversation_id=$1",conv) is None
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_pacote_ultima_interacao_de_lista():
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn);conv='bykoji:canal:x'
        opcoes=[{'id':'morumbi','titulo':'Morumbi','descricao':'Aberta até 22h30'},{'id':'patio_guedala','titulo':'Pátio Guedala'}]
        await conn.execute("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,message_id,meta,processada_em)
            VALUES ('bykoji',$1,'saida','bot','Em qual unidade?','canal:L1',$2::jsonb,now())""",conv,json.dumps({'botoes':{'opcoes':opcoes,'forma':'lista','motivo':None}}))
        async def cliente(texto,ident,tipo='lista'):
            return await conn.fetchrow("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,meta)
                VALUES ('bykoji',$1,'entrada','cliente',$2,$3::jsonb) RETURNING id,conteudo,criado_em,enviada_em,tipo,meta""",conv,texto,json.dumps({'interacao':{'tipo':tipo,'id':ident,'mensagem':'L1'}}))
        pacote=lambda m: skyone.pacote(repo,'bykoji',conv,m['id'],m['conteudo'],cfg,lote=[m])
        c=await cliente('Pátio Guedala','patio_guedala');p=await pacote(c)
        assert p['ultima_interacao']=={'tipo':'lista','id':'patio_guedala','titulo':'Pátio Guedala','atual':True}
        assert p['mensagens_do_lote'][0]['interacao']=={'tipo':'lista','id':'patio_guedala','atual':True} and p['message']=='Pátio Guedala'
        # Id que não estava na lista enviada, ou tipo desconhecido: texto puro.
        for m in (await cliente('Outra','einstein'),await cliente('Pátio Guedala','patio_guedala','carrossel')):
            p=await pacote(m);assert p['ultima_interacao'] is None and 'interacao' not in p['mensagens_do_lote'][0]
    banco(cenario)
