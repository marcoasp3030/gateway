"""UX1, bloco B1: ler o clique em botão de resposta (payload real de 10/10/2026 em fixtures/uazapi/clique_botao.json)."""
import copy,json,time
from urllib.parse import urlsplit,parse_qs
import pytest
from agentes import skyone
from canais import uazapi
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_skyone_adaptador import preparar_agente
from test_uazapi_real import fx,AGORA,CLIENTE

ORIGEM='3EB02BEE543170F3B092D5'


def test_clique_real_vira_texto_com_o_titulo_do_botao():
    n=uazapi.normalizar(fx('clique_botao'),AGORA)
    assert (n['telefone'],n['tipo'],n['texto'],n['from_me'],n['via_api'],n['nome'])==(CLIENTE,'texto','Sim, funcionou',False,False,'Cliente Teste')
    assert n['meta']=={'interacao':{'tipo':'botao','id':'teste_sim','mensagem':ORIGEM},'resposta_a':ORIGEM}
    assert n['message_id']=='A5C9BB44D679061FAF9C90DCE206ADF6'


def test_clique_apple_buttons_response_vira_texto_com_interacao():
    """iPhone: uazapi manda ButtonsResponseMessage (payload real 16/10/2026, mascarado)."""
    origem_apple='3EB07A66F2D9A6D01BCE0F'
    n=uazapi.normalizar(fx('clique_botao_apple'),AGORA)
    assert (n['telefone'],n['tipo'],n['texto'],n['from_me'],n['via_api'])==(CLIENTE,'texto','Entrega',False,False)
    assert n['meta']=={'interacao':{'tipo':'botao','id':'entrega','mensagem':origem_apple},'resposta_a':origem_apple}
    assert n['message_id']=='2A445D772CCFD1676F54'
    def com(mudar):
        p=copy.deepcopy(fx('clique_botao_apple'));mudar(p['message']);return uazapi.normalizar(p,AGORA)
    # Sem vote: ainda lê Response.SelectedDisplayText.
    assert com(lambda m: m.update(vote=''))['texto']=='Entrega'
    # Id só em selectedButtonID (sem buttonOrListid).
    assert com(lambda m: (m.update(buttonOrListid=''),))['meta']['interacao']['id']=='entrega'
    # Sem título nenhum: continua não suportado (agente pede texto).
    r=com(lambda m: (m.update(vote='',text=''),m['content'].get('Response',{}).clear() if False else m['content'].pop('Response',None)))
    assert r['tipo']=='desconhecido' and r['texto'].startswith('[não foi possível ler ButtonsResponseMessage')
    def com(mudar):
        p=copy.deepcopy(fx('clique_botao'));mudar(p['message']);return uazapi.normalizar(p,AGORA)
    # Título pelo campo "vote" quando o conteúdo não traz o texto exibido.
    assert com(lambda m: m['content'].pop('selectedDisplayText'))['texto']=='Sim, funcionou'
    # Id fora de [a-z0-9_]{1,40} ou sem a mensagem de origem: fica só o texto, sem interação.
    for mudar in (lambda m: (m['content'].update(selectedID='Confirmar Pedido!'),m.update(buttonOrListid='Confirmar Pedido!')),
                  lambda m: (m['content'].update(selectedID='a'*41),),
                  lambda m: (m.update(quoted=''),m['content']['contextInfo'].pop('stanzaID'))):
        r=com(mudar);assert (r['tipo'],r['texto'])==('texto','Sim, funcionou') and 'interacao' not in r['meta']
    # Id só em buttonOrListid também vale; origem só em contextInfo também.
    assert com(lambda m: m['content'].pop('selectedID'))['meta']['interacao']['id']=='teste_sim'
    assert com(lambda m: m.update(quoted=''))['meta']['interacao']['mensagem']==ORIGEM
    # Sem título nenhum não dá para saber o que o cliente escolheu: segue como tipo não suportado (o agente pede texto).
    r=com(lambda m: (m['content'].pop('selectedDisplayText'),m.update(vote='',text='')))
    assert r['tipo']=='desconhecido' and r['texto'].startswith('[não foi possível ler TemplateButtonReplyMessage')
    # Texto digitado continua igual.
    assert uazapi.normalizar(fx('texto'),AGORA)['meta']=={}


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_clique_pelo_webhook_uma_mensagem_so_mesmo_debounce_e_idempotente():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,_=await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal)
            conv=f'alpha:{canal}:{CLIENTE}'
            async def enviar(nome,**campos):
                p=fx(nome);p['message']['messageTimestamp']=int(time.time()*1000)-5000;p['message'].update(campos)
                resposta=await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=p)
                assert resposta.status_code==200,resposta.text
                return resposta.json()
            r=await enviar('clique_botao')
            assert r['conversation_id']==conv and not r['duplicada'] and len(fila.agendados)==1
            m=await conn.fetchrow('SELECT * FROM mensagens WHERE conversation_id=$1',conv)
            assert (m['origem'],m['direcao'],m['tipo'],m['conteudo'],m['processada_em'])==('cliente','entrada','texto','Sim, funcionou',None)
            assert json.loads(m['meta'])['interacao']=={'tipo':'botao','id':'teste_sim','mensagem':ORIGEM}
            # O mesmo clique de novo (reenvio do provedor ou varredura) não duplica.
            assert (await enviar('clique_botao'))['duplicada'] and await conn.fetchval('SELECT count(*) FROM mensagens WHERE conversation_id=$1',conv)==1
            # Clique seguido de texto digitado: as duas ficam pendentes para o mesmo turno, na ordem.
            await enviar('texto',messageid='OUTRA0000000000000000000000000001',id='5511900000002:OUTRA0000000000000000000000000001',text='e sem cebola',content='e sem cebola')
            itens=await app.state.pipeline._pendentes(conn,'alpha',conv)
            assert [(i['tipo'],i['conteudo']) for i in itens]==[('texto','Sim, funcionou'),('texto','e sem cebola')]
            # O gateway não mandou a mensagem de origem nesta conversa: o clique vai ao agente como texto puro.
            cfg=await app.state.configuracoes.efetiva('alpha',a)
            p=await skyone.pacote(app.state.pipeline.repo.__class__(conn),'alpha',conv,itens[0]['id'],'Sim, funcionou\ne sem cebola',cfg,lote=itens)
            assert p['ultima_interacao'] is None and p['message']=='Sim, funcionou\ne sem cebola'
            assert [set(i) for i in p['mensagens_do_lote']]==[{'enviada_em','tipo','texto'}]*2
            # O bruto fica guardado sem token.
            bruto=await conn.fetchval("SELECT payload::text FROM webhook_raw WHERE tenant_id='alpha' ORDER BY id LIMIT 1")
            assert 'TemplateButtonReplyMessage' in bruto and 'token-fixture' not in bruto
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_pacote_ultima_interacao_atual_antiga_e_texto_puro():
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        conv='bykoji:canal:x'
        await conn.execute("INSERT INTO conversas(conversation_id,canal,tenant_id,canal_id,agente_id) VALUES ('bykoji:canal:y','api','bykoji','canal','bykoji_atendimento')")
        async def bot(texto,pid=None,meta=None,conversa=conv,origem='bot'):
            return await conn.fetchval("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,message_id,meta,processada_em)
                VALUES ('bykoji',$1,'saida',$2,$3,$4,$5::jsonb,now()) RETURNING id""",conversa,origem,texto,f'canal:{pid}' if pid else None,json.dumps(meta) if meta else None)
        async def cliente(texto,clique=None,origem_msg='M1'):
            meta={'interacao':{'tipo':'botao','id':clique,'mensagem':origem_msg}} if clique else None
            return await conn.fetchrow("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,meta)
                VALUES ('bykoji',$1,'entrada','cliente',$2,$3::jsonb) RETURNING id,conteudo,criado_em,enviada_em,tipo,meta""",conv,texto,json.dumps(meta) if meta else None)
        async def pacote(*lote):
            return await skyone.pacote(repo,'bykoji',conv,lote[0]['id'],'\n'.join(i['conteudo'] for i in lote),cfg,lote=list(lote))
        # Sem clique: campo presente e nulo; itens do lote como sempre.
        t=await cliente('oi');p=await pacote(t)
        assert p['ultima_interacao'] is None and set(p['mensagens_do_lote'][0])=={'enviada_em','tipo','texto'}
        # Clique no botão da última mensagem do bot: atual.
        await bot('Posso confirmar?\n\n1. Confirmar pedido\n2. Alterar','M1')
        c=await cliente('Confirmar pedido','confirmar_pedido');p=await pacote(c)
        assert p['ultima_interacao']=={'tipo':'botao','id':'confirmar_pedido','titulo':'Confirmar pedido','atual':True}
        assert p['mensagens_do_lote'][0]['interacao']=={'tipo':'botao','id':'confirmar_pedido','atual':True}
        assert p['mensagens_do_lote'][0]['texto']=='Confirmar pedido' and p['message']=='Confirmar pedido'
        # Clique + texto digitado no mesmo lote: o clique continua identificado; o texto não ganha interação.
        d=await cliente('mas sem wasabi');p=await pacote(c,d)
        assert p['ultima_interacao']['id']=='confirmar_pedido' and 'interacao' not in p['mensagens_do_lote'][1]
        # O bot (ou o atendente) falou depois: o mesmo botão agora é de mensagem antiga.
        await bot('Pedido confirmado!');p=await pacote(c)
        assert p['ultima_interacao']['atual'] is False and p['mensagens_do_lote'][0]['interacao']['atual'] is False
        # Mensagem de origem que o gateway não enviou nesta conversa (inexistente ou de outra conversa): texto puro.
        await bot('De outra conversa','M9',conversa='bykoji:canal:y')
        for origem_msg in ('NAO_EXISTE','M9'):
            x=await cliente('Confirmar pedido','confirmar_pedido',origem_msg);p=await pacote(x)
            assert p['ultima_interacao'] is None and 'interacao' not in p['mensagens_do_lote'][0] and p['message']=='Confirmar pedido'
        # Mensagem do atendente não é origem de botão.
        await bot('Do atendente','M7',origem='humano')
        assert (await pacote(await cliente('Sim','sim','M7')))['ultima_interacao'] is None
        # Com as opções gravadas na mensagem (bloco B2): só vale id que esteja entre elas.
        await bot('Qual unidade?','M2',{'botoes':{'opcoes':[{'id':'morumbi','titulo':'Morumbi'},{'id':'einstein','titulo':'Einstein'}]}})
        assert (await pacote(await cliente('Morumbi','morumbi','M2')))['ultima_interacao']=={'tipo':'botao','id':'morumbi','titulo':'Morumbi','atual':True}
        assert (await pacote(await cliente('Outra','forjado','M2')))['ultima_interacao'] is None
    banco(cenario)
