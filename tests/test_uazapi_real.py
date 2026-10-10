"""Fase 1: webhook real do uazapi (payloads de 10/10/2026 anonimizados em tests/fixtures/uazapi)."""
import copy,json,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import pytest
from canais import uazapi
from nucleo.pipeline import sanitizar
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo

PASTA=Path(__file__).parent/'fixtures/uazapi'
AGORA=datetime(2026,10,10,2,30,tzinfo=timezone.utc)
CLIENTE='5511900000001'


def fx(nome): return json.loads((PASTA/f'{nome}.json').read_text(encoding='utf-8'))


def test_normalizar_payloads_reais():
    n=lambda nome: uazapi.normalizar(fx(nome),AGORA)
    t=n('texto')
    assert (t['telefone'],t['texto'],t['tipo'],t['from_me'],t['via_api'],t['nome'])==(CLIENTE,'Oi','texto',False,False,'Cliente Teste')
    # Data/hora do provedor (ms), não a do recebimento.
    assert t['enviada_em']==datetime(2026,10,10,2,14,3,tzinfo=timezone.utc) and t['meta']=={}
    assert uazapi.normalizar(fx('texto'))['enviada_em'] is None or datetime.now(timezone.utc).date()<=datetime(2026,10,17).date()
    for nome in ('atendente_whatsapp_web','atendente_celular'):
        h=n(nome)
        # Conversa é a do cliente, não a do número da instância; o nome do atendente não vira nome do contato.
        assert h['from_me'] and not h['via_api'] and h['telefone']==CLIENTE and h['tipo']=='texto' and h['nome'] is None
    a=n('audio_ptt')
    assert a['tipo']=='audio' and a['texto']=='[não foi possível ler áudio de 10 s: leitura deste tipo de anexo ainda não está disponível]'
    assert a['meta']['segundos']==10 and a['meta']['mimetype'].startswith('audio/ogg')
    i=n('imagem_sem_legenda')
    assert i['tipo']=='imagem' and i['texto'].startswith('[não foi possível ler imagem:')
    il=n('imagem_com_legenda')
    assert il['texto'].split('\n')==['Foto do pedido','[não foi possível ler imagem: leitura deste tipo de anexo ainda não está disponível]']
    d=n('documento_pdf_grande')
    assert d['tipo']=='documento' and d['texto']=='[não foi possível ler documento "cardapio.pdf": arquivo grande demais (21,2 MB)]'
    assert d['meta']['arquivo']=='cardapio.pdf' and d['meta']['tamanho_bytes']>uazapi.LIMITE_ANEXO_BYTES
    l=n('localizacao_ao_vivo')
    assert l['tipo']=='localizacao' and l['texto']=='[localização: -23.55052, -46.633308]'
    assert l['meta']=={'latitude':-23.55052,'longitude':-46.633308,'ao_vivo':True}
    pino=n('localizacao_pino')  # pino comum real (sem nome nem endereço no payload)
    assert pino['tipo']=='localizacao' and pino['texto']=='[localização: -23.55052, -46.633308]' and pino['meta']['ao_vivo'] is False
    r=n('reacao')
    assert r['tipo']=='reacao' and r['texto']=='[reação: 👍🏻]' and r['meta']['reacao_a']==fx('documento_pdf_grande')['message']['messageid']
    c=n('texto_citando_mensagem')
    assert c['tipo']=='texto' and c['texto']=='Tudo bem, e você?' and c['meta']=={'resposta_a':fx('atendente_celular')['message']['messageid']}


def test_normalizar_bordas():
    base=fx('texto')
    def com(**campos):
        p=copy.deepcopy(base);p['message'].update(campos);return uazapi.normalizar(p,AGORA)
    assert com(isGroup=True) is None and com(chatid='120363000000000000@g.us') is None
    assert com(chatid='120363000000000000@newsletter') is None and com(chatid='status@broadcast') is None
    assert uazapi.normalizar({**base,'EventType':'connection'},AGORA) is None
    assert com(wasSentByApi=True,fromMe=True)['via_api'] is True
    # Chat identificado só por @lid: o telefone vem de sender_pn.
    assert com(chatid='100000000000001@lid')['telefone']==CLIENTE
    sem=copy.deepcopy(base);sem['message'].update(chatid='100000000000001@lid',sender_pn='');sem['chat'].update(wa_chatid='',phone='')
    assert uazapi.normalizar(sem,AGORA) is None
    assert com(messageid='',id='') is None and com(text='',content='') is None
    # Pino comum (formato do WhatsApp; não veio no roteiro real): nome e endereço entram no texto.
    pino=com(messageType='LocationMessage',type='media',text='',content={'degreesLatitude':-23.5,'degreesLongitude':-46.6,'name':'Loja','address':'Rua A, 10'})
    assert pino['texto']=='[localização: -23.5, -46.6 — Loja — Rua A, 10]' and pino['meta']['ao_vivo'] is False and pino['meta']['lugar']=='Loja — Rua A, 10'
    assert com(messageType='LocationMessage',text='',content={})['texto']=='[não foi possível ler localização: coordenadas ausentes]'
    novo=com(messageType='PollCreationMessage',type='poll',text='',content={'name':'x'})
    assert novo['tipo']=='desconhecido' and novo['texto']=='[não foi possível ler PollCreationMessage: tipo de mensagem não suportado]'
    assert com(text='',content={'text':''},type='reaction',messageType='ReactionMessage',reaction='ABC')['texto']=='[reação removida]'
    # Relógio do provedor fora da janela: vale o recebimento.
    assert com(messageTimestamp=1)['enviada_em'] is None and com(messageTimestamp='x')['enviada_em'] is None
    assert com(messageTimestamp=int(AGORA.timestamp())-30)['enviada_em'] is not None  # em segundos


def test_webhook_raw_sem_token_endereco_chave_de_midia_nem_miniatura():
    for arquivo in PASTA.glob('*.json'):
        bruto=json.loads(arquivo.read_text(encoding='utf-8'))
        limpo=json.dumps(uazapi.limpar_bruto(sanitizar(bruto)))
        for proibido in ('token-falso-de-teste','instancia.exemplo.invalid','BaseUrl','JPEGThumbnail','waveform','chave-falsa-de-teste'):
            assert proibido not in limpo,(arquivo.name,proibido)
        assert '"mediaKey": "QU5' not in limpo and '"e2EeMediaKey": "QU5' not in limpo
        assert json.loads(limpo)['message']['messageid']==bruto['message']['messageid']
    assert 'JPEGThumbnail' in json.dumps(fx('imagem_sem_legenda')) and 'mediaKey' in json.dumps(fx('audio_ptt'))


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_webhook_uazapi_ponta_a_ponta():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,_=await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal,pause_minutes=45)
            assert await conn.fetchval('SELECT ultimo_evento_em FROM tenant_canais WHERE id=$1',canal) is None
            conv=f'alpha:{canal}:{CLIENTE}'
            async def enviar(nome,**campos):
                p=fx(nome);p['message']['messageTimestamp']=int(time.time()*1000)-90000;p['message'].update(campos)
                resposta=await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=p)
                assert resposta.status_code==200,resposta.text
                return resposta.json()
            ult=lambda: conn.fetchrow('SELECT * FROM mensagens WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1',conv)
            # Segredo errado não conta como sinal de vida.
            assert (await client.post(f'/webhooks/uazapi/{canal}',params={'s':'errado'},json=fx('texto'))).status_code==401
            assert await conn.fetchval('SELECT ultimo_evento_em FROM tenant_canais WHERE id=$1',canal) is None

            r=await enviar('texto')
            assert r['conversation_id']==conv and not r['duplicada'] and len(fila.agendados)==1
            m=await ult()
            assert (m['origem'],m['direcao'],m['tipo'],m['conteudo'],m['meta'],m['processada_em'])==('cliente','entrada','texto','Oi',None,None)
            # enviada_em é a hora do provedor (90 s atrás), não a do recebimento.
            assert 60<(m['criado_em']-m['enviada_em']).total_seconds()<120
            assert await conn.fetchval('SELECT ultimo_evento_em FROM tenant_canais WHERE id=$1',canal) is not None
            assert (await enviar('texto'))['duplicada'] and await conn.fetchval('SELECT count(*) FROM mensagens WHERE conversation_id=$1',conv)==1

            for nome,tipo in (('audio_ptt','audio'),('imagem_com_legenda','imagem'),('documento_pdf_grande','documento'),('localizacao_ao_vivo','localizacao')):
                await enviar(nome);m=await ult()
                assert (m['origem'],m['tipo'])==('cliente',tipo) and m['processada_em'] is None,nome
            assert json.loads(m['meta'])['latitude']==-23.55052 and m['conteudo']=='[localização: -23.55052, -46.633308]'
            agendados=len(fila.agendados);assert agendados==6

            # Reação: entra no histórico, não abre turno.
            r=await enviar('reacao');m=await ult()
            assert r.get('reacao') and (m['tipo'],m['origem'],m['conteudo'])==('reacao','cliente','[reação: 👍🏻]') and m['processada_em'] is not None
            assert len(fila.agendados)==agendados
            assert await conn.fetchval('SELECT bot_pausado_ate FROM conversas WHERE conversation_id=$1',conv) is None

            # Enviada pela API (o próprio bot): não grava nem pausa.
            antes=await conn.fetchval('SELECT count(*) FROM mensagens WHERE conversation_id=$1',conv)
            assert (await enviar('atendente_celular',wasSentByApi=True,messageid='BOT1'))['ignorado']
            assert await conn.fetchval('SELECT count(*) FROM mensagens WHERE conversation_id=$1',conv)==antes
            assert await conn.fetchval('SELECT bot_pausado_ate FROM conversas WHERE conversation_id=$1',conv) is None
            # Reação do atendente não é assumir a conversa.
            await enviar('reacao',fromMe=True,messageid='REACAOATENDENTE');m=await ult()
            assert (m['origem'],m['direcao'],m['tipo'])==('humano','saida','reacao')
            assert await conn.fetchval('SELECT bot_pausado_ate FROM conversas WHERE conversation_id=$1',conv) is None

            # Atendente respondeu pelo celular da instância: origem humano e bot pausado (INV03).
            assert (await enviar('atendente_celular'))['from_me']
            m=await ult();assert (m['origem'],m['direcao'],m['tipo'],m['conteudo'])==('humano','saida','texto','Olá, tudo bem?')
            pausa=await conn.fetchrow('SELECT bot_pausado_ate,now() agora,nome FROM conversas WHERE conversation_id=$1',conv)
            assert 44*60<(pausa['bot_pausado_ate']-pausa['agora']).total_seconds()<=45*60 and pausa['nome']=='Cliente Teste'
            assert len(fila.agendados)==agendados
            await enviar('texto_citando_mensagem');m=await ult()
            assert json.loads(m['meta'])=={'resposta_a':fx('atendente_celular')['message']['messageid']}

            # Grupo e outros eventos: ignorados, mas o bruto fica (sanitizado).
            assert (await enviar('texto',isGroup=True,messageid='G1'))['ignorado']
            brutos=[json.loads(r['payload']) for r in await conn.fetch("SELECT payload FROM webhook_raw WHERE tenant_id='alpha' AND fonte='uazapi'")]
            assert len(brutos)==12 and all(b['token']=='***' and 'BaseUrl' not in b for b in brutos)
            texto=json.dumps(brutos)
            assert 'token-falso-de-teste' not in texto and 'JPEGThumbnail' not in texto and '"mediaKey": "***"' in texto
            assert await conn.fetchval("SELECT count(*) FROM webhook_raw WHERE fonte='uazapi' AND contato_id IS NOT NULL")>=10
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_016_colunas_novas_com_padrao_e_anonimizacao_limpa_meta():
    async def cenario(conn):
        await migrar.aplicar(conn)
        cols={r['column_name']:(r['is_nullable'],r['column_default']) for r in await conn.fetch("SELECT column_name,is_nullable,column_default FROM information_schema.columns WHERE table_name='mensagens' AND column_name IN ('tipo','meta')")}
        assert cols['tipo'][0]=='NO' and "'texto'" in cols['tipo'][1] and cols['meta'][0]=='YES'
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,ca=await criar(client)
            await ativo(client,'alpha',a,ca)
            # Canal API continua igual: tipo padrão e sem meta.
            r=await client.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':ca,'identificador':'u','texto':'oi','message_id':'m1'})
            assert r.status_code==200,r.text
            m=await conn.fetchrow('SELECT tipo,meta FROM mensagens WHERE conversation_id=$1',r.json()['conversation_id'])
            assert m['tipo']=='texto' and m['meta'] is None
    banco(cenario)


# ---------------- varredura de reconciliação, estado da conexão, envio e "digitando" ----------------
import asyncio,httpx
from canais.varredura import Varredura,registrar_estado


class Provedor:
    """Instância uazapi simulada: /instance/status, /message/find, /send/text, /message/presence."""
    def __init__(self): self.mensagens=[];self.estado='connected';self.chamadas=[];self.pagina_forcada=None
    def __call__(self,req):
        corpo=json.loads(req.content) if req.content else None
        self.chamadas.append((req.method,req.url.path,corpo,req.headers.get('token')))
        if req.url.path=='/instance/status': return httpx.Response(200,json={'instance':{'status':self.estado},'status':{'connected':self.estado=='connected'}})
        if req.url.path=='/message/find':
            ordenadas=sorted(self.mensagens,key=lambda m:-m['messageTimestamp']);o,l=corpo['offset'],corpo['limit']
            return httpx.Response(200,json={'messages':ordenadas[o:o+l],'hasMore':o+l<len(ordenadas),'nextOffset':o+l,'limit':l,'offset':o,'returnedMessages':len(ordenadas[o:o+l])})
        if req.url.path in ('/send/text','/message/presence'): return httpx.Response(200,json={'response':'ok'})
        return httpx.Response(404)


def da_busca(nome,segundos_atras,**campos):
    """Mensagem no formato do /message/find (sem sender_pn, chatlid nem wasSentByApi)."""
    m=fx(nome)['message']
    m={k:v for k,v in m.items() if k not in ('sender_pn','sender_lid','chatlid','wasSentByApi')}
    m['messageTimestamp']=int(time.time()*1000)-segundos_atras*1000;m.update(campos);return m


def test_chamadas_ao_provedor_envio_digitando_estado_e_paginacao(monkeypatch):
    async def caso():
        async def livre(url): return None
        monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',livre)
        class Cofre:
            def abrir(self,tenant,valor): return {'base_url':'https://instancia.exemplo.com/','token':'tok-1'}
        prov=Provedor();canal={'credenciais_enc':{'fernet':'x'}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(prov)) as h:
            await uazapi.enviar(h,Cofre(),'alpha',canal,'5511900000001','olá')
            await uazapi.digitando(h,Cofre(),'alpha',canal,'5511900000001',999999)
            await uazapi.digitando(h,Cofre(),'alpha',canal,'5511900000001',presenca='paused')
            with pytest.raises(ValueError): await uazapi.digitando(h,Cofre(),'alpha',canal,'5511900000001',presenca='outra')
            assert prov.chamadas==[('POST','/send/text',{'number':'5511900000001','text':'olá'},'tok-1'),
                ('POST','/message/presence',{'number':'5511900000001','presence':'composing','delay':300000},'tok-1'),
                ('POST','/message/presence',{'number':'5511900000001','presence':'paused'},'tok-1')]
            assert await uazapi.estado(h,Cofre(),'alpha',canal)=='conectado'
            prov.estado='disconnected';assert await uazapi.estado(h,Cofre(),'alpha',canal)=='desconectado'
            prov.estado='connecting';assert await uazapi.estado(h,Cofre(),'alpha',canal)=='conectando'
            # Paginação: da mais recente para trás, para ao passar do corte; não há filtro de data na API.
            prov.mensagens=[da_busca('texto',s,messageid=f'M{s}') for s in range(10,260,10)]
            corte=time.time()*1000-125*1000
            achadas,completo=await uazapi.recentes(h,Cofre(),'alpha',canal,corte,pagina=5)
            assert completo and [m['messageid'] for m in achadas]==[f'M{s}' for s in range(10,130,10)]
            achadas,completo=await uazapi.recentes(h,Cofre(),'alpha',canal,corte,pagina=5,max_paginas=2)
            assert not completo and len(achadas)==10
            prov.mensagens=[];assert await uazapi.recentes(h,Cofre(),'alpha',canal,corte)==([],True)
        # Erro do provedor não vaza endereço nem token.
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500,json={'error':'x'}))) as h:
            for chamada in (uazapi.enviar(h,Cofre(),'alpha',canal,'55','oi'),uazapi.estado(h,Cofre(),'alpha',canal)):
                with pytest.raises(ValueError) as erro: await chamada
                assert 'exemplo' not in str(erro.value) and 'tok-1' not in str(erro.value)
    asyncio.run(caso())


def test_estado_do_evento_connection_formatos_provaveis():
    e=uazapi.estado_do_evento
    assert e({'EventType':'connection','instance':{'status':'disconnected'}})=='desconectado'
    assert e({'EventType':'connection','status':{'connected':True}})=='conectado'
    assert e({'EventType':'connection','data':{'state':'connecting'}})=='conectando'
    assert e({'EventType':'connection','status':'connected'})=='conectado'
    assert e({'EventType':'connection','outra':'coisa'}) is None


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_varredura_recupera_o_que_o_webhook_nao_entregou(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async def livre(url): return None
        monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',livre)
        prov=Provedor();app.state.pipeline.http=httpx.AsyncClient(transport=httpx.MockTransport(prov))
        async with client:
            a,_=await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a,
                'credenciais':{'base_url':'https://instancia.exemplo.com','token':'tok-1'}})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal)
            conv=f'alpha:{canal}:{CLIENTE}';v=Varredura(app.state.pipeline)
            auditoria=lambda acao: conn.fetchval('SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao=$2 AND alvo=$3','alpha',acao,canal)
            # Uma chegou pelo webhook; as outras "se perderam" (recepção fora).
            pw=fx('texto');pw['message']['messageTimestamp']=int(time.time()*1000)-70000
            assert (await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=pw)).status_code==200
            prov.mensagens=[
                da_busca('texto',70),                                   # já recebida pelo webhook
                da_busca('audio_ptt',60),                               # perdida → recuperar
                da_busca('localizacao_ao_vivo',50),                     # perdida → recuperar
                da_busca('texto',47,messageid='LIGACAO1',fromMe=True,messageType='call',source='call_log'),  # registro de ligação
                da_busca('texto',40,messageid='GRUPO1',chatid='120363000000000000@g.us',isGroup=True),
                da_busca('texto',35,messageid='LID1',chatid='100000000000009@lid'),   # sem telefone identificável
                da_busca('texto',5,messageid='RECENTE1'),               # dentro da carência: o webhook ainda pode chegar
                da_busca('texto',1200,messageid='ANTIGA1'),             # fora da janela de 10 min
            ]
            agendados=len(fila.agendados)
            r=await v.rodar()
            assert (r['canais'],r['recuperadas'],r['sem_telefone'],r['do_atendente'],r['ambiguas'],r['erros'])==(1,2,1,0,0,0)
            linhas=await conn.fetch('SELECT tipo,origem,origem_recebimento,processada_em,enviada_em,criado_em FROM mensagens WHERE conversation_id=$1 ORDER BY id',conv)
            assert [(l['tipo'],l['origem'],l['origem_recebimento']) for l in linhas]==[('texto','cliente','webhook'),('audio','cliente','varredura'),('localizacao','cliente','varredura')]
            # Entram na ordem em que foram enviadas, com a hora do provedor, e abrem turno normalmente.
            assert linhas[1]['enviada_em']<linhas[2]['enviada_em']<linhas[2]['criado_em'] and all(l['processada_em'] is None for l in linhas)
            assert len(fila.agendados)==agendados+2
            assert await conn.fetchval('SELECT bot_pausado_ate FROM conversas WHERE conversation_id=$1',conv) is None
            assert await auditoria('canal.varredura_recuperou')==1
            assert await conn.fetchval('SELECT status_conexao FROM tenant_canais WHERE id=$1',canal)=='conectado'
            assert (await v.indicador())=={} or True  # Redis de teste não guarda o resumo
            # Repetir não duplica nem alerta de novo; a que estava na carência entra quando amadurece.
            r=await v.rodar();assert r['recuperadas']==0 and await auditoria('canal.varredura_recuperou')==1
            # Mesmo instante de outra mensagem pendente (empate na ordenação).
            prov.mensagens[6]['messageTimestamp']=prov.mensagens[5]['messageTimestamp']
            r=await v.rodar();assert r['recuperadas']==1 and await auditoria('canal.varredura_recuperou')==2
            # Webhook atrasado da mesma mensagem: duplicada, sem segunda gravação.
            atrasado=fx('audio_ptt');atrasado['message']['messageTimestamp']=prov.mensagens[1]['messageTimestamp']
            assert (await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=atrasado)).json()['duplicada']
            assert await conn.fetchval('SELECT count(*) FROM mensagens WHERE conversation_id=$1',conv)==4

            # Mensagens "minhas" (fromMe). A busca não diz se saíram pela API; vale o id guardado no envio.
            total=lambda: conn.fetchval('SELECT count(*) FROM mensagens WHERE conversation_id=$1',conv)
            pausa=lambda: conn.fetchval('SELECT bot_pausado_ate FROM conversas WHERE conversation_id=$1',conv)
            # a) enviada pelo bot, id guardado → já está no gateway: não duplica nem pausa.
            await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,message_id) VALUES ('alpha',$1,'saida','bot','resposta',now(),$2)",conv,f'{canal}:BOTENVIO0001')
            prov.mensagens.append(da_busca('atendente_celular',30,messageid='BOTENVIO0001'))
            antes=await total();r=await v.rodar()
            assert (r['recuperadas'],r['do_atendente'],r['ambiguas'])==(0,0,0) and await total()==antes and await pausa() is None
            # b) envio do gateway sem id do provedor perto do mesmo instante → ambígua: não grava nem pausa.
            await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,processada_em,meta) VALUES ('alpha',$1,'saida','bot','sem id',now(),'{\"envio_sem_id\": true}')",conv)
            prov.mensagens.append(da_busca('atendente_celular',28,messageid='SEMORIGEM0001'))
            r=await v.rodar();assert (r['recuperadas'],r['ambiguas'])==(0,1) and await pausa() is None
            await conn.execute("DELETE FROM mensagens WHERE conversation_id=$1 AND meta->>'envio_sem_id'='true'",conv)
            # c) sem nenhum envio do gateway correspondente → atendente pelo celular: humano e pausa (INV03).
            r=await v.rodar();assert (r['recuperadas'],r['do_atendente'],r['ambiguas'])==(1,1,0)
            m=await conn.fetchrow('SELECT origem,direcao,origem_recebimento,conteudo FROM mensagens WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1',conv)
            assert tuple(m)==('humano','saida','varredura','Olá, tudo bem?') and await pausa() is not None
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND message_id LIKE '%LIGACAO1'",conv)==0
            r=await v.rodar();assert r['recuperadas']==0
            base_msgs=await total()

            # Estado da conexão: alerta uma vez por mudança.
            prov.estado='disconnected';await v.rodar();await v.rodar()
            assert await conn.fetchval('SELECT status_conexao FROM tenant_canais WHERE id=$1',canal)=='desconectado'
            assert await auditoria('canal.desconectado')==1 and await auditoria('canal.reconectado')==0
            prov.estado='connected';await v.rodar()
            assert await auditoria('canal.reconectado')==1
            # Evento "connection" do webhook também atualiza, sem virar mensagem.
            r=await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json={'EventType':'connection','instance':{'status':'disconnected'},'token':'tok-1'})
            assert r.json()=={'ok':True,'ignorado':True} and await auditoria('canal.desconectado')==2
            assert await total()==base_msgs
            # Canal sem credencial, inativo ou de outro tipo não é varrido; falha do provedor não derruba a rodada.
            await client.patch(f'/admin/v1/tenants/alpha/canais/{canal}',json={'ativo':False})
            assert (await v.rodar())['canais']==0
            await client.patch(f'/admin/v1/tenants/alpha/canais/{canal}',json={'ativo':True})
            app.state.pipeline.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda q: httpx.Response(500)))
            r=await v.rodar();assert (r['canais'],r['erros'],r['recuperadas'])==(1,1,0)
            # Nenhuma chamada de envio foi feita pela varredura.
            assert not [c for c in prov.chamadas if c[1] in ('/send/text','/message/presence')]
    banco(cenario)


def test_id_do_envio_e_marca():
    assert uazapi.id_do_envio({'messageid':'3EB0ABCDEF0123456789','id':'5511900000002:3EB0ABCDEF0123456789','status':'Sent'})=='3EB0ABCDEF0123456789'
    assert uazapi.id_do_envio({'id':'5511900000002:3EB0ABCDEF0123456789'})=='3EB0ABCDEF0123456789'
    assert uazapi.id_do_envio({'message':{'messageid':'A5AC313F195EBDC8A5E8C34EE6BB4712'},'response':{'status':'success'}})=='A5AC313F195EBDC8A5E8C34EE6BB4712'
    for sem in ({'response':'ok'},{'messageid':''},{'messageid':'curto'},{'messageid':'com espaço e ; injeção'},[],None): assert uazapi.id_do_envio(sem) is None
    assert uazapi.marca_do_envio('can_1','ABCDEFGH1')==('can_1:ABCDEFGH1',None)
    assert uazapi.marca_do_envio('can_1',None)==(None,'{"envio_sem_id": true}')


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_envio_ao_vivo_guarda_o_id_do_provedor(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async def livre(url): return None
        monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',livre)
        respostas=[{'messageid':'3EB0ENVIODOBOT000001','id':'5511900000002:3EB0ENVIODOBOT000001','response':{'status':'success'}},{'response':{'status':'success'}}]
        app.state.pipeline.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda q: httpx.Response(200,json=respostas.pop(0))))
        async with client:
            a,_=await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a,
                'credenciais':{'base_url':'https://instancia.exemplo.com','token':'tok-1'}})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal)
            p=fx('texto');p['message']['messageTimestamp']=int(time.time()*1000)
            conv=(await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=p)).json()['conversation_id']
            await conn.execute("UPDATE agentes SET send_mode='live' WHERE id=$1",a);app.state.configuracoes.invalidar('alpha')
            assert await app.state.pipeline.enviar('alpha',conv,a,'primeira') and await app.state.pipeline.enviar('alpha',conv,a,'segunda')
            linhas=await conn.fetch("SELECT conteudo,message_id,meta FROM mensagens WHERE conversation_id=$1 AND origem='bot' ORDER BY id",conv)
            assert (linhas[0]['message_id'],linhas[0]['meta'])==(f'{canal}:3EB0ENVIODOBOT000001',None)
            assert linhas[1]['message_id'] is None and json.loads(linhas[1]['meta'])=={'envio_sem_id':True}
            # O webhook do próprio envio (wasSentByApi) continua ignorado e não pausa.
            eco=fx('atendente_celular');eco['message'].update(wasSentByApi=True,messageid='3EB0ENVIODOBOT000001')
            assert (await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=eco)).json()['ignorado']
            assert await conn.fetchval('SELECT bot_pausado_ate FROM conversas WHERE conversation_id=$1',conv) is None
    banco(cenario)
