"""Fase 4, bloco 3: aviso à unidade, confirmação pelo painel e pelo WhatsApp, aviso ao cliente e alerta de transbordo."""
import json,time
from urllib.parse import urlsplit,parse_qs
import httpx,pytest
from canais import uazapi
from modulos.pedidos import avisos,servico
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila,ativo
from test_uazapi_real import fx
from test_fase4_pedidos import CSV

UNIDADE='5511900000050';ALERTA='5511900000060';CLIENTE='5511900000001'


def test_comando_da_unidade_e_textos():
    c=servico.comando_da_unidade
    assert c('OK 48213')==('confirmado','48213',None) and c(' ok #48213. ')==('confirmado','48213',None) and c('Sim 48213')==('confirmado','48213',None)
    assert c('NÃO 48213 sem salmão hoje')==('recusado','48213','sem salmão hoje') and c('nao 48213')==('recusado','48213',None)
    assert c('Não 48213: fechamos\n  mais cedo')==('recusado','48213','fechamos mais cedo')
    # Conversa comum não é comando: nada de decidir pedido por engano.
    for texto in ('ok','OK obrigado','48213','ok 48213 e o outro?','não sei 48213','oi, tudo bem?','OK 12','',None,'okay então 48213'):
        assert c(texto) is None,texto
    assert avisos.reais(327)=='R$ 327,00' and avisos.reais(1200.5)=='R$ 1.200,50' and avisos.reais(None)=='a confirmar'
    assert avisos.mascarar_numero('5511942717046')=='5511****7046' and avisos.mascarar_numero(None)=='(sem número)'
    p={'numero':'48213','cliente_nome':'Ana','cliente_telefone':'5511900000001','subtotal':327,'taxa_entrega':None,'total':327,'modalidade':'delivery',
       'endereco':'Rua A, 10','horario':'20h','observacao':None,'tem_alcoolico':True}
    t=avisos.texto_pedido_unidade(p,[{'quantidade':2,'nome':'Tempurá Moriawase','preco_unitario':136,'subtotal':272,'observacao':'sem cebola'},
                                     {'quantidade':1,'nome':'Sapporo','preco_unitario':55,'subtotal':55,'observacao':None}],'Pátio Guedala')
    for trecho in ('NOVO PEDIDO 48213','2× Tempurá Moriawase (R$ 136,00) = R$ 272,00','obs.: sem cebola','Subtotal: R$ 327,00','Taxa de entrega: a confirmar',
                   'Total: R$ 327,00','Entrega — Rua A, 10','Horário: 20h','18 anos','responda: OK 48213','responda: NÃO 48213 motivo'):
        assert trecho in t,trecho
    assert 'não se aplica' in avisos.texto_pedido_unidade({**p,'modalidade':'retirada'},[],'X') and 'Retirada' in avisos.texto_pedido_unidade({**p,'modalidade':'retirada'},[],'X')
    # Alerta de transbordo: número mascarado, motivo, pedido e painel; nunca o telefone inteiro.
    a=avisos.texto_transbordo('5511942717046','Ana','transferido_pelo_agente','pediu_humano',{'numero':'48213','status':'aguardando_loja'},'https://painel.exemplo.com')
    assert '5511****7046' in a and '5511942717046' not in a and 'pediu_humano' in a and 'Pedido: 48213' in a and 'https://painel.exemplo.com' in a


async def montar(conn,client,app,monkeypatch,whatsapp=UNIDADE):
    """bykoji com canal uazapi em envio real (provedor simulado), catálogo, unidade com WhatsApp e chave das Skills."""
    async def livre(url): return None
    monkeypatch.setattr(uazapi.seguranca,'validar_url_saida',livre)
    enviados=[]
    def provedor(req):
        corpo=json.loads(req.content);enviados.append((corpo.get('number'),corpo.get('text')))
        return httpx.Response(200,json={'messageid':'3EB0AVISO%011d'%len(enviados)})
    app.state.pipeline.http=httpx.AsyncClient(transport=httpx.MockTransport(provedor))
    await conn.execute("INSERT INTO modulos_tenant(tenant_id,modulo,ativo) VALUES ('bykoji','catalogo',true),('bykoji','pedidos',true) ON CONFLICT (tenant_id,modulo) DO UPDATE SET ativo=true")
    r=await client.post('/admin/v1/tenants/bykoji/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':'bykoji_atendimento','credenciais':{'base_url':'https://instancia.exemplo.com','token':'tok-1'}})
    canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
    await ativo(client,'bykoji','bykoji_atendimento',canal,debounce_s=1,max_wait_s=1)
    n=[0]
    async def webhook(telefone,texto,**campos):
        n[0]+=1;p=fx('texto');p['message'].update(chatid=f'{telefone}@s.whatsapp.net',sender_pn=f'{telefone}@s.whatsapp.net',text=texto,content=texto,
            messageid=f'WEBHOOK{n[0]:06d}',messageTimestamp=int(time.time()*1000));p['message'].update(campos)
        r=await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=p);assert r.status_code==200,r.text
        return r.json()
    assert (await client.post('/admin/v1/tenants/bykoji/catalogo/importar',params={'simulacao':'false'},content=CSV.encode())).json()['aplicado']
    assert (await client.post('/admin/v1/tenants/bykoji/unidades',json={'nome':'Pátio Guedala','codigo':'patio_guedala','whatsapp_pedidos':whatsapp})).status_code==201
    k=(await client.post('/admin/v1/tenants/bykoji/api-keys',json={'nome':'skills','escopos':['ferramentas:catalogo','ferramentas:pedidos']})).json()
    chave=next(v for a,v in k.items() if isinstance(v,str) and a in ('chave','api_key','valor','segredo','key'))
    ids={r['codigo']:r['id'] for r in await conn.fetch("SELECT codigo,id FROM catalogo_itens WHERE tenant_id='bykoji'")}
    async def visto(caminho):
        # DISP1: adicionar_item só aceita item que apareceu numa busca da conversa. Aqui os testes marcam o catálogo
        # como buscado; a proteção em si é testada em tests/test_disp1.py.
        import re
        m=re.fullmatch(r'/carrinho/([^/]+)/itens',caminho)
        if m: await conn.execute("INSERT INTO catalogo_vistos(tenant_id,conversation_id,item_id) SELECT 'bykoji',$1,id FROM catalogo_itens WHERE tenant_id='bykoji' ON CONFLICT DO NOTHING",m.group(1))
    async def api(metodo,caminho,**kw):
        if metodo=='POST': await visto(caminho)
        # PAG1: como em test_fase4_pedidos.montar, a entrega sem forma de pagamento leva Pix (regra testada em tests/test_pag1.py).
        if metodo=='PUT' and caminho.endswith('/entrega') and 'modalidade' in (kw.get('json') or {}) and 'forma_pagamento' not in kw['json'] \
                and (kw['json'].get('unidade') or await conn.fetchval("SELECT unidade FROM carrinhos WHERE status='aberto' AND conversation_id=$1",caminho.split('/')[2])):
            await conn.execute("""INSERT INTO unidade_pagamentos(tenant_id,unidade_id,codigo,rotulo,aceita_troco,ativo,ordem)
                SELECT u.tenant_id,u.id,'pix','Pix',false,true,0 FROM tenant_unidades u WHERE u.tenant_id='bykoji'
                AND NOT EXISTS (SELECT 1 FROM unidade_pagamentos p WHERE p.tenant_id=u.tenant_id AND p.unidade_id=u.id)""")
            kw['json']={**kw['json'],'forma_pagamento':'pix'}
        return await client.request(metodo,'/api/v1'+caminho,headers={'X-API-Key':chave,'Authorization':''},**kw)
    async def pedido(telefone=CLIENTE):
        conv=(await webhook(telefone,'oi'))['conversation_id']
        await api('POST',f'/carrinho/{conv}/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})
        await api('PUT',f'/carrinho/{conv}/entrega',json={'modalidade':'retirada'})
        return conv,(await api('POST','/pedidos',json={'conversation_id':conv})).json()['pedido']['numero']
    async def live():
        await conn.execute("UPDATE agentes SET send_mode='live' WHERE id='bykoji_atendimento'");app.state.configuracoes.invalidar('bykoji')
    return webhook,api,pedido,enviados,live,canal


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_pedido_avisa_a_unidade_e_ela_confirma_pelo_whatsapp(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch)
            assert (await client.post('/admin/v1/tenants/bykoji/webhooks-saida',json={'url':'https://painel.exemplo.com/gw','eventos':['pedido.criado','pedido.confirmado','pedido.recusado'],'ativo':True})).status_code==201
            conv,numero=await pedido()
            aviso=lambda tipo: conn.fetchrow("SELECT * FROM avisos_internos WHERE tenant_id='bykoji' AND tipo=$1 ORDER BY id DESC LIMIT 1",tipo)
            a=await aviso('pedido_unidade');assert (a['destino'],a['status'],a['referencia'])==(UNIDADE,'pendente',numero) and f'NOVO PEDIDO {numero}' in a['texto']
            # Envio de teste: o aviso fica "simulado" e nada sai para o WhatsApp.
            await avisos.despachar(p);assert (await aviso('pedido_unidade'))['status']=='simulado' and enviados==[]
            # Envio real: sai para o WhatsApp de pedidos da unidade, uma vez só.
            await live();conv,numero=await pedido('5511900000002')
            await avisos.despachar(p);await avisos.despachar(p)
            assert [e[0] for e in enviados]==[UNIDADE] and f'NOVO PEDIDO {numero}' in enviados[0][1] and (await aviso('pedido_unidade'))['status']=='enviado'
            # O número da unidade nunca vira cliente: nem conversa, nem turno, nem resposta do bot.
            r=await webhook(UNIDADE,'boa tarde, alguém aí?');assert r.get('interno') and r.get('ignorado')
            assert await conn.fetchval("SELECT count(*) FROM conversas WHERE telefone=$1",UNIDADE)==0 and await conn.fetchval("SELECT count(*) FROM mensagens m JOIN conversas v USING(conversation_id) WHERE v.telefone=$1",UNIDADE)==0
            # "OK <numero>" de um número qualquer (cliente) não decide nada: vira mensagem comum.
            r=await webhook('5511900000077',f'OK {numero}');assert not r.get('interno')
            assert await conn.fetchval("SELECT status FROM pedidos WHERE numero=$1",numero)=='aguardando_loja'
            # A unidade confirma: pedido confirmado, cliente avisado, unidade recebe a resposta, webhook de saída.
            enviados.clear()
            r=await webhook(UNIDADE,f'ok {numero}');assert (r['comando'],r['resultado'])==('confirmado','confirmado')
            ped=await conn.fetchrow("SELECT status,decidido_por,decidido_em FROM pedidos WHERE numero=$1",numero)
            assert (ped['status'],ped['decidido_por'])==('confirmado','unidade:whatsapp') and ped['decidido_em'] is not None
            await avisos.despachar(p)
            assert sorted(e[0] for e in enviados)==sorted([UNIDADE,'5511900000002'])
            textos=dict(enviados);assert f'nº {numero} foi confirmado' in textos['5511900000002'] and 'Pátio Guedala' in textos['5511900000002'] and f'Pedido {numero} confirmado' in textos[UNIDADE]
            # O aviso ao cliente entra na conversa como mensagem do sistema (com o id do provedor e a marca do pedido).
            m=await conn.fetchrow("SELECT origem,conteudo,message_id,meta FROM mensagens WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1",conv)
            assert m['origem']=='sistema' and 'foi confirmado' in m['conteudo'] and m['message_id'] and json.loads(m['meta'])['aviso_pedido']=={'numero':numero,'status':'confirmado'}
            ev=[r[0] for r in await conn.fetch("SELECT evento FROM webhook_entregas WHERE tenant_id='bykoji' ORDER BY id")]
            assert ev.count('pedido.criado')==2 and ev.count('pedido.confirmado')==1
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='pedido.confirmado' AND alvo=$1 AND actor='unidade:whatsapp'",numero)==1
            # Repetir (ou tentar recusar depois) não muda nada; a unidade é informada. Reenvio do mesmo webhook não responde duas vezes.
            enviados.clear()
            r=await webhook(UNIDADE,f'NÃO {numero} acabou');assert r['resultado']=='ja_decidido' and await conn.fetchval("SELECT status FROM pedidos WHERE numero=$1",numero)=='confirmado'
            r=await webhook(UNIDADE,f'NÃO {numero} acabou',messageid='WEBHOOK-REPETIDO');await webhook(UNIDADE,f'NÃO {numero} acabou',messageid='WEBHOOK-REPETIDO')
            await avisos.despachar(p);assert len([e for e in enviados if e[0]==UNIDADE])==2 and all('já estava confirmado' in e[1] for e in enviados)
            assert await conn.fetchval("SELECT count(*) FROM avisos_internos WHERE tipo='pedido_cliente'")==1
            # Pedido inexistente: resposta neutra.
            enviados.clear();r=await webhook(UNIDADE,'OK 99999');assert r['resultado']=='nao_encontrado'
            await avisos.despachar(p);assert 'não encontrado' in enviados[0][1]
            # Mensagem enviada pela API (o próprio aviso do gateway voltando pelo webhook) é ignorada.
            assert (await webhook(UNIDADE,f'OK {numero}',wasSentByApi=True,fromMe=True)).get('ignorado')
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_recusa_devolve_o_carrinho_e_o_primeiro_que_decide_vale(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch)
            await live();conv,numero=await pedido()
            # Recusa pelo WhatsApp com motivo: cliente avisado e itens de volta no carrinho.
            r=await webhook(UNIDADE,f'Não {numero} estamos sem edamame');assert r['resultado']=='recusado'
            ped=await conn.fetchrow("SELECT status,motivo FROM pedidos WHERE numero=$1",numero);assert tuple(ped)==('recusado','estamos sem edamame')
            k=(await api('GET',f'/carrinho/{conv}')).json()['carrinho'];assert [(i['codigo'],i['quantidade']) for i in k['itens']]==[('GUE-0001',2)] and k['pode_fechar']
            await avisos.despachar(p);texto=dict(enviados)[CLIENTE]
            assert 'não conseguiu aceitar' in texto and 'Motivo: estamos sem edamame.' in texto and 'carrinho' in texto
            # O cliente pode fechar de novo: novo pedido, novo número.
            n2=(await api('POST','/pedidos',json={'conversation_id':conv})).json()['pedido']['numero'];assert n2!=numero
            # Painel confirma primeiro; o WhatsApp chega depois e não muda nada.
            r=await client.post(f'/admin/v1/tenants/bykoji/pedidos/{n2}/confirmar');assert r.status_code==200 and r.json()['status']=='confirmado' and r.json()['decidido_por']=='teste'
            assert (await webhook(UNIDADE,f'NÃO {n2} tarde demais'))['resultado']=='ja_decidido'
            r=await client.post(f'/admin/v1/tenants/bykoji/pedidos/{n2}/recusar',json={'motivo':'x'});assert r.status_code==409 and r.json()['erro']['detalhes']['status']=='confirmado'
            assert await conn.fetchval("SELECT status FROM pedidos WHERE numero=$1",n2)=='confirmado'
            # WhatsApp decide primeiro; o painel recebe 409.
            c3,n3=await pedido('5511900000003');assert (await webhook(UNIDADE,f'OK {n3}'))['resultado']=='confirmado'
            assert (await client.post(f'/admin/v1/tenants/bykoji/pedidos/{n3}/confirmar')).status_code==409
            # Painel recusa com motivo; pedido cancelado pelo cliente não é mais decidível; inexistente → 404.
            c4,n4=await pedido('5511900000004');r=await client.post(f'/admin/v1/tenants/bykoji/pedidos/{n4}/recusar',json={'motivo':'fora da área'})
            assert r.status_code==200 and (r.json()['status'],r.json()['motivo'])==('recusado','fora da área')
            c5,n5=await pedido('5511900000005');await api('POST',f'/pedidos/{n5}/cancelar',json={'conversation_id':c5})
            assert (await client.post(f'/admin/v1/tenants/bykoji/pedidos/{n5}/confirmar')).status_code==409 and (await webhook(UNIDADE,f'OK {n5}'))['resultado']=='ja_decidido'
            assert (await client.post('/admin/v1/tenants/bykoji/pedidos/00000/confirmar')).status_code==404
            # Unidade de outro número não decide pedido que não é dela.
            await client.post('/admin/v1/tenants/bykoji/unidades',json={'nome':'Morumbi','codigo':'morumbi','whatsapp_pedidos':'5511900000051'})
            c6,n6=await pedido('5511900000006');assert (await webhook('5511900000051',f'OK {n6}'))['resultado']=='nao_encontrado'
            assert await conn.fetchval("SELECT status FROM pedidos WHERE numero=$1",n6)=='aguardando_loja'
            # Lista de avisos para o painel (sem o texto).
            itens=(await client.get('/admin/v1/tenants/bykoji/avisos')).json()['itens'];assert itens and 'texto' not in itens[0] and {i['tipo'] for i in itens}>={'pedido_unidade','pedido_cliente','resposta_unidade'}
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_unidade_sem_whatsapp_fica_so_no_painel_e_aviso_ao_cliente_sai_mesmo_com_o_bot_pausado(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch,whatsapp=None)
            await live();conv,numero=await pedido()
            # Sem WhatsApp de pedidos: nenhum aviso; o pedido fica no painel e é confirmado por lá.
            assert await conn.fetchval("SELECT count(*) FROM avisos_internos WHERE tipo='pedido_unidade'")==0
            assert [x['numero'] for x in (await client.get('/admin/v1/tenants/bykoji/pedidos',params={'status':'aguardando_loja'})).json()['itens']]==[numero]
            # Conversa assumida por atendente: o aviso de status é transacional e sai assim mesmo (PED1); a pausa não muda.
            await conn.execute("UPDATE conversas SET bot_pausado_ate=now()+interval '1 hour' WHERE conversation_id=$1",conv)
            assert (await client.post(f'/admin/v1/tenants/bykoji/pedidos/{numero}/confirmar')).status_code==200
            await avisos.despachar(p);assert [e[0] for e in enviados]==[CLIENTE] and (await conn.fetchrow("SELECT status FROM avisos_internos WHERE tipo='pedido_cliente'"))['status']=='enviado'
            assert await conn.fetchval("SELECT bot_pausado_ate>now() FROM conversas WHERE conversation_id=$1",conv)
            # Falha do provedor no aviso à unidade: tenta de novo e desiste depois de 3; aviso ao cliente nunca é reenviado.
            uid=(await client.get('/admin/v1/tenants/bykoji/unidades')).json()['itens'][0]['id']
            await client.patch(f'/admin/v1/tenants/bykoji/unidades/{uid}',json={'whatsapp_pedidos':UNIDADE})
            p.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda q: httpx.Response(500,json={'error':'x'})))
            c2,n2=await pedido('5511900000002')
            for _ in range(4): await avisos.despachar(p)
            a=await conn.fetchrow("SELECT status,tentativas,erro FROM avisos_internos WHERE tipo='pedido_unidade'");assert (a['status'],a['tentativas'])==('falhou',3) and 'exemplo' not in (a['erro'] or '')
            assert (await client.post(f'/admin/v1/tenants/bykoji/pedidos/{n2}/confirmar')).status_code==200
            for _ in range(3): await avisos.despachar(p)
            a=await conn.fetchrow("SELECT status,tentativas FROM avisos_internos WHERE tipo='pedido_cliente' ORDER BY id DESC LIMIT 1");assert (a['status'],a['tentativas'])==('falhou',1)
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_alerta_de_transbordo_quando_a_conversa_fica_aguardando_atendente(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila;p=app.state.pipeline
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch)
            await live()
            async def agente(http,repo,cofre,tenant,cfg,payload): return {'resposta':['Vou chamar a equipe.'],'acao':'transferir_humano','motivo':'pediu_humano'}
            p.chamar=agente
            # Sem número de alerta cadastrado: nenhum aviso.
            conv=(await webhook(CLIENTE,'quero falar com uma pessoa'))['conversation_id'];await p.processar_duravel('bykoji',conv)
            assert await conn.fetchval("SELECT aguardando_motivo FROM conversas WHERE conversation_id=$1",conv)=='transferido_pelo_agente'
            assert await conn.fetchval("SELECT count(*) FROM avisos_internos WHERE tipo='transbordo'")==0
            # Com número cadastrado: um alerta por entrada em "aguardando atendente", com motivo e painel. Destino interno (padrão): telefone inteiro (CFG1-c).
            assert (await client.patch('/admin/v1/tenants/bykoji',json={'whatsapp_alerta_transbordo':ALERTA})).status_code==200
            assert (await client.patch('/admin/v1/tenants/bykoji',json={'whatsapp_alerta_transbordo':'11-9999'})).status_code in (400,422)
            enviados.clear();c2=(await webhook('5511942717046','quero falar com uma pessoa'))['conversation_id'];await p.processar_duravel('bykoji',c2)
            await avisos.despachar(p);alerta=[e for e in enviados if e[0]==ALERTA]
            assert len(alerta)==1 and '+55 11 94271-7046' in alerta[0][1] and 'pediu_humano' in alerta[0][1] and 'Painel:' in alerta[0][1]
            # Já aguardando: nova mensagem não gera outro alerta. Erro do agente em outra conversa gera.
            await conn.execute("UPDATE conversas SET bot_pausado_ate=NULL WHERE conversation_id=$1",c2)
            await webhook('5511942717046','alô?');await p.processar_duravel('bykoji',c2);assert await conn.fetchval("SELECT count(*) FROM avisos_internos WHERE tipo='transbordo'")==1
            async def quebra(*a,**k): raise RuntimeError('x')
            p.chamar=quebra;c3=(await webhook('5511900000003','oi'))['conversation_id'];await p.processar_duravel('bykoji',c3)
            a=await conn.fetchrow("SELECT texto,destino FROM avisos_internos WHERE tipo='transbordo' ORDER BY id DESC LIMIT 1");assert a['destino']==ALERTA and 'erro do agente' in a['texto']
            # O número de alerta também nunca é cliente.
            r=await webhook(ALERTA,'ok, vou ver');assert r.get('interno') and await conn.fetchval("SELECT count(*) FROM conversas WHERE telefone=$1",ALERTA)==0
            # Desligar: campo nulo.
            assert (await client.patch('/admin/v1/tenants/bykoji',json={'whatsapp_alerta_transbordo':None})).status_code==200
    banco(cenario)
