import asyncio,importlib.util,json
from pathlib import Path
import httpx,pytest
spec=importlib.util.spec_from_file_location('canal_credencial',Path(__file__).resolve().parents[1]/'scripts/canal_credencial.py')
cc=importlib.util.module_from_spec(spec);spec.loader.exec_module(cc)


async def sem_rede(url):
    # Só o formato (regras de SSRF sem DNS), para o teste não depender de rede.
    cc.sys.modules['seguranca'].validar_formato_url(url)


def test_preparar_normaliza_e_recusa_endereco_inseguro_sem_repetir_o_valor():
    async def caso():
        ok=await cc.preparar(' https://instancia.exemplo.com/ ',' tok-123 ',sem_rede)
        assert ok=={'base_url':'https://instancia.exemplo.com','token':'tok-123'}
        ruins=['http://instancia.exemplo.com','https://usuario:senha@instancia.exemplo.com','https://127.0.0.1',
               'https://10.0.0.5','https://localhost','https://redis.internal','https://instancia.exemplo.com:8443',
               'https://instancia.exemplo.com/send/text','https://instancia.exemplo.com?x=1','https://169.254.169.254']
        for url in ruins:
            with pytest.raises(ValueError) as erro: await cc.preparar(url,'tok-123',sem_rede)
            assert 'tok-123' not in str(erro.value) and 'exemplo' not in str(erro.value) and '169.254' not in str(erro.value)
        for url,token in (('','tok'),('https://instancia.exemplo.com',''),('https://instancia.exemplo.com','tok com espaço'),
                          ('https://instancia.exemplo.com','tok\n'+'x')):
            with pytest.raises(ValueError): await cc.preparar(url,token,sem_rede)
    asyncio.run(caso())


def test_preparar_usa_a_validacao_completa_por_padrao_e_recusa_dns_interno(monkeypatch):
    async def caso():
        async def interno(host,porta,**kw): return [(2,1,6,'',('10.1.2.3',443))]
        monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',interno)
        with pytest.raises(ValueError) as erro: await cc.preparar('https://parece-publico.exemplo.com','tok-123')
        assert '10.1.2.3' not in str(erro.value) and 'exemplo' not in str(erro.value)
    asyncio.run(caso())


def test_gravar_so_em_canal_uazapi_e_envia_credenciais_no_corpo():
    async def caso():
        visto={}
        def responder(req):
            assert req.url.path=='/admin/v1/tenants/acme/canais/'+req.url.path.rsplit('/',1)[1] and not req.url.query
            canal=req.url.path.rsplit('/',1)[1]
            if canal=='can_nao': return httpx.Response(404,json={})
            if req.method=='GET': return httpx.Response(200,json={'id':canal,'tipo':'api' if canal=='can_api' else 'uazapi'})
            visto.update(json.loads(req.content));return httpx.Response(200,json={'id':canal,'credenciais_configurado':True})
        async with httpx.AsyncClient(base_url='http://teste',transport=httpx.MockTransport(responder)) as c:
            cred={'base_url':'https://instancia.exemplo.com','token':'tok-123'}
            assert await cc.gravar(c,'acme','can_ok',cred) is True and visto=={'credenciais':cred}
            for canal in ('can_api','can_nao'):
                with pytest.raises(ValueError) as erro: await cc.gravar(c,'acme',canal,cred)
                assert 'tok-123' not in str(erro.value)
    asyncio.run(caso())
