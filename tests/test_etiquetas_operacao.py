"""Etiquetas, busca na conversa, envio de arquivo e números da operação."""
import base64,json
import httpx,pytest
from canais import uazapi
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila

ANA={'X-Role':'funcionario','X-Actor':'ana@exemplo.test'}
BIA={'X-Role':'funcionario','X-Actor':'bia@exemplo.test'}
SUP={'X-Role':'supervisor','X-Actor':'sup@exemplo.test'}
E='/admin/v1/tenants/alpha/etiquetas'
PDF=base64.b64encode(b'%PDF-1.4 conteudo').decode()
pular=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


async def montar(conn,cliente='5511900000601'):
    await migrar.aplicar(conn);app,c=preparar(conn);a,ca=await criar(c)
    await conn.execute("UPDATE tenant_canais SET ativo=true WHERE tenant_id='alpha'")
    conv=f'alpha:{ca}:{cliente}';ct='ct_'+cliente
    await conn.execute("INSERT INTO contatos(id,tenant_id,telefone) VALUES ($1,'alpha',$2)",ct,cliente)
    await conn.execute("INSERT INTO conversas(conversation_id,canal,tenant_id,agente_id,canal_id,contato_id,telefone) VALUES ($1,'api','alpha',$2,$3,$4,$5)",conv,a,ca,ct,cliente)
    return app,c,conv,ct,f'/admin/v1/tenants/alpha/conversas/{conv}'


async def fala(conn,conv,texto,direcao='entrada',origem='cliente',segundos_atras=0,autor=None):
    await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,enviada_em,autor) VALUES ('alpha',$1,$2,$3,$4,now()-make_interval(secs=>$5),$6)",conv,direcao,origem,texto,segundos_atras,autor)


@pular
def test_etiquetas_por_empresa_papeis_e_filtro():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        assert (await c.post(E,headers=ANA,json={'nome':'VIP'})).status_code==403
        r=await c.post(E,headers=SUP,json={'nome':'  VIP  ','cor':'dourado'});assert r.status_code==201,r.text
        vip=r.json()['id']
        rec=(await c.post(E,headers=SUP,json={'nome':'Reclamação','cor':'vermelho'})).json()['id']
        repetida=await c.post(E,headers=SUP,json={'nome':'vip'})
        assert repetida.status_code==409 and repetida.json()['erro']['detalhes']['campo']=='nome'
        assert (await c.post(E,headers=SUP,json={'nome':'x','cor':'azul'})).status_code in (400,422)
        # Funcionário aplica, não cria.
        assert [i['nome'] for i in (await c.put(B+'/etiquetas',headers=ANA,json={'ids':[vip,rec]})).json()['itens']]==['Reclamação','VIP']
        L='/admin/v1/tenants/alpha/conversas'
        linha=(await c.get(L,headers=ANA)).json()['itens'][0]
        assert [e['nome'] for e in linha['etiquetas']]==['Reclamação','VIP']
        assert len((await c.get(f'{L}?etiqueta={vip}',headers=ANA)).json()['itens'])==1
        # A fila de espera obedece ao mesmo filtro.
        assert len((await c.get(f'{L}?etiqueta={vip}&aguardando=false',headers=ANA)).json()['itens'])==1
        await c.put(B+'/etiquetas',headers=ANA,json={'ids':[rec]})
        assert (await c.get(f'{L}?etiqueta={vip}',headers=ANA)).json()['itens']==[]
        # Apagar a etiqueta tira de todas as conversas.
        assert (await c.delete(f'{E}/{rec}',headers=ANA)).status_code==403
        assert (await c.delete(f'{E}/{rec}',headers=SUP)).status_code==200
        assert (await c.get(L,headers=ANA)).json()['itens'][0]['etiquetas']==[]
    banco(cenario)


@pular
def test_etiqueta_de_outra_empresa_nao_entra():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        await c.post('/admin/v1/tenants',json={'id':'bravo','nome':'b'})
        outra=(await c.post('/admin/v1/tenants/bravo/etiquetas',headers=SUP,json={'nome':'Dela'})).json()['id']
        assert (await c.put(B+'/etiquetas',headers=ANA,json={'ids':[outra]})).status_code==400
        assert (await c.patch(f'{E}/{outra}',headers=SUP,json={'nome':'x'})).status_code==404
        assert (await c.delete(f'{E}/{outra}',headers=SUP)).status_code==404
        assert (await c.get(E,headers=ANA)).json()['itens']==[]
    banco(cenario)


@pular
def test_busca_acha_em_mensagens_e_notas_sem_ligar_para_acento():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        await fala(conn,conv,'Moro na Rua São João, 100')
        await fala(conn,conv,'Outra coisa')
        await c.post(B+'/notas',headers=ANA,json={'texto':'Portão azul na sao joao'})
        r=(await c.get(B+'/busca?q=SAO JOAO',headers=ANA)).json()
        assert sorted(i['tipo'] for i in r['itens'])==['mensagem','nota'],r
        assert (await c.get(B+'/busca?q=x',headers=ANA)).status_code==400
        assert (await c.get(B.replace('/alpha/','/nao-existe/')+'/busca?q=joao',headers=ANA)).status_code==404
    banco(cenario)


@pular
def test_arquivo_em_teste_registra_sem_sair_e_recusa_formato():
    async def cenario(conn):
        app,c,conv,_,B=await montar(conn)
        enviadas=[]
        app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:(enviadas.append(req),httpx.Response(200,json={}))[1]))
        r=await c.post(B+'/midia',headers=ANA,json={'nome':'cardapio.pdf','conteudo':PDF,'legenda':'Segue'})
        assert r.status_code==201 and r.json()['send_mode']=='dry',r.text
        assert enviadas==[]
        m=await conn.fetchrow("SELECT conteudo,tipo,autor,meta->>'arquivo' AS arquivo FROM mensagens WHERE conversation_id=$1",conv)
        assert dict(m)=={'conteudo':'[arquivo enviado: cardapio.pdf]\nSegue','tipo':'documento','autor':'ana@exemplo.test','arquivo':'cardapio.pdf'}
        for ruim in ('nao-base64!!',base64.b64encode(b'MZ executavel').decode()):
            assert (await c.post(B+'/midia',headers=ANA,json={'nome':'x.pdf','conteudo':ruim})).status_code==400
        grande=base64.b64encode(b'%PDF'+b'0'*(10*1024*1024)).decode()
        assert (await c.post(B+'/midia',headers=ANA,json={'nome':'g.pdf','conteudo':grande})).status_code==413
        # Quem não está com a conversa não envia arquivo.
        assert (await c.post(B+'/midia',headers=BIA,json={'nome':'c.pdf','conteudo':PDF})).status_code==409
        lista=(await c.get(B+'/mensagens',headers=ANA)).json()['itens']
        assert (lista[0]['tipo'],lista[0]['arquivo'])==('documento','cardapio.pdf')
    banco(cenario)


def test_uazapi_envia_documento_e_imagem_no_formato_da_documentacao(monkeypatch):
    async def caso():
        chamadas=[]
        def provedor(req):
            chamadas.append((req.url.path,json.loads(req.content)))
            return httpx.Response(200,json={'messageid':'3EB0ABCDEF123456'})
        async def livre(url): return None
        monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',livre)
        monkeypatch.setattr(uazapi,'_credencial',lambda *a:('https://exemplo.test','tok'))
        http=httpx.AsyncClient(transport=httpx.MockTransport(provedor))
        assert await uazapi.enviar_midia(http,None,'t',{},'5511900000001','document','QUJD','a.pdf','Oi')=='3EB0ABCDEF123456'
        await uazapi.enviar_midia(http,None,'t',{},'5511900000001','image','QUJD','f.png')
        assert chamadas==[('/send/media',{'number':'5511900000001','type':'document','file':'QUJD','text':'Oi','docName':'a.pdf'}),
                          ('/send/media',{'number':'5511900000001','type':'image','file':'QUJD'})]
        recusa=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(400)))
        with pytest.raises(uazapi.EnvioRecusado): await uazapi.enviar_midia(recusa,None,'t',{},'1','image','QUJD','f.png')
    import asyncio;asyncio.run(caso())


@pular
def test_operacao_mede_espera_resposta_e_atendente():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        O='/admin/v1/tenants/alpha/operacao'
        await conn.execute("UPDATE conversas SET aguardando_atendente_desde=now()-interval '120 seconds',aguardando_motivo='erro_agente' WHERE conversation_id=$1",conv)
        assert (await c.get(O,headers=ANA)).status_code==403
        v=(await c.get(O,headers=SUP)).json()
        assert v['agora']['fila']==1 and v['periodo']['esperas']==0
        await fala(conn,conv,'Oi',segundos_atras=30)
        assert (await c.post(B+'/mensagens',headers=ANA,json={'texto':'Olá'})).status_code==201
        v=(await c.get(O,headers=SUP)).json()
        assert v['agora']=={'fila':0,'mais_antiga':None,'com_atendente':1}
        assert v['periodo']['esperas']==1 and 115<=v['periodo']['espera_p50_s']<=130
        assert v['periodo']['mensagens']==1 and 28<=v['periodo']['resposta_p50_s']<=40
        assert [(p['atendente'],p['mensagens'],p['esperas_assumidas'],p['abertas_agora']) for p in v['por_atendente']]==[('ana@exemplo.test',1,1,1)]
        for dias in (1,7,30):
            assert (await c.get(f'{O}?dias={dias}',headers=SUP)).json()['dias']==dias
        assert (await c.get(O+'?dias=3',headers=SUP)).status_code in (400,422)
    banco(cenario)


@pular
def test_lgpd_etiqueta_na_exportacao_e_fora_na_anonimizacao():
    async def cenario(conn):
        app,c,conv,ct,B=await montar(conn,'5511900000602')
        fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        e=(await c.post(E,headers=SUP,json={'nome':'VIP'})).json()['id']
        await c.put(B+'/etiquetas',headers=ANA,json={'ids':[e]})
        U=f'/admin/v1/tenants/alpha/contatos/{ct}'
        assert [x['nome'] for x in (await c.get(U+'/exportar',headers={'X-Role':'gestor'})).json()['etiquetas']]==['VIP']
        assert (await c.request('DELETE',U,json={'confirmacao':'5511900000602'},headers={'X-Role':'gestor'})).status_code==200
        assert await conn.fetchval("SELECT count(*) FROM conversa_etiquetas WHERE tenant_id='alpha'")==0
    banco(cenario)
