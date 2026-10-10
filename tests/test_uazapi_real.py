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
