"""UX3.1: segundo refazer (só sem Skill e sem balão, com teto de espera) e acoes_recentes no pacote."""
import asyncio,json
import pytest
from nucleo import pipeline as PL
from modulos.pedidos import servico as pedidos
from test_migrations import DSN
from test_fila import banco_real,montar as montar_fila,limpar
from test_fase4_pedidos import montar as montar_pedidos
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
T,A='bykoji','bykoji_atendimento'
EDAMAME={'skill':'adicionar_item','item':'Edamame','quantidade':1,'resultado':'ok'}


async def preparar(pool,tmp_path):
    app,c,r,p,pref=await montar_fila(pool,True,tmp_path)
    pacotes=[];roteiro=[]
    async def agente(http,repo,cofre,tenant,cfg,payload):
        pacotes.append(payload);return await roteiro.pop(0)(payload)
    p.chamar=agente
    def fala(texto,antes=None,demora=0):
        async def passo(payload):
            if antes: await antes(payload)
            await asyncio.sleep(demora);return {'resposta':[texto],'acao':'nenhuma'}
        return passo
    async def chegar(conv,texto): await pool.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ($1,$2,'entrada','cliente',$3)",T,conv,texto)
    def chega(texto):
        async def f(payload): await chegar(payload['conversation_id'],texto)
        return f
    saidas=lambda conv: pool.fetch("SELECT conteudo FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' AND origem='bot' ORDER BY id",T,conv)
    turnos=lambda conv: pool.fetch("SELECT status,refazer_recusado,refeito_de,id FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id",T,conv)
    return app,c,r,p,pref,pacotes,roteiro,fala,chegar,chega,saidas,turnos


def test_segundo_refazer_so_sem_skill_e_sem_balao_terceiro_nunca_e_teto_de_espera(tmp_path,monkeypatch):
    async def cenario(pool):
        app,c,r,p,pref,pacotes,roteiro,fala,chegar,chega,saidas,turnos=await preparar(pool,tmp_path)
        try:
            async with c:
                conversa,ids,api,_=await montar_pedidos(pool,c,app)
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'refazer_turno':'on'})).status_code==200
                async def rodar(conv): assert await p.processar_duravel(T,conv)=='concluido'
                quadro=lambda ts: [(t['status'],t['refazer_recusado']) for t in ts]
                async def adiciona(payload):
                    assert (await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001'],'quantidade':1})).status_code==200
                def depois(*passos):
                    async def f(payload):
                        for passo in passos: await passo(payload)
                    return f

                # 1) Turno refeito sem Skill e sem balão, e chega mais uma mensagem: refaz a segunda vez. A terceira, nunca.
                c1=await conversa('s1')
                roteiro[:]=[fala('descartada 1',chega('quero 1 edamame')),fala('descartada 2',chega('pra retirar')),fala('resposta única',chega('e uma coca')),fala('coca anotada')]
                await rodar(c1)
                assert len(pacotes)==3 and [m['texto'] for m in pacotes[2]['mensagens_do_lote']]==['oi','quero 1 edamame','pra retirar']
                assert [m['conteudo'] for m in await saidas(c1)]==['resposta única']          # uma resposta só chegou ao cliente
                ts=await turnos(c1);assert quadro(ts)==[('refeito',None),('refeito',None),('ok','ja_refeito')]
                assert (ts[1]['refeito_de'],ts[2]['refeito_de'])==(ts[0]['id'],ts[1]['id'])
                await rodar(c1);assert [m['conteudo'] for m in await saidas(c1)]==['resposta única','coca anotada']   # a 4ª mensagem vira o próximo turno

                # 2) O turno refeito chamou uma Skill: não refaz de novo; a resposta dele sai e a mensagem nova fica para o próximo turno.
                pacotes.clear();c2=await conversa('s2')
                roteiro[:]=[fala('descartada',chega('quero 1 edamame')),fala('Adicionei 1 Edamame!',depois(adiciona,chega('pra retirar'))),fala('Retirada anotada')]
                await rodar(c2)
                assert len(pacotes)==2 and [m['conteudo'] for m in await saidas(c2)]==['Adicionei 1 Edamame!']
                assert quadro(await turnos(c2))==[('refeito',None),('ok','skill_chamada')]
                # O turno seguinte é comum, mas sabe pelo acoes_recentes que o Edamame acabou de entrar.
                await rodar(c2)
                assert pacotes[2]['acoes_ja_executadas']==[] and [{k:v for k,v in a.items() if k!='ha_segundos'} for a in pacotes[2]['acoes_recentes']]==[EDAMAME]
                assert await pool.fetchval("SELECT sum(l.quantidade) FROM carrinho_itens l JOIN carrinhos k ON k.id=l.carrinho_id WHERE k.conversation_id=$1",c2)==1
                # Skill só no turno ORIGINAL (não no refeito): o segundo refazer continua permitido e leva as ações do original.
                pacotes.clear();c3=await conversa('s3')
                roteiro[:]=[fala('descartada 1',depois(adiciona,chega('pra retirar'))),fala('descartada 2',chega('no guedala')),fala('final')]
                await rodar(c3)
                assert quadro(await turnos(c3))==[('refeito',None),('refeito',None),('ok',None)] and pacotes[1]['acoes_ja_executadas']==pacotes[2]['acoes_ja_executadas']==[EDAMAME]

                # 3) Saiu balão na conversa durante o turno refeito: não refaz de novo.
                pacotes.clear();c4=await conversa('s4')
                async def balao(payload): await pool.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ($1,$2,'saida','bot','aviso qualquer')",T,payload['conversation_id'])
                roteiro[:]=[fala('descartada',chega('segunda')),fala('segue',depois(balao,chega('terceira'))),fala('terceira respondida')]
                await rodar(c4)
                assert len(pacotes)==2 and quadro(await turnos(c4))==[('refeito',None),('ok','balao_enviado')]

                # 4) Teto de espera: o turno original começou há mais que o teto → o 1º refazer vale (como hoje), o 2º não.
                monkeypatch.setattr(PL,'TETO_REFAZER_S',1)
                pacotes.clear();c5=await conversa('s5')
                roteiro[:]=[fala('descartada',chega('segunda'),demora=1.3),fala('segue',chega('terceira')),fala('terceira respondida')]
                await rodar(c5)
                assert len(pacotes)==2 and [m['conteudo'] for m in await saidas(c5)]==['segue'] and quadro(await turnos(c5))==[('refeito',None),('ok','teto_de_espera')]
                # Dentro do teto, o mesmo roteiro refaz duas vezes.
                monkeypatch.setattr(PL,'TETO_REFAZER_S',30)
                pacotes.clear();c6=await conversa('s6')
                roteiro[:]=[fala('descartada',chega('segunda'),demora=1.3),fala('descartada 2',chega('terceira')),fala('final')]
                await rodar(c6)
                assert len(pacotes)==3 and [m['conteudo'] for m in await saidas(c6)]==['final']

                # 5) Desligado no agente: nada é refeito.
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'refazer_turno':'off'})).status_code==200
                pacotes.clear();c7=await conversa('s7')
                roteiro[:]=[fala('primeira',chega('segunda')),fala('segunda respondida')]
                await rodar(c7);assert len(pacotes)==1 and quadro(await turnos(c7))==[('ok',None)]
                m=(await c.get(f'/admin/v1/tenants/{T}/metricas')).json()['picotadas']
                assert m['refazer_recusado']=={'ja_refeito':1,'skill_chamada':1,'balao_enviado':1,'teto_de_espera':1}
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_acoes_recentes_sempre_no_pacote_com_janela_por_agente_e_isolamento(tmp_path):
    async def cenario(pool):
        app,c,r,p,pref,pacotes,roteiro,fala,chegar,chega,saidas,turnos=await preparar(pool,tmp_path)
        try:
            async with c:
                conversa,ids,api,_=await montar_pedidos(pool,c,app)
                cfg=await app.state.configuracoes.efetiva(T,A)
                assert (cfg['acoes_recentes_min'],cfg['origem_config']['acoes_recentes_min'])==(15,'plataforma')
                async def turno(conv,passo=None):
                    roteiro[:]=[passo or fala('ok')];assert await p.processar_duravel(T,conv)=='concluido';return pacotes[-1]
                async def adiciona(payload):
                    resp=await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001'],'quantidade':1,'observacao':'sem cebolinha por favor'})
                    assert resp.status_code==200,resp.text
                    resp=await api('PUT',f'/carrinho/{payload["conversation_id"]}/entrega',json={'modalidade':'retirada'})
                    assert resp.status_code==200,resp.text
                envelhecer=lambda conv,intervalo: pool.execute("UPDATE skill_chamadas SET criado_em=criado_em-$3::text::interval WHERE tenant_id=$1 AND conversation_id=$2",T,conv,intervalo)

                # 1) Turno comum, conversa sem ações: lista vazia (sempre presente).
                c1=await conversa('r1');c2=await conversa('r2')
                assert (await turno(c2))['acoes_recentes']==[]
                # 2) O agente adiciona o item no turno 1; o pacote do turno 2 (comum) traz a chamada do turno anterior.
                primeiro=await turno(c1,fala('Adicionei 1 Edamame!',adiciona))
                assert primeiro['acoes_recentes']==[]                      # o pacote é montado antes de o agente agir
                await chegar(c1,'pra retirar');segundo=await turno(c1)
                assert segundo['acoes_ja_executadas']==[]                  # não é turno refeito
                recentes=segundo['acoes_recentes']
                assert [a['skill'] for a in recentes]==['adicionar_item','definir_entrega']      # da mais antiga para a mais recente
                assert {k:v for k,v in recentes[0].items() if k!='ha_segundos'}==EDAMAME and isinstance(recentes[0]['ha_segundos'],int) and 0<=recentes[0]['ha_segundos']<60
                assert all(set(a)<={'skill','item','quantidade','resultado','detalhe','ha_segundos'} for a in recentes)
                assert 'cebolinha' not in json.dumps(recentes,ensure_ascii=False)                 # sem observação nem endereço
                # 3) Isolamento: outra conversa não vê; outra empresa não alcança.
                await chegar(c2,'oi de novo');assert (await turno(c2))['acoes_recentes']==[]
                assert await pedidos.acoes_recentes(pool,'postman_teste',c1,15)==[] and len(await pedidos.acoes_recentes(pool,T,c1,15))==2
                # 4) Janela: 14 min ainda aparece (com o tempo certo); 16 min, não. Configurável por agente.
                await envelhecer(c1,'14 minutes');await chegar(c1,'a');r14=(await turno(c1))['acoes_recentes']
                assert len(r14)==2 and 840<=r14[0]['ha_segundos']<900
                await envelhecer(c1,'2 minutes');await chegar(c1,'b');assert (await turno(c1))['acoes_recentes']==[]
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'acoes_recentes_min':30})).status_code==200
                await chegar(c1,'c');assert len((await turno(c1))['acoes_recentes'])==2
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'acoes_recentes_min':0})).status_code==200
                await chegar(c1,'d');assert (await turno(c1))['acoes_recentes']==[]
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'acoes_recentes_min':1441})).status_code==400
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'acoes_recentes_min':None})).status_code==200
                # 5) No máximo 20, as mais recentes, em ordem.
                c3=await conversa('r3')
                for n in range(25):
                    await pool.execute("INSERT INTO skill_chamadas(tenant_id,conversation_id,skill,dados,resultado,criado_em) VALUES ($1,$2,'adicionar_item',$3::jsonb,'ok',now()-make_interval(secs=>$4))",
                                       T,c3,json.dumps({'item':f'Item {n}','quantidade':1}),float(100-n))
                muitas=(await turno(c3))['acoes_recentes']
                assert [a['item'] for a in muitas]==[f'Item {n}' for n in range(5,25)] and muitas[0]['ha_segundos']>muitas[-1]['ha_segundos']
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_esvaziar_e_expirar_devolvem_o_carrinho_ao_estado_inicial_completo(tmp_path):
    async def cenario(pool):
        app,c,r,p,pref,pacotes,roteiro,fala,chegar,chega,saidas,turnos=await preparar(pool,tmp_path)
        try:
            async with c:
                conversa,ids,api,_=await montar_pedidos(pool,c,app)
                INICIAL=dict(unidade=None,modalidade=None,endereco=None,horario=None,observacao=None,maioridade_confirmada=False,itens=[],
                             numero_itens=0,subtotal=None,taxa_entrega=None,total=None,faltando=['itens','modalidade','pagamento'],pode_fechar=False,
                             forma_pagamento=None,troco_para=None)   # PAG1: o pagamento também volta ao começo
                async def carrinho(conv): return (await api('GET',f'/carrinho/{conv}')).json()['carrinho']
                async def montar_cheio(conv):
                    assert (await api('POST',f'/carrinho/{conv}/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})).status_code==200
                    resp=await api('PUT',f'/carrinho/{conv}/entrega',json={'modalidade':'delivery','endereco':'Rua Topázio, 100','horario':'amanhã às 20h','observacao':'tocar a campainha','maioridade_confirmada':True})
                    assert resp.status_code==200,resp.text
                    k=await carrinho(conv)
                    assert (k['modalidade'],k['endereco'],k['horario'],k['observacao'],k['maioridade_confirmada'])==('delivery','Rua Topázio, 100','amanhã às 20h','tocar a campainha',True) and k['unidade']
                # 1) Esvaziar: tudo volta ao começo (antes a modalidade, o endereço e o horário ficavam).
                c1=await conversa('z1');await montar_cheio(c1)
                assert (await api('DELETE',f'/carrinho/{c1}')).status_code==200
                k=await carrinho(c1);assert {campo:k[campo] for campo in INICIAL}==INICIAL
                # Pode recomeçar em outra unidade/modalidade sem herança.
                assert (await api('POST',f'/carrinho/{c1}/itens',json={'item_id':ids['GUE-0001'],'quantidade':1})).status_code==200
                k=await carrinho(c1);assert (k['modalidade'],k['endereco'],k['faltando'])==(None,None,['modalidade','pagamento'])
                # 2) Expiração: o carrinho que o agente vê depois é o inicial; os dados antigos ficam só no arquivado.
                c2=await conversa('z2');await montar_cheio(c2)
                await pool.execute("UPDATE carrinhos SET atualizado_em=now()-interval '7 hours' WHERE conversation_id=$1",c2)
                roteiro[:]=[fala('ok')];assert await p.processar_duravel(T,c2)=='concluido'
                ctx=pacotes[-1]['contexto_modulos']['pedidos']
                assert ctx['carrinho_expirado'] is True and {campo:ctx['carrinho'][campo] for campo in INICIAL}==INICIAL
                k=await carrinho(c2);assert {campo:k[campo] for campo in INICIAL}==INICIAL
                assert (await api('PUT',f'/carrinho/{c2}/entrega',json={'modalidade':'retirada'})).status_code==200
                k=await carrinho(c2);assert (k['modalidade'],k['endereco'],k['horario'],k['maioridade_confirmada'])==('retirada',None,None,False)
                # 3) Pedido fechado não é afetado: esvaziar depois de fechar não mexe no pedido nem no carrinho fechado.
                c3=await conversa('z3');await montar_cheio(c3)
                fechado=await api('POST','/pedidos',json={'conversation_id':c3});assert fechado.status_code==201,fechado.text
                antes=dict(await pool.fetchrow("SELECT modalidade,endereco,horario,status FROM carrinhos WHERE conversation_id=$1",c3))
                assert (await api('DELETE',f'/carrinho/{c3}')).status_code==200
                assert dict(await pool.fetchrow("SELECT modalidade,endereco,horario,status FROM carrinhos WHERE conversation_id=$1",c3))==antes and antes['status']=='fechado' and antes['modalidade']=='delivery'
                p_=await pool.fetchrow("SELECT * FROM pedidos WHERE numero=$1",fechado.json()['pedido']['numero']);assert p_['status']=='aguardando_loja'
        finally:
            await limpar(r,pref)
    banco_real(cenario)
