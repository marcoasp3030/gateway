"""UX2: espera adaptativa (P1), refazer o turno quando chega mensagem antes de a resposta sair (P2)
e amostra de eventos de presença no webhook."""
import json
from urllib.parse import urlsplit,parse_qs
import pytest
from nucleo import pipeline as PL
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_fila import banco_real,montar as montar_fila,limpar
from test_fase4_pedidos import montar as montar_pedidos
from test_uazapi_real import fx

CFG=dict(debounce_s=4,debounce_curto_s=2.5,debounce_palavras=6,debounce_incompleta_s=7)


def test_P1_espera_em_tres_faixas():
    f=lambda texto,**m: PL.forma_da_mensagem(CFG,{'texto':texto,**m})
    d=lambda texto,cfg=CFG,**m: PL.debounce_da_mensagem(cfg,{'texto':texto,**m})
    # Incompleta: vírgula, dois-pontos, hífen, reticências ou palavra de ligação no fim.
    for texto in ('quero um temaki e','pode ser…','então...','vou querer:','tipo','mas','o endereço é rua topázio,','quero 2 de','é pra','manda também','sem',
                  'quero um temaki e 🙂','Eu queria saber se vocês entregam no Morumbi e também que'):
        assert (f(texto),d(texto))==('incompleta',7),texto
    # Completa: pontuação final, ou mais de 6 palavras sem terminar em ligação.
    for texto in ('Qual o horário do Guedala amanhã?','Pode fechar.','Obrigado!','tudo bem? 🙂','quero um temaki de salmão sem cebolinha hoje','o que?'):
        assert (f(texto),d(texto))==('completa',2.5),texto
    # Demais: curta, sem sinal de continuação.
    for texto in ('Oi','Entrega','Pátio','quero edamame','13h'):
        assert (f(texto),d(texto))==('normal',4),texto
    # Clique em botão é resposta decidida; mídia, localização e reação seguem o normal.
    assert d('Confirmar pedido',meta={'interacao':{'tipo':'botao','id':'confirmar_pedido','mensagem':'X'}})==2.5
    for tipo in ('audio','imagem','localizacao','reacao'): assert d('qualquer coisa e',tipo=tipo)==4
    # Quem não configurou a espera de incompleta continua com o debounce normal (regressão), e a completa com a curta.
    antigo={**CFG,'debounce_curto_s':1.5,'debounce_incompleta_s':None}
    assert d('quero um temaki e',antigo)==4 and d('Pode fechar.',antigo)==1.5 and d('Oi',antigo)==4
    # A espera de incompleta nunca fica abaixo da normal; a curta nunca acima.
    assert d('tipo',{**CFG,'debounce_incompleta_s':2})==4 and d('Pode fechar.',{**CFG,'debounce_curto_s':9})==4


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_presenca_so_uma_amostra_fica_guardada_e_nada_e_processado():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            a,_=await criar(client)
            r=await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a})
            canal=r.json()['id'];segredo=parse_qs(urlsplit(r.json()['webhook_url']).query)['s'][0]
            await ativo(client,'alpha',a,canal)
            for i in range(26):
                r=await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json={'EventType':'presence','event':{'Chat':'5511900000001@s.whatsapp.net','State':'composing','n':i},'token':'tok-secreto'})
                assert r.status_code==200 and r.json()=={'ok':True,'ignorado':True}
            brutos=await conn.fetch("SELECT payload::text p FROM webhook_raw WHERE tenant_id='alpha'")
            assert len(brutos)==20 and all('tok-secreto' not in b['p'] for b in brutos)
            assert await conn.fetchval('SELECT count(*) FROM mensagens')==0 and await conn.fetchval('SELECT count(*) FROM conversas')==0 and fila.agendados==[]
            # Mensagem comum continua entrando normalmente depois da amostra cheia.
            assert (await client.post(f'/webhooks/uazapi/{canal}',params={'s':segredo},json=fx('texto'))).json().get('conversation_id')
            assert (await client.post(f'/webhooks/uazapi/{canal}',params={'s':'errado'},json={'EventType':'presence'})).status_code==401
    banco(cenario)


def test_P2_refazer_o_turno_uma_vez_sem_duplicar_carrinho_nem_resposta(tmp_path):
    async def cenario(pool):
        app,c,r,p,pref=await montar_fila(pool,True,tmp_path)
        try:
            async with c:
                conversa,ids,api,_=await montar_pedidos(pool,c,app)
                T,A='bykoji','bykoji_atendimento'
                async def modo(**campos): assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json=campos)).status_code==200
                async def chegar(conv,texto):
                    await pool.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ($1,$2,'entrada','cliente',$3)",T,conv,texto)
                saidas=lambda conv: pool.fetch("SELECT conteudo FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' ORDER BY id",T,conv)
                turnos=lambda conv: pool.fetch("SELECT * FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id",T,conv)
                pendentes=lambda conv: pool.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='entrada' AND processada_em IS NULL",T,conv)
                pacotes=[];roteiro=[]
                async def agente(http,repo,cofre,tenant,cfg,payload):
                    pacotes.append(payload);passo=roteiro.pop(0)
                    return await passo(payload)
                p.chamar=agente
                def fala(*textos,acao='nenhuma',antes=None):
                    async def passo(payload):
                        if antes: await antes(payload)
                        return {'resposta':list(textos),'acao':acao}
                    return passo

                # 0) Desligado (padrão): mensagem nova durante o turno NÃO refaz; a resposta sai e a nova fica para o próximo turno.
                c0=await conversa('u0')
                async def e_chega(payload): await chegar(payload['conversation_id'],'para retirada')
                roteiro[:]=[fala('Resposta do primeiro lote',antes=e_chega),fala('Resposta do segundo lote')]
                assert await p.processar_duravel(T,c0)=='concluido' and len(pacotes)==1
                assert [m['conteudo'] for m in await saidas(c0)]==['Resposta do primeiro lote'] and await pendentes(c0)==1
                assert [(t['status'],t['refeito_de'],t['refazer_recusado']) for t in await turnos(c0)]==[('ok',None,None)]
                assert await p.processar_duravel(T,c0)=='concluido' and pacotes[1]['acoes_ja_executadas']==[]

                # 1) Ligado: o agente põe 1 edamame no carrinho e, antes de a resposta sair, o cliente manda mais uma mensagem.
                await modo(refazer_turno='on');pacotes.clear()
                c1=await conversa('u1')
                async def adiciona_e_chega(payload):
                    resp=await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001'],'quantidade':1})
                    assert resp.status_code==200,resp.text
                    await chegar(payload['conversation_id'],'para retirada')
                roteiro[:]=[fala('Adicionei 1 Edamame!','Entrega ou retirada?',antes=adiciona_e_chega),fala('Edamame para retirada, anotado!')]
                assert await p.processar_duravel(T,c1)=='concluido' and len(pacotes)==2
                # Uma resposta só chegou ao cliente: a do lote completo.
                assert [m['conteudo'] for m in await saidas(c1)]==['Edamame para retirada, anotado!'] and await pendentes(c1)==0
                t1,t2=await turnos(c1)
                assert (t1['status'],t1['etapa'],t1['enviado_em'],t1['refeito_de'])==('refeito','concluido',None,None)
                assert json.loads(t1['resposta'])['resposta']==['Adicionei 1 Edamame!','Entrega ou retirada?']   # guardada para auditoria
                assert (t2['status'],t2['etapa'],t2['refeito_de'],t2['n_mensagens'])==('ok','concluido',t1['id'],2) and set(t1['mensagem_ids'])<set(t2['mensagem_ids'])
                # O pacote do turno refeito: lote completo, o que já foi feito e o carrinho como ficou.
                segundo=pacotes[1]
                assert [m['texto'] for m in segundo['mensagens_do_lote']]==['oi','para retirada'] and segundo['message']=='oi\npara retirada'
                assert segundo['acoes_ja_executadas']==[{'skill':'adicionar_item','item':'Edamame','quantidade':1,'resultado':'ok'}]
                assert pacotes[0]['acoes_ja_executadas']==[]
                carrinho=segundo['contexto_modulos']['pedidos']['carrinho']
                assert [(i['nome'],i['quantidade']) for i in carrinho['itens']]==[('Edamame',1)]
                # Não duplicou: o gateway não repete Skill nenhuma; o carrinho segue com 1 edamame.
                assert [(r['item_id'],r['quantidade']) for r in await pool.fetch("SELECT l.item_id,l.quantidade FROM carrinho_itens l JOIN carrinhos k ON k.id=l.carrinho_id WHERE k.conversation_id=$1",c1)]==[(ids['GUE-0001'],1)]
                assert await pool.fetchval("SELECT count(*) FROM skill_chamadas WHERE conversation_id=$1",c1)==1
                acoes=[r['acao'] for r in await pool.fetch("SELECT acao FROM audit_log WHERE tenant_id=$1 AND alvo=$2 ORDER BY id",T,c1)]
                assert 'turno.refeito' in acoes and 'resposta.descartada' in acoes

                # 2) No máximo um refazer: outra mensagem durante o turno refeito não refaz de novo.
                pacotes.clear();c2=await conversa('u2')
                async def chega(texto):
                    async def f(payload): await chegar(payload['conversation_id'],texto)
                    return f
                roteiro[:]=[fala('primeira (descartada)',antes=await chega('segunda mensagem')),fala('resposta do lote de duas',antes=await chega('terceira mensagem')),fala('resposta da terceira')]
                assert await p.processar_duravel(T,c2)=='concluido' and len(pacotes)==2
                assert [m['conteudo'] for m in await saidas(c2)]==['resposta do lote de duas'] and await pendentes(c2)==1
                ts=await turnos(c2);assert [(t['status'],t['refazer_recusado']) for t in ts]==[('refeito',None),('ok','ja_refeito')]
                # A terceira vira o próximo turno, comum (sem ações herdadas).
                assert await p.processar_duravel(T,c2)=='concluido' and pacotes[2]['acoes_ja_executadas']==[] and [m['texto'] for m in pacotes[2]['mensagens_do_lote']]==['terceira mensagem']
                assert [m['conteudo'] for m in await saidas(c2)]==['resposta do lote de duas','resposta da terceira'] and await pendentes(c2)==0

                # 3) Não refaz quando o turno já teve efeito: fechar_pedido/cancelar_pedido com sucesso, ou transferir/encerrar.
                for n,(skill,acao,motivo) in enumerate((('fechar_pedido','nenhuma','skill_com_efeito'),('cancelar_pedido','nenhuma','skill_com_efeito'),
                                                       (None,'transferir_humano','acao_com_efeito'),(None,'encerrar','acao_com_efeito'))):
                    pacotes.clear();cx=await conversa(f'e{n}')
                    async def efeito(payload,skill=skill):
                        if skill: await pool.execute("INSERT INTO skill_chamadas(tenant_id,conversation_id,skill,dados,resultado) VALUES ($1,$2,$3,'{}','ok')",T,payload['conversation_id'],skill)
                        await chegar(payload['conversation_id'],'mais uma')
                    roteiro[:]=[fala('Resposta que segue',acao=acao,antes=efeito)]
                    assert await p.processar_duravel(T,cx)=='concluido' and len(pacotes)==1,motivo
                    assert [m['conteudo'] for m in await saidas(cx)]==['Resposta que segue'] and await pendentes(cx)==1
                    assert [(t['status'],t['refazer_recusado']) for t in await turnos(cx)]==[('ok',motivo)]
                # Skill de pedido recusada (sem efeito) não impede o refazer.
                pacotes.clear();cr=await conversa('r1')
                async def recusada(payload):
                    assert (await api('POST','/pedidos',json={'conversation_id':payload['conversation_id']})).status_code in (409,422)
                    await chegar(payload['conversation_id'],'mais uma')
                roteiro[:]=[fala('descartada',antes=recusada),fala('final')]
                assert await p.processar_duravel(T,cr)=='concluido' and [m['conteudo'] for m in await saidas(cr)]==['final']
                assert pacotes[1]['acoes_ja_executadas'][0]['skill']=='fechar_pedido' and pacotes[1]['acoes_ja_executadas'][0]['resultado']!='ok'

                # 4) Worker cai logo depois de descartar: a retomada reconhece o refazer (não refaz de novo e leva as ações).
                pacotes.clear();c4=await conversa('u4')
                roteiro[:]=[fala('descartada',antes=adiciona_e_chega)]
                responder=p._responder;vezes=[]
                async def cai_na_segunda(*a,**k):
                    vezes.append(1)
                    if len(vezes)==2: raise RuntimeError('worker caiu depois de descartar')
                    return await responder(*a,**k)
                p._responder=cai_na_segunda
                with pytest.raises(RuntimeError): await p.processar_duravel(T,c4)
                p._responder=responder
                assert [m['conteudo'] for m in await saidas(c4)]==[] and await pendentes(c4)==2
                roteiro[:]=[fala('retomada',antes=await chega('outra ainda'))]
                assert await p.processar_duravel(T,c4)=='concluido' and len(pacotes)==2
                assert pacotes[1]['acoes_ja_executadas']==[{'skill':'adicionar_item','item':'Edamame','quantidade':1,'resultado':'ok'}]
                assert [m['conteudo'] for m in await saidas(c4)]==['retomada']
                assert [(t['status'],t['refazer_recusado']) for t in await turnos(c4)]==[('refeito',None),('ok','ja_refeito')]
                # Worker cai depois da resposta do turno refeito: só falta enviar; o agente não é chamado de novo.
                pacotes.clear();c5=await conversa('u5')
                roteiro[:]=[fala('descartada',antes=await chega('segunda')),fala('final')]
                enviar=p._enviar_partes
                async def cair(*a,**k): raise RuntimeError('worker caiu antes de enviar')
                p._enviar_partes=cair
                with pytest.raises(RuntimeError): await p.processar_duravel(T,c5)
                p._enviar_partes=enviar
                assert await p.processar_duravel(T,c5)=='concluido' and len(pacotes)==2 and [m['conteudo'] for m in await saidas(c5)]==['final']

                # 5) Métricas.
                m=(await c.get(f'/admin/v1/tenants/{T}/metricas')).json()['picotadas']
                assert m['turnos_refeitos']==5 and m['refazer_recusado']=={'ja_refeito':2,'skill_com_efeito':2,'acao_com_efeito':2}
                assert m['mensagens_por_turno']['dois']>=3 and m['espera_media_ms'] is not None and m['atropelados']>=1
                # Herança: empresa liga, agente pode desligar.
                await modo(refazer_turno=None,debounce_incompleta_s=7,debounce_curto_s=2.5)
                cfg=await app.state.configuracoes.efetiva(T,A)
                assert (cfg['refazer_turno'],cfg['origem_config']['refazer_turno'],cfg['debounce_incompleta_s'],cfg['debounce_curto_s'])==('off','plataforma',7,2.5)
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'refazer_turno':'sim'})).status_code==400
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'debounce_incompleta_s':20})).status_code==400
        finally:
            await limpar(r,pref)
    banco_real(cenario)
