"""PED1-a: ciclo de status do pedido, linha do tempo, aviso transacional ao cliente, rotas do painel e consultar_pedido."""
import json
from datetime import datetime
import httpx,pytest
from modulos.pedidos import avisos,servico
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila
from test_fase4_avisos import montar,UNIDADE,CLIENTE

B='/admin/v1/tenants/bykoji/pedidos'


def test_maquina_de_status_e_textos():
    c=servico.conferir
    # Para frente, inclusive pulando etapas.
    for de,para in (('aguardando_loja','confirmado'),('aguardando_loja','em_preparo'),('confirmado','em_preparo'),('confirmado','concluido'),
                    ('em_preparo','concluido'),('aguardando_loja','concluido')):
        assert c(de,'retirada',para) is None and c(de,'delivery',para) is None,(de,para)
    assert c('em_preparo','retirada','pronto_retirada') is None and c('em_preparo','delivery','saiu_entrega') is None
    assert c('pronto_retirada','retirada','concluido') is None and c('saiu_entrega','delivery','concluido') is None
    # Modalidade errada, voltar, mesmo status, finais.
    assert c('em_preparo','delivery','pronto_retirada')=='modalidade_incompativel' and c('em_preparo','retirada','saiu_entrega')=='modalidade_incompativel'
    for de,para in (('confirmado','aguardando_loja'),('em_preparo','confirmado'),('pronto_retirada','em_preparo'),('saiu_entrega','confirmado')):
        assert c(de,'retirada' if de!='saiu_entrega' else 'delivery',para)=='nao_pode_voltar',(de,para)
    assert c('confirmado','retirada','confirmado')=='mesmo_status'
    for final in servico.FINAIS:
        for para in servico.STATUS[1:]: assert c(final,'retirada',para)=='status_final'
    # Recusar só enquanto aguarda a loja; cancelar em qualquer ponto antes do fim.
    assert c('aguardando_loja','retirada','recusado') is None
    for de in ('confirmado','em_preparo','pronto_retirada'): assert c(de,'retirada','recusado')=='recusa_so_aguardando'
    for de in ('aguardando_loja','confirmado','em_preparo','pronto_retirada'): assert c(de,'retirada','cancelado') is None
    assert servico.proximos({'status':'em_preparo','modalidade':'retirada'})==['pronto_retirada','concluido','cancelado']
    assert servico.proximos({'status':'aguardando_loja','modalidade':'delivery'})==['confirmado','em_preparo','saiu_entrega','concluido','recusado','cancelado']
    assert servico.proximos({'status':'concluido','modalidade':'delivery'})==[]
    # Textos: variáveis, trecho opcional e previsão em palavras.
    r=avisos.renderizar
    assert r('A {numero}[[ Previsão: {previsao}.]]',{'numero':'1','previsao':''})=='A 1' and r('A {numero}[[ Previsão: {previsao}.]]',{'numero':'1','previsao':'x'})=='A 1 Previsão: x.'
    assert r('Oi[[, {nome_cliente}]]! {desconhecida}',{'nome_cliente':'Ana'})=='Oi, Ana! {desconhecida}'
    assert [avisos.previsao(m) for m in (None,1,40,60,90,120)]==['','cerca de 1 minuto','cerca de 40 minutos','cerca de 1 hora','cerca de 1h30','cerca de 2 horas']
    assert [avisos.primeiro_nome(n) for n in ('Marco Antonio','.','🙂','5511999990000',None,'ana-maria silva')]==['Marco','','','','','ana-maria']
    p={'numero':'48213','status':'confirmado','motivo':None,'previsao_min':40,'cliente_nome':'Ana Lima','modalidade':'delivery'}
    assert avisos.texto_cliente(p,'Pátio Guedala')=='Oba! Seu pedido nº 48213 foi confirmado pela unidade Pátio Guedala ✅ Previsão: cerca de 40 minutos.'
    assert avisos.texto_cliente({**p,'previsao_min':None},'Pátio Guedala')=='Oba! Seu pedido nº 48213 foi confirmado pela unidade Pátio Guedala ✅'
    assert avisos.texto_cliente({**p,'status':'concluido'},'X')=='Pedido nº 48213 entregue. Bom apetite, Ana! 😊' and avisos.texto_cliente({**p,'status':'concluido','cliente_nome':'.'},'X')=='Pedido nº 48213 entregue. Bom apetite! 😊'
    assert avisos.texto_cliente({**p,'status':'cancelado','motivo':'Faltou salmão.'},'Morumbi')=='Seu pedido nº 48213 foi cancelado pela unidade Morumbi. Motivo: Faltou salmão. Se precisar, estou por aqui para ajudar.'
    assert avisos.texto_cliente({**p,'status':'recusado'},'Morumbi').startswith('Poxa, a unidade Morumbi não conseguiu aceitar o pedido nº 48213. Seus itens continuam')
    # Padrão: em preparo desligado; concluído só avisa em entrega.
    L=avisos.ligado
    assert [L(s,'delivery') for s in servico.STATUS[1:]]==[True,False,True,True,True,True,True] and L('concluido','retirada') is False and L('pronto_retirada','retirada')


async def ambiente(conn,monkeypatch,whatsapp=None):
    await migrar.aplicar(conn)
    app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
    return app,client,app.state.pipeline


def detalhes(r): return r.json()['erro']['detalhes']


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_ciclo_completo_linha_do_tempo_e_um_aviso_por_transicao(monkeypatch):
    async def cenario(conn):
        app,client,p=await ambiente(conn,monkeypatch)
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch,whatsapp=None)
            await live()
            assert (await client.post('/admin/v1/tenants/bykoji/webhooks-saida',json={'url':'https://painel.exemplo.com/gw','ativo':True,
                'eventos':['pedido.confirmado','pedido.em_preparo','pedido.pronto_retirada','pedido.concluido','pedido.cancelado']})).status_code==201
            async def mudar(numero,**corpo): return await client.post(f'{B}/{numero}/status',json=corpo,headers={'X-Actor':'joana'})
            async def despachar():
                # Sem limpar a lista: o provedor simulado numera os ids pelo tamanho dela.
                antes=len(enviados);await avisos.despachar(p);await avisos.despachar(p);return [e[1] for e in enviados[antes:] if e[0]!=UNIDADE]
            conv,n=await pedido()          # retirada
            d=(await client.get(f'{B}/{n}')).json()
            assert d['status']=='aguardando_loja' and d['proximos_status']==['confirmado','em_preparo','pronto_retirada','concluido','recusado','cancelado']
            assert [(e['de'],e['para'],e['origem'],e['aviso']) for e in d['linha_do_tempo']]==[(None,'aguardando_loja','agente',None)]
            assert d['cliente']=={'nome':'Cliente Teste','telefone':'+'+CLIENTE,'canal':{'id':canal,'tipo':'uazapi','nome':'WhatsApp','icone':'whatsapp'}}
            assert d['entrega']['modalidade']=='retirada' and d['unidade_nome']=='Pátio Guedala' and d['numero_itens']==2 and d['aviso'] is None
            # Confirmado com previsão: aviso com a previsão, uma vez só.
            r=await mudar(n,status='confirmado',previsao_min=40);assert r.status_code==200,r.text
            d=r.json();assert (d['status'],d['previsao_min'],d['decidido_por'],d['aviso'])==('confirmado',40,'joana',{'status':'pendente'})
            assert await despachar()==[f'Oba! Seu pedido nº {n} foi confirmado pela unidade Pátio Guedala ✅ Previsão: cerca de 40 minutos.']
            # Em preparo: aviso desligado por padrão; nada sai.
            r=await mudar(n,status='em_preparo');assert r.status_code==200 and r.json()['aviso']=={'status':'desligado'}
            assert await despachar()==[]
            # Pronto para retirada: avisa. Saiu para entrega não vale em pedido de retirada.
            r=await mudar(n,status='saiu_entrega');assert r.status_code==409 and detalhes(r)=={'campo':'status','status':'em_preparo','motivo':'modalidade_incompativel'}
            assert (await mudar(n,status='pronto_retirada')).status_code==200
            assert await despachar()==[f'Seu pedido nº {n} está pronto! Já pode retirar na unidade Pátio Guedala 🛍️']
            # Voltar, repetir: 409 com o status atual; nada muda e nada é avisado.
            for novo,motivo in (('confirmado','nao_pode_voltar'),('em_preparo','nao_pode_voltar'),('pronto_retirada','mesmo_status'),('recusado','recusa_so_aguardando')):
                r=await mudar(n,status=novo);assert r.status_code==409 and detalhes(r)=={'campo':'status','status':'pronto_retirada','motivo':motivo},novo
            # Concluído em retirada: aviso desligado por padrão. Depois disso, nada mais muda.
            r=await mudar(n,status='concluido');d=r.json();assert r.status_code==200 and d['proximos_status']==[] and d['aviso']=={'status':'desligado'}
            assert await despachar()==[]
            for novo in ('cancelado','confirmado','concluido'):
                r=await client.post(f'{B}/{n}/status',json={'status':novo,'motivo':'tarde demais'},headers={'X-Actor':'joana'})
                assert r.status_code==409 and detalhes(r)['status']=='concluido' and detalhes(r)['motivo']=='status_final'
            # Linha do tempo: quem, quando, previsão e o que houve com o aviso de cada passo.
            lt=d['linha_do_tempo']
            assert [(e['de'],e['para'],e['por'],e['origem'],e['previsao_min'],(e['aviso'] or {}).get('status')) for e in lt]==[
                (None,'aguardando_loja','agente','agente',None,None),('aguardando_loja','confirmado','joana','painel',40,'enviado'),
                ('confirmado','em_preparo','joana','painel',None,'desligado'),('em_preparo','pronto_retirada','joana','painel',None,'enviado'),
                ('pronto_retirada','concluido','joana','painel',None,'desligado')]
            assert all(datetime.fromisoformat(e['em']).tzinfo for e in lt) and lt[1]['aviso']['em'] and lt[1]['aviso']['erro'] is None
            # Um aviso por transição, gravado no histórico como "sistema" com a marca do pedido.
            assert await conn.fetchval("SELECT count(*) FROM avisos_internos WHERE tipo='pedido_cliente'")==2
            ms=await conn.fetch("SELECT origem,meta,message_id FROM mensagens WHERE conversation_id=$1 AND direcao='saida' ORDER BY id",conv)
            assert [(m['origem'],json.loads(m['meta'])['aviso_pedido']['status'],bool(m['message_id'])) for m in ms]==[('sistema','confirmado',True),('sistema','pronto_retirada',True)]
            ev=[r[0] for r in await conn.fetch("SELECT evento FROM webhook_entregas WHERE tenant_id='bykoji' ORDER BY id")]
            assert ev==['pedido.confirmado','pedido.em_preparo','pedido.pronto_retirada','pedido.concluido']
            assert [r['acao'] for r in await conn.fetch("SELECT acao FROM audit_log WHERE alvo=$1 AND actor='joana' ORDER BY id",n)]==['pedido.confirmado','pedido.em_preparo','pedido.pronto_retirada','pedido.concluido']

            # Pular etapas (aguardando → em preparo com previsão; → concluído): uma linha por pulo, aviso só do status de chegada.
            c2,n2=await pedido('5511900000002')
            r=await mudar(n2,status='em_preparo',previsao_min=25);assert r.status_code==200 and r.json()['decidido_por']=='joana' and r.json()['previsao_min']==25
            assert (await mudar(n2,status='concluido')).status_code==200
            assert [(e['de'],e['para']) for e in (await client.get(f'{B}/{n2}')).json()['linha_do_tempo']]==[(None,'aguardando_loja'),('aguardando_loja','em_preparo'),('em_preparo','concluido')]
            assert await despachar()==[] and await conn.fetchval("SELECT count(*) FROM avisos_internos WHERE tipo='pedido_cliente' AND referencia LIKE $1",n2+':%')==0

            # Cancelar: motivo obrigatório; vale depois de confirmado; itens NÃO voltam ao carrinho; aviso com o motivo.
            c3,n3=await pedido('5511900000003');assert (await mudar(n3,status='confirmado')).status_code==200
            for corpo in ({'status':'cancelado'},{'status':'cancelado','motivo':'  '},{'status':'cancelado','motivo':'ab'}): assert (await mudar(n3,**corpo)).status_code==400
            assert (await mudar(n3,status='concluido',previsao_min=10)).status_code==400 and (await mudar(n3,status='inexistente')).status_code==400
            assert (await mudar(n3,status='confirmado',previsao_min=601)).status_code==400
            r=await mudar(n3,status='cancelado',motivo='Faltou salmão');assert r.status_code==200 and (r.json()['status'],r.json()['motivo'])==('cancelado','Faltou salmão')
            textos=await despachar();assert textos[-1]==f'Seu pedido nº {n3} foi cancelado pela unidade Pátio Guedala. Motivo: Faltou salmão. Se precisar, estou por aqui para ajudar.'
            assert (await api('GET',f'/carrinho/{c3}')).json()['carrinho']['itens']==[]
            # Recusar (atalho antigo) continua devolvendo os itens ao carrinho e avisando.
            c4,n4=await pedido('5511900000004');r=await client.post(f'{B}/{n4}/recusar',json={'motivo':'fora da área'});assert r.status_code==200 and r.json()['status']=='recusado'
            assert len((await api('GET',f'/carrinho/{c4}')).json()['carrinho']['itens'])==1
            assert 'não conseguiu aceitar' in (await despachar())[-1]
            # Atalho confirmar aceita previsão; sem X-Actor não muda; pedido inexistente → 404.
            c5,n5=await pedido('5511900000005');r=await client.post(f'{B}/{n5}/confirmar',json={'previsao_min':15});assert r.status_code==200 and r.json()['previsao_min']==15
            assert (await client.post(f'{B}/{n5}/status',json={'status':'em_preparo'},headers={'X-Actor':''})).status_code==400
            assert (await mudar('00000',status='confirmado')).status_code==404
            # Cliente cancela pela Skill só enquanto aguarda a loja; entra na linha do tempo sem aviso.
            assert (await api('POST',f'/pedidos/{n5}/cancelar',json={'conversation_id':c5})).status_code==409
            c6,n6=await pedido('5511900000006');assert (await api('POST',f'/pedidos/{n6}/cancelar',json={'conversation_id':c6,'motivo':'desisti'})).status_code==200
            lt=(await client.get(f'{B}/{n6}')).json()['linha_do_tempo'];assert [(e['para'],e['origem'],e['motivo'],e['aviso']) for e in lt][-1]==('cancelado','cliente','desisti',None)
            # Dois cliques ao mesmo tempo no mesmo status: um vale, o outro recebe 409; um aviso só.
            c7,n7=await pedido('5511900000007')
            codigos=sorted([(await mudar(n7,status='confirmado')).status_code,(await mudar(n7,status='confirmado')).status_code]);assert codigos==[200,409]
            assert await conn.fetchval("SELECT count(*) FROM avisos_internos WHERE tipo='pedido_cliente' AND referencia=$1",n7+':confirmado')==1
            assert await conn.fetchval("SELECT count(*) FROM pedido_eventos e JOIN pedidos q ON q.id=e.pedido_id WHERE q.numero=$1 AND e.para='confirmado'",n7)==1
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_aviso_transacional_com_bot_pausado_piloto_teste_falha_e_isolamento(monkeypatch):
    async def cenario(conn):
        app,client,p=await ambiente(conn,monkeypatch)
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch,whatsapp=None)
            async def mudar(numero,**corpo): return await client.post(f'{B}/{numero}/status',json=corpo,headers={'X-Actor':'joana'})
            aviso=lambda n,s: conn.fetchrow("SELECT status,erro,tentativas FROM avisos_internos WHERE tipo='pedido_cliente' AND referencia=$1",f'{n}:{s}')
            # Envio de teste (dry): nada sai; o aviso fica "simulado" e entra no histórico.
            c0,n0=await pedido('5511900000010');assert (await mudar(n0,status='confirmado')).status_code==200
            await avisos.despachar(p);assert enviados==[] and (await aviso(n0,'confirmado'))['status']=='simulado'
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND origem='sistema' AND direcao='saida'",c0)==1
            await live()
            # Bot pausado por atendente e conversa aguardando atendente: o aviso sai; pausa e fila de atendimento não mudam.
            c1,n1=await pedido();await conn.execute("UPDATE conversas SET bot_pausado_ate=now()+interval '1 hour',aguardando_atendente_desde=now(),aguardando_motivo='erro_agente' WHERE conversation_id=$1",c1)
            assert (await mudar(n1,status='confirmado')).status_code==200
            await avisos.despachar(p);assert [e[0] for e in enviados]==[CLIENTE] and (await aviso(n1,'confirmado'))['status']=='enviado'
            v=await conn.fetchrow("SELECT bot_pausado_ate>now() pausada,aguardando_atendente_desde FROM conversas WHERE conversation_id=$1",c1);assert v['pausada'] and v['aguardando_atendente_desde']
            # Conversa encerrada: o aviso sai e não reabre.
            await conn.execute("UPDATE conversas SET bot_pausado_ate=NULL,aguardando_atendente_desde=NULL,aguardando_motivo=NULL,encerrada_em=now(),desfecho='pedido_feito' WHERE conversation_id=$1",c1)
            assert (await mudar(n1,status='pronto_retirada')).status_code==200;enviados.clear()
            await avisos.despachar(p);assert len(enviados)==1 and await conn.fetchval("SELECT encerrada_em IS NOT NULL FROM conversas WHERE conversation_id=$1",c1)
            # O aviso não vira turno do bot: nada fica pendente para o agente.
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND processada_em IS NULL AND direcao='saida'",c1)==0
            # Piloto: contato fora da lista não recebe (descartado); o da lista recebe.
            c2,n2=await pedido('5511900000002');c3,n3=await pedido('5511900000003')
            assert (await client.patch('/admin/v1/tenants/bykoji/agentes/bykoji_atendimento',json={'modo_piloto':True,'contatos_permitidos':['5511900000003']})).status_code==200
            assert (await mudar(n2,status='confirmado')).status_code==200 and (await mudar(n3,status='confirmado')).status_code==200;enviados.clear()
            await avisos.despachar(p);assert [e[0] for e in enviados]==['5511900000003'] and (await aviso(n2,'confirmado'))['status']=='descartado' and (await aviso(n3,'confirmado'))['status']=='enviado'
            assert (await client.patch('/admin/v1/tenants/bykoji/agentes/bykoji_atendimento',json={'modo_piloto':False})).status_code==200
            # Falha de envio: a transição fica; o aviso aparece como falha na linha do tempo e na contagem; não é reenviado.
            bom=p.http;p.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda q: httpx.Response(500,json={'error':'x'})))
            c4,n4=await pedido('5511900000004');assert (await mudar(n4,status='confirmado')).status_code==200
            for _ in range(3): await avisos.despachar(p)
            a=await aviso(n4,'confirmado');assert (a['status'],a['tentativas'])==('falhou',1)
            d=(await client.get(f'{B}/{n4}')).json();assert d['status']=='confirmado' and d['aviso']=={'status':'falhou'} and d['linha_do_tempo'][-1]['aviso']['status']=='falhou' and d['linha_do_tempo'][-1]['aviso']['erro']
            assert (await client.get(f'{B}/contagem')).json()['avisos_com_falha']==1
            p.http=bom
            # A varredura/reenvio do provedor não duplica: rodar de novo não envia nada.
            enviados.clear();await avisos.despachar(p);assert enviados==[]

            # Lista com filtros, busca e paginação; contagem por unidade.
            await conn.execute("UPDATE pedidos SET cliente_nome='Marina Souza' WHERE numero=$1",n3)
            L=lambda **q: client.get(B,params=q)
            todos=(await L()).json()['itens'];assert [x['numero'] for x in todos]==[n4,n3,n2,n1,n0] and todos[0]['aviso']=={'status':'falhou'}
            assert [x['numero'] for x in (await L(status='confirmado')).json()['itens']]==[n4,n3,n2,n0] and [x['numero'] for x in (await L(status='pronto_retirada,concluido')).json()['itens']]==[n1]
            assert [x['numero'] for x in (await L(busca='marina')).json()['itens']]==[n3] and [x['numero'] for x in (await L(busca=n2)).json()['itens']]==[n2]
            assert [x['numero'] for x in (await L(busca='0000004')).json()['itens']]==[n4] and (await L(busca='x')).status_code==400
            assert (await L(modalidade='delivery')).json()['itens']==[] and len((await L(modalidade='retirada',unidade='patio_guedala')).json()['itens'])==5 and (await L(unidade='morumbi')).json()['itens']==[]
            assert (await L(desde='2099-01-01T00:00:00Z')).json()['itens']==[] and len((await L(ate='2099-01-01T00:00:00Z')).json()['itens'])==5 and (await L(desde='2026-01-01T00:00:00')).status_code==400
            pg=(await L(limite=2)).json();assert [x['numero'] for x in pg['itens']]==[n4,n3] and [x['numero'] for x in (await L(limite=2,cursor=pg['proximo_cursor'])).json()['itens']]==[n2,n1]
            assert (await L(status='qualquer')).status_code==400
            c5,n5=await pedido('5511900000005')
            k=(await client.get(f'{B}/contagem')).json()
            assert k['aguardando_loja']['total']==1 and [(u['unidade'],u['nome'],u['total']) for u in k['aguardando_loja']['por_unidade']]==[('patio_guedala','Pátio Guedala',1)] and k['aguardando_loja']['mais_antigo_em']
            assert k['em_andamento']=={'confirmado':4,'em_preparo':0,'pronto_retirada':1,'saiu_entrega':0}

            # Isolamento: outra empresa não vê, não conta e não muda pedido do By Koji.
            await criar(client);O='/admin/v1/tenants/alpha/pedidos'
            assert (await client.get(O)).json()['itens']==[] and (await client.get(f'{O}/{n5}')).status_code==404
            assert (await client.post(f'{O}/{n5}/status',json={'status':'confirmado'},headers={'X-Actor':'x'})).status_code==404
            assert (await client.get(f'{O}/contagem')).json()['aguardando_loja']['total']==0 and (await client.get('/admin/v1/tenants/nao_existe/pedidos/contagem')).status_code==404
            assert await conn.fetchval("SELECT status FROM pedidos WHERE numero=$1",n5)=='aguardando_loja'
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_consultar_pedido_com_linha_do_tempo_e_ultimos_pedidos_no_pacote(monkeypatch):
    async def cenario(conn):
        app,client,p=await ambiente(conn,monkeypatch)
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch,whatsapp=None)
            async def mudar(numero,**corpo): assert (await client.post(f'{B}/{numero}/status',json=corpo,headers={'X-Actor':'joana'})).status_code==200
            conv,n=await pedido()
            await mudar(n,status='confirmado',previsao_min=40);await mudar(n,status='pronto_retirada')
            r=await api('GET',f'/pedidos/{n}',params={'conversation_id':conv});assert r.status_code==200,r.text
            d=r.json()['pedido']
            assert (d['status'],d['status_descricao'],d['previsao_min'])==('pronto_retirada','Pronto para retirada',40) and 0<=d['atualizado_ha_segundos']<60
            assert [e['status'] for e in d['linha_do_tempo']]==['aguardando_loja','confirmado','pronto_retirada'] and all(set(e)=={'status','em'} and e['em'].endswith('-03:00') for e in d['linha_do_tempo'])
            # Previsão = hora da confirmação + 40 min, no fuso da empresa. Nada de quem mexeu nem do aviso.
            minutos=(datetime.fromisoformat(d['previsao_ate'])-datetime.fromisoformat(d['linha_do_tempo'][1]['em'])).total_seconds()/60;assert 39<=minutos<=41
            assert 'joana' not in r.text and 'aviso' not in d and 'cliente_telefone' not in d
            # Sem número: os 3 últimos da conversa, do mais recente para o mais antigo.
            numeros=[n]
            for _ in range(3):
                await api('POST',f'/carrinho/{conv}/itens',json={'item_id':(await conn.fetchval("SELECT id FROM catalogo_itens WHERE codigo='GUE-0001'")),'quantidade':1})
                await api('PUT',f'/carrinho/{conv}/entrega',json={'modalidade':'retirada'})
                numeros.append((await api('POST','/pedidos',json={'conversation_id':conv})).json()['pedido']['numero'])
            r=await api('GET','/pedidos',params={'conversation_id':conv});assert r.status_code==200,r.text
            u=r.json()['pedidos'];assert [x['numero'] for x in u]==numeros[:0:-1] and set(u[0])=={'numero','status','status_descricao','unidade','total','atualizado_em'}
            assert u[0]['status']=='aguardando_loja' and u[0]['unidade']=='patio_guedala' and u[0]['atualizado_em'].endswith('-03:00')
            # Pedido de outra conversa: 404, com ou sem número ele não aparece.
            outra,n_outra=await pedido('5511900000002')
            assert (await api('GET',f'/pedidos/{n_outra}',params={'conversation_id':conv})).status_code==404
            assert n_outra not in [x['numero'] for x in (await api('GET','/pedidos',params={'conversation_id':conv})).json()['pedidos']]
            assert [x['numero'] for x in (await api('GET','/pedidos',params={'conversation_id':outra})).json()['pedidos']]==[n_outra]
            # No pacote do agente: último pedido com a situação em palavras e os 3 últimos.
            ctx=await servico.contexto(type('R',(),{'db':conn})(),'bykoji',conv)
            assert [x['numero'] for x in ctx['ultimos_pedidos']]==numeros[:0:-1] and ctx['ultimo_pedido']['status_descricao']=='Aguardando a confirmação da unidade' and ctx['ultimo_pedido']['atualizado_em']
            vazio=await servico.contexto(type('R',(),{'db':conn})(),'bykoji',(await webhook('5511900000009','oi'))['conversation_id'])
            assert vazio['ultimos_pedidos']==[] and vazio['ultimo_pedido'] is None
            assert await servico.ultimos(conn,'outra_empresa',conv)==[]
    banco(cenario)
