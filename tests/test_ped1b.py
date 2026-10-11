"""PED1-b: textos e liga/desliga dos avisos por empresa, prévia, reenvio de aviso que falhou e X-Role."""
import httpx,pytest
from modulos.pedidos import avisos
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila
from test_fase4_avisos import montar,CLIENTE

B='/admin/v1/tenants/bykoji/pedidos'
ADMIN={'X-Actor':'ana','X-Role':'admin'}


def test_validacao_de_texto_e_padroes_por_modalidade():
    v=avisos.validar_texto
    assert v('Pedido {numero} ok[[ Previsão: {previsao}.]]') is None and v(avisos.TEXTOS['recusado']) is None
    assert v('')=='tamanho' and v('   ')=='tamanho' and v('x'*1001)=='tamanho' and v(None)=='tamanho'
    assert v('Oi {telefone}')=='variavel_desconhecida' and v('Oi {}')=='variavel_desconhecida'
    for ruim in ('Oi [[ {motivo}','Oi {motivo} ]]','Oi [[a [[b]] c]]','Oi {numero','Oi numero}'): assert v(ruim)=='formato',ruim
    assert all(v(t) is None for t in list(avisos.TEXTOS.values())+list(avisos.TEXTOS_RETIRADA.values()))
    assert avisos.modalidades('pronto_retirada')==('retirada',) and avisos.modalidades('saiu_entrega')==('delivery',) and avisos.modalidades('confirmado')==('delivery','retirada')
    assert 'entregue' in avisos.texto_padrao('concluido','delivery') and 'retirado' in avisos.texto_padrao('concluido','retirada')
    # Sem nada configurado vale o padrão; a empresa pode mudar só o texto, só o liga/desliga, ou os dois.
    e=avisos.efetivo
    assert e({},'em_preparo','delivery')==(False,avisos.TEXTOS['em_preparo']) and e({},'concluido','retirada')[0] is False and e({},'concluido','delivery')[0] is True
    assert e({('em_preparo','delivery'):(True,None)},'em_preparo','delivery')==(True,avisos.TEXTOS['em_preparo'])
    assert e({('confirmado','retirada'):(None,'Novo {numero}')},'confirmado','retirada')==(True,'Novo {numero}') and e({('confirmado','retirada'):(False,'x')},'confirmado','delivery')[0] is True


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_textos_por_empresa_liga_desliga_previa_e_papel(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch,whatsapp=None)
            await live()
            async def mudar(numero,**corpo): return await client.post(f'{B}/{numero}/status',json=corpo,headers={'X-Actor':'joana'})
            async def despachar():
                antes=len(enviados);await avisos.despachar(p);return [e[1] for e in enviados[antes:]]
            # Padrões: 12 linhas (status × modalidade que existe), tudo marcado como padrão.
            g=(await client.get(f'{B}/avisos')).json()
            assert g['variaveis']==['numero','unidade','previsao','motivo','nome_cliente'] and g['max_texto']==1000 and len(g['itens'])==12 and all(i['padrao'] and i['texto']==i['texto_padrao'] and i['ativo']==i['ativo_padrao'] for i in g['itens'])
            L={(i['status'],i['modalidade']):i for i in g['itens']}
            assert ('pronto_retirada','delivery') not in L and ('saiu_entrega','retirada') not in L
            assert [k for k,i in L.items() if not i['ativo']]==[('em_preparo','delivery'),('em_preparo','retirada'),('concluido','retirada')]
            # Papel: membro, sem cabeçalho ou papel desconhecido não altera (403); ler e ver a prévia pode.
            for cab in ({'X-Actor':'joana','X-Role':'membro'},{'X-Actor':'joana'},{'X-Actor':'joana','X-Role':'dono'},{'X-Actor':'joana','X-Role':''}):
                r=await client.put(f'{B}/avisos/confirmado',json={'ativo':False},headers=cab);assert r.status_code==403 and r.json()['erro']['codigo']=='FORBIDDEN',cab
            assert (await client.get(f'{B}/avisos',headers={'X-Role':'membro'})).status_code==200
            assert await conn.fetchval('SELECT count(*) FROM pedido_avisos_config')==0
            # Membro continua mudando status de pedido (PED1-a).
            c0,n0=await pedido('5511900000009');assert (await client.post(f'{B}/{n0}/status',json={'status':'confirmado'},headers={'X-Actor':'joana','X-Role':'membro'})).status_code==200
            await despachar()
            # Admin troca o texto do confirmado (as duas modalidades) e a plataforma liga o "em preparo" só na retirada.
            r=await client.put(f'{B}/avisos/confirmado',json={'texto':'  Recebemos! Pedido {numero} confirmado na {unidade}.[[ Fica pronto em {previsao}.]]  '},headers=ADMIN);assert r.status_code==200,r.text
            L={(i['status'],i['modalidade']):i for i in r.json()['itens']}
            assert L[('confirmado','retirada')]['texto']==L[('confirmado','delivery')]['texto']=='Recebemos! Pedido {numero} confirmado na {unidade}.[[ Fica pronto em {previsao}.]]' and not L[('confirmado','retirada')]['padrao'] and L[('confirmado','retirada')]['ativo']
            r=await client.put(f'{B}/avisos/em_preparo',json={'modalidade':'retirada','ativo':True},headers={'X-Actor':'root','X-Role':'plataforma'});assert r.status_code==200
            L={(i['status'],i['modalidade']):i for i in r.json()['itens']};assert L[('em_preparo','retirada')]['ativo'] and not L[('em_preparo','delivery')]['ativo'] and L[('em_preparo','retirada')]['texto']==avisos.TEXTOS['em_preparo']
            # Inválidos: variável desconhecida, formato, vazio, modalidade que não existe no status, corpo sem nada, status sem aviso.
            r=await client.put(f'{B}/avisos/confirmado',json={'texto':'Oi {telefone}'},headers=ADMIN);assert r.status_code==400 and r.json()['erro']['detalhes']['motivo']=='variavel_desconhecida' and 'numero' in r.json()['erro']['detalhes']['variaveis']
            for corpo in ({'texto':'Oi [[ {motivo}'},{'texto':''},{'texto':'x'*1001},{'modalidade':'delivery'},{}): assert (await client.put(f'{B}/avisos/confirmado',json=corpo,headers=ADMIN)).status_code==400,corpo
            assert (await client.put(f'{B}/avisos/pronto_retirada',json={'modalidade':'delivery','ativo':False},headers=ADMIN)).status_code==400
            assert (await client.put(f'{B}/avisos/aguardando_loja',json={'ativo':False},headers=ADMIN)).status_code==404
            assert (await client.put(f'{B}/avisos/confirmado',json={'ativo':False},headers={'X-Role':'admin','X-Actor':''})).status_code==400     # sem X-Actor
            # O aviso real usa o texto da empresa; "em preparo" agora avisa na retirada.
            c1,n1=await pedido();assert (await mudar(n1,status='confirmado',previsao_min=30)).status_code==200
            assert await despachar()==[f'Recebemos! Pedido {n1} confirmado na Pátio Guedala. Fica pronto em cerca de 30 minutos.']
            r=await mudar(n1,status='em_preparo');assert r.json()['aviso']=={'status':'pendente'}
            assert await despachar()==[f'Seu pedido nº {n1} já está sendo preparado 🍣']
            # Desligar um status: a mudança acontece, nada é enviado, a linha do tempo mostra "desligado".
            assert (await client.put(f'{B}/avisos/pronto_retirada',json={'ativo':False},headers=ADMIN)).status_code==200
            r=await mudar(n1,status='pronto_retirada');assert r.status_code==200 and r.json()['aviso']=={'status':'desligado'} and await despachar()==[]
            # Concluído em retirada: ligado pela empresa, usa o texto de retirada.
            assert (await client.put(f'{B}/avisos/concluido',json={'modalidade':'retirada','ativo':True},headers=ADMIN)).status_code==200
            assert (await mudar(n1,status='concluido')).status_code==200 and await despachar()==[f'Pedido nº {n1} retirado. Bom apetite, Cliente! 😊']
            # Voltar ao padrão: texto nulo e ativo nulo.
            r=await client.put(f'{B}/avisos/confirmado',json={'texto':None},headers=ADMIN);L={(i['status'],i['modalidade']):i for i in r.json()['itens']}
            assert L[('confirmado','delivery')]['texto']==avisos.TEXTOS['confirmado'] and L[('confirmado','delivery')]['padrao']
            r=await client.put(f'{B}/avisos/pronto_retirada',json={'ativo':None},headers=ADMIN);assert {(i['status'],i['modalidade']):i for i in r.json()['itens']}[('pronto_retirada','retirada')]['ativo'] is True
            # Auditoria com quem mexeu.
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id='bykoji' AND acao='pedido.aviso_configurado' AND actor IN ('ana','root')")==6
            # Prévia: com o texto enviado, ou com o que vale hoje; mostra também a versão sem os opcionais. Nada é gravado nem enviado.
            antes=len(enviados)
            r=await client.post(f'{B}/avisos/confirmado/previa',json={'texto':'Oi[[, {nome_cliente}]]! Pedido {numero} na {unidade}.[[ Previsão: {previsao}.]]'},headers={'X-Role':'membro','X-Actor':'joana'})
            assert r.status_code==200 and r.json()=={'status':'confirmado','modalidade':'delivery','texto':'Oi, Ana! Pedido 12345 na Pátio Guedala. Previsão: cerca de 40 minutos.','texto_sem_opcionais':'Oi! Pedido 12345 na Pátio Guedala.'}
            r=await client.post(f'{B}/avisos/concluido/previa',json={'modalidade':'retirada'},headers={'X-Actor':'joana'});assert r.json()['texto']=='Pedido nº 12345 retirado. Bom apetite, Ana! 😊'
            assert (await client.post(f'{B}/avisos/cancelado/previa',headers={'X-Actor':'joana'})).json()['texto'].startswith('Seu pedido nº 12345 foi cancelado pela unidade Pátio Guedala. Motivo: Faltou um ingrediente.')
            assert (await client.post(f'{B}/avisos/confirmado/previa',json={'texto':'{cpf}'},headers={'X-Actor':'joana'})).status_code==400 and (await client.post(f'{B}/avisos/nada/previa',headers={'X-Actor':'joana'})).status_code==404
            assert len(enviados)==antes
            # Isolamento: a outra empresa segue com os padrões e não é afetada pelo que o By Koji configurou.
            await criar(client)
            assert (await client.put(f'{B}/avisos/recusado',json={'texto':'Só do By Koji {numero}'},headers=ADMIN)).status_code==200
            o=(await client.get('/admin/v1/tenants/alpha/pedidos/avisos')).json();assert len(o['itens'])==12 and all(i['padrao'] for i in o['itens'])
            assert (await client.put('/admin/v1/tenants/alpha/pedidos/avisos/recusado',json={'ativo':False},headers=ADMIN)).status_code==200
            assert {(i['status'],i['modalidade']):i for i in (await client.get(f'{B}/avisos')).json()['itens']}[('recusado','delivery')]['ativo'] is True
            assert (await client.get('/admin/v1/tenants/nao_existe/pedidos/avisos')).status_code==404
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_reenvio_manual_so_de_aviso_que_falhou(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch,whatsapp=None)
            await live();bom=p.http
            async def mudar(numero,**corpo): assert (await client.post(f'{B}/{numero}/status',json=corpo,headers={'X-Actor':'joana'})).status_code==200
            async def reenviar(numero,status,corpo=None,**cab): return await client.post(f'{B}/{numero}/avisos/{status}/reenviar',json=corpo,headers={'X-Actor':'joana',**cab})
            aviso=lambda n,s: conn.fetchrow("SELECT status,erro,tentativas FROM avisos_internos WHERE tipo='pedido_cliente' AND referencia=$1",f'{n}:{s}')
            ultimo=lambda n: client.get(f'{B}/{n}')
            def fora(codigo): p.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda q: httpx.Response(codigo,json={'error':'x'})))
            motivo=lambda r: r.json()['erro']['detalhes']['motivo']
            # 1) Provedor recusou (4xx): com certeza não saiu → pode reenviar direto.
            c1,n1=await pedido();fora(400);await mudar(n1,status='confirmado');await avisos.despachar(p)
            a=await aviso(n1,'confirmado');assert (a['status'],a['erro'])==('falhou','recusado_pelo_provedor')
            lt=(await ultimo(n1)).json()['linha_do_tempo'][-1]['aviso'];assert (lt['status'],lt['incerto'],lt['pode_reenviar'])==('falhou',False,True)
            p.http=bom;antes=len(enviados)
            r=await reenviar(n1,'confirmado',**{'X-Role':'membro'});assert r.status_code==202 and r.json()=={'ok':True,'aviso':{'status':'pendente'}}
            await avisos.despachar(p);await avisos.despachar(p)
            assert [e[0] for e in enviados[antes:]]==[CLIENTE] and 'foi confirmado' in enviados[-1][1] and (await aviso(n1,'confirmado'))['status']=='enviado'
            # Já entregue: nunca reenvia.
            r=await reenviar(n1,'confirmado',{'mesmo_incerto':True});assert r.status_code==409 and motivo(r)=='aviso_nao_falhou' and r.json()['erro']['detalhes']['status']=='enviado'
            await avisos.despachar(p);assert len(enviados)==antes+1
            lt=(await ultimo(n1)).json()['linha_do_tempo'][-1]['aviso'];assert (lt['status'],lt['pode_reenviar'],lt['incerto'])==('enviado',False,False)
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='pedido.aviso_reenviado' AND alvo=$1 AND actor='joana'",n1)==1
            # 2) Falha incerta (5xx): pode ter chegado → só reenvia com confirmação explícita.
            c2,n2=await pedido('5511900000002');fora(500);await mudar(n2,status='confirmado');await avisos.despachar(p)
            lt=(await ultimo(n2)).json()['linha_do_tempo'][-1]['aviso'];assert (lt['status'],lt['incerto'],lt['pode_reenviar'])==('falhou',True,True)
            r=await reenviar(n2,'confirmado');assert r.status_code==409 and motivo(r)=='entrega_incerta' and (await aviso(n2,'confirmado'))['status']=='falhou'
            p.http=bom;antes=len(enviados)
            assert (await reenviar(n2,'confirmado',{'mesmo_incerto':True})).status_code==202
            await avisos.despachar(p);assert [e[0] for e in enviados[antes:]]==['5511900000002']
            # 3) O pedido já saiu daquele status: o aviso antigo não é reenviado.
            c3,n3=await pedido('5511900000003');fora(400);await mudar(n3,status='confirmado');await avisos.despachar(p);p.http=bom
            await mudar(n3,status='em_preparo')
            r=await reenviar(n3,'confirmado');assert r.status_code==409 and motivo(r)=='pedido_mudou_de_status'
            assert (await ultimo(n3)).json()['linha_do_tempo'][1]['aviso']['pode_reenviar'] is False
            # 4) Sem aviso (status desligado), pedido inexistente, outra empresa: 404. Sem X-Actor: 400.
            assert (await reenviar(n3,'em_preparo')).status_code==404 and (await reenviar('00000','confirmado')).status_code==404
            await criar(client);assert (await client.post(f'/admin/v1/tenants/alpha/pedidos/{n3}/avisos/confirmado/reenviar',headers={'X-Actor':'x'})).status_code==404
            assert (await client.post(f'{B}/{n3}/avisos/confirmado/reenviar',headers={'X-Actor':''})).status_code==400
            # 5) Reenvio que falha de novo volta a "falhou" (uma tentativa por pedido de reenvio).
            c4,n4=await pedido('5511900000004');fora(400);await mudar(n4,status='confirmado');await avisos.despachar(p)
            assert (await reenviar(n4,'confirmado')).status_code==202
            for _ in range(3): await avisos.despachar(p)
            a=await aviso(n4,'confirmado');assert (a['status'],a['tentativas'])==('falhou',2)
    banco(cenario)
