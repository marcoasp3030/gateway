import json
from datetime import datetime,timezone,timedelta
from cryptography.fernet import Fernet
import httpx
import pytest
from agentes import skyone
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from nucleo.credenciais import Cofre
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


async def preparar_agente(conn):
    await migrar.aplicar(conn)
    repo=Repositorio(conn);cofre=Cofre(Fernet.generate_key())
    await repo.alterar('bykoji','agentes','bykoji_atendimento',{
        'provedor':'skyone','agente_mode':'live','history_turns':3,'skyone_webhook_url':'https://skyone.example/fluxo',
        'skyone_auth_tipo':'bearer','skyone_auth_enc':cofre.cifrar('bykoji','credencial-fixture')})
    await repo.inserir('bykoji','tenant_canais','canal',{'nome':'C','tipo':'api','agente_id':'bykoji_atendimento'})
    await conn.execute("INSERT INTO conversas(conversation_id,canal,tenant_id,canal_id,agente_id) VALUES ('bykoji:canal:x','api','bykoji','canal','bykoji_atendimento')")
    cfg=await Configuracoes(repo).efetiva('bykoji','bykoji_atendimento')
    return repo,cofre,cfg


def test_INV17_historico_real_limitado_e_fuso():
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        for i in range(4):
            await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,enviada_em) VALUES ('bykoji','bykoji:canal:x',$1,'cliente',$2,$3)",
                'entrada' if i%2==0 else 'saida',str(i),datetime(2026,10,8,12,i,tzinfo=timezone.utc))
        await conn.execute("INSERT INTO conversas(conversation_id,canal,tenant_id,canal_id,agente_id) VALUES ('bykoji:canal:y','api','bykoji','canal','bykoji_atendimento')")
        await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('bykoji','bykoji:canal:y','entrada','cliente','nao incluir')")
        atual=await conn.fetchval("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('bykoji','bykoji:canal:x','entrada','cliente','atual') RETURNING id")
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',atual,'atual',cfg)
        assert [m['content'] for m in body['historico']]==['1','2','3']
        assert [m['role'] for m in body['historico']]==['assistant','user','assistant']
        assert all(m['enviada_em'].endswith('-03:00') for m in body['historico'])
        assert body['session_id']==body['request_id'] and 'token' not in body
        with pytest.raises(LookupError): await skyone.pacote(repo,'outro','bykoji:canal:x',atual,'x',cfg)
    banco(cenario)


@pytest.mark.parametrize('tipo',['bearer','cabecalho'])
def test_SEG03_SEG13_contrato_headers_timeout(monkeypatch,tipo):
    chamadas=[]
    async def validar(url,dominios): chamadas.append('dns')
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',validar)
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        await repo.alterar('bykoji','agentes',cfg['id'],{'skyone_auth_tipo':tipo,'skyone_auth_cabecalho':'X-Test-Key'})
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'Oi',cfg)
        def responder(req):
            assert chamadas==['dns']
            assert req.headers['Authorization' if tipo=='bearer' else 'X-Test-Key']==('Bearer ' if tipo=='bearer' else '')+'credencial-fixture'
            assert b'credencial-fixture' not in req.content
            assert req.extensions['timeout']['read']==90
            payload=json.loads(req.content)
            assert payload['message']=='Oi' and payload['session_id']==payload['request_id']
            return httpx.Response(200,json={'resposta':{'body':'Resposta'},'acao':'nenhuma','status':'success','tipo':'text',
                                          'request_id':payload['request_id'],'conversation_id':payload['conversation_id']})
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            assert (await skyone.chamar(client,repo,cofre,'bykoji',cfg,body))['resposta']==['Resposta']
    banco(cenario)


def test_SEG03_saida_recusada_redirect_e_erro_sem_segredos(monkeypatch):
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'Oi',cfg)
        async def recusar(url,dominios): raise skyone.seguranca.UrlRecusada('credencial-fixture')
        monkeypatch.setattr(skyone.seguranca,'validar_url_saida',recusar)
        def proibido(req): raise AssertionError('não deve enviar')
        async with httpx.AsyncClient(transport=httpx.MockTransport(proibido)) as client:
            with pytest.raises(skyone.FalhaAgente) as erro: await skyone.chamar(client,repo,cofre,'bykoji',cfg,body)
            assert 'credencial-fixture' not in str(erro.value)
        async def validar(url,dominios): pass
        monkeypatch.setattr(skyone.seguranca,'validar_url_saida',validar)
        for status in (302,401,500):
            vistos=[]
            def responder(req):
                vistos.append(req)
                return httpx.Response(status,headers={'Location':'http://127.0.0.1'},text='credencial-fixture')
            async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
                with pytest.raises(skyone.FalhaAgente) as erro: await skyone.chamar(client,repo,cofre,'bykoji',cfg,body)
                assert len(vistos)==1 and 'credencial-fixture' not in str(erro.value)
    banco(cenario)


def test_SEG13_live_sem_credencial_recusado_na_api():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            agente,_=await criar(client)
            url=f'/admin/v1/tenants/alpha/agentes/{agente}'
            live={'provedor':'skyone','agente_mode':'live','skyone_webhook_url':'https://skyone.example/fluxo'}
            assert (await client.patch(url,json=live)).status_code==400
            live.update(skyone_auth_tipo='bearer',skyone_auth='valor-cifrado-apenas')
            r=await client.patch(url,json=live)
            assert r.status_code==200,r.text
            assert 'valor-cifrado-apenas' not in r.text
            assert r.json()['skyone_auth_configurado'] is True
            # Configuração por tenant também não pode habilitar live sem credencial.
            outro,_=await criar(client,'bravo')
            assert (await client.patch(f'/admin/v1/tenants/bravo/agentes/{outro}',json={'agente_mode':None,'provedor':'skyone','skyone_webhook_url':'https://skyone.example/fluxo'})).status_code==200
            assert (await client.put('/admin/v1/tenants/bravo/config',json={'agente_mode':'live'})).status_code==400
    banco(cenario)


def test_timeout_e_eco_credencial_recusados(monkeypatch):
    async def validar(url,dominios): pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',validar)
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'Oi',cfg)
        def timeout(req): raise httpx.ReadTimeout('credencial-fixture',request=req)
        def eco(req): return httpx.Response(200,json={
            'resposta':'credencial-fixture','acao':'nenhuma','status':'success','tipo':'text',
            'request_id':body['request_id'],'conversation_id':body['conversation_id']})
        for responder in (timeout,eco):
            async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
                with pytest.raises(skyone.FalhaAgente) as erro:
                    await skyone.chamar(client,repo,cofre,'bykoji',cfg,body)
                assert 'credencial-fixture' not in str(erro.value)
    banco(cenario)
