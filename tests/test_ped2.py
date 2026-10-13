"""PED2: horário agendado validado, horario_vencido no carrinho, número do pedido na raiz, categorias na busca,
registro de todas as chamadas de Skill e pedido anunciado sem fechar (docs/ESPEC_PED2.md)."""
import json
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import pytest
from agentes import skyone
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from modulos.pedidos import agendamento as AG,anuncio as AN
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar,motivo
from test_fila import banco_real,montar as montar_fila,limpar

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
SP=ZoneInfo('America/Sao_Paulo')
T,A='bykoji','bykoji_atendimento'
# Segunda-feira, 17:56 em São Paulo: a hora do teste do piloto em que o agente marcou "hoje às 19h".
AGORA=datetime(2026,10,12,17,56,tzinfo=SP)


def test_texto_do_horario_vira_data_e_hora():
    def ler(texto): return AG.interpretar(texto,AGORA)
    def em(dia,hora,minuto=0): return 'agendado',datetime(2026,10,dia,hora,minuto,tzinfo=SP)
    for texto,esperado in (('hoje às 19h',em(12,19)),('19h',em(12,19)),('19:30',em(12,19,30)),('às 20h30',em(12,20,30)),('20 horas',em(12,20)),
                           ('7 da noite',em(12,19)),('às 19',em(12,19)),('amanhã ao meio-dia',em(13,12)),('amanha 12h',em(13,12)),('depois de amanhã 12h',em(14,12)),
                           ('sexta às 20h',em(16,20)),('dia 15/10 às 12h',em(15,12)),('dia 20 às 13:15',em(20,13,15)),('Hoje às 19h no Pátio Guedala',em(12,19)),
                           ('meia-noite',em(13,0)),('segunda 12h',em(19,12)),('daqui a 40 minutos',em(12,18,36)),('em 2 horas',em(12,19,56)),('hoje às 15h',em(12,15))):
        assert ler(texto)==esperado,texto
    for texto in ('o quanto antes','agora','já','assim que possível','hoje, o quanto antes','',None):
        assert ler(texto)==('sem_agendamento',None),texto
    for texto in ('mais tarde','à noite','hoje','amanhã','31/02 às 10h','19','no fim da tarde'):
        assert ler(texto)==('invalido',None),texto
    assert AG.de_iso('2026-10-12T19:00','America/Sao_Paulo')==AG.de_iso('2026-10-12T19:00:00-03:00','UTC')==datetime(2026,10,12,19,tzinfo=SP)
    assert AG.de_iso('2026-10-12','UTC') is None and AG.de_iso('amanhã','UTC') is None


def test_texto_que_anuncia_pedido_fechado_e_o_que_nao_anuncia():
    anuncia=lambda t: AN.sinal({'resposta':[t],'acao':'nenhuma'})
    for texto in ('Acabei de fechar seu pedido para retirada hoje às 19h no Pátio Guedala 😊','Pedido feito!','Prontinho, seu pedido foi registrado.',
                  'Fechei o seu pedido, Marco!','Seu pedido está confirmado para hoje às 19h.','Tudo certo! Pedido enviado para a unidade.','Finalizei seu pedido 🎉',
                  'O número do seu pedido é 48213.','Pedido nº 55978 registrado'):
        assert anuncia(texto)=='texto',texto
    # Falso positivo que não pode acontecer: falar do que ainda vai ser feito, perguntar, explicar o que falta.
    for texto in ('Vou fechar seu pedido, posso confirmar?','Vou fechar seu pedido agora.','Posso fechar o pedido?','Quer confirmar o pedido?',
                  'Para confirmar o pedido, toque em Confirmar pedido.','Assim que você confirmar, seu pedido será enviado para a unidade.',
                  'Seu pedido ainda não foi fechado: falta o endereço.','Resumo do pedido: 1 Baterá Shake — R$ 58,00. Confirma?','O pedido mínimo é R$ 50.',
                  'Anotei 1 Edamame no seu pedido.','Rua Jacarandá, número 1200. Posso confirmar?','Não consegui fechar seu pedido porque o horário já passou.',
                  'Quando o pedido for confirmado eu aviso.'):
        assert anuncia(texto) is None,texto
    # Transferência para a equipe confirmar um pedido não é anúncio de fechamento.
    assert AN.sinal({'resposta':['Seu pedido foi enviado para a equipe confirmar o preço.'],'acao':'transferir_humano','motivo':'pedido_para_confirmar'}) is None
    assert AN.sinal({'resposta':['Tudo certo!'],'acao':'pedido_fechado'})=='acao' and AN.sinal({'resposta':['Tudo certo!'],'acao':'nenhuma','desfecho':'pedido_feito'})=='desfecho'


def test_horario_agendado_recusas_carrinho_vencido_e_numero_na_raiz(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        relogio=[AGORA];monkeypatch.setattr(AG,'agora_utc',lambda: relogio[0].astimezone(timezone.utc))
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            c=await conversa('cliente1');base=f'/carrinho/{c}'
            async def entrega(**dados): return await api('PUT',base+'/entrega',json=dados)
            carrinho=lambda: api('GET',base)
            detalhes=lambda r: r.json()['erro']['detalhes']
            assert (await api('POST',base+'/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})).status_code==200

            # Carrinho sem horário: os dois campos novos existem e nada muda (regressão).
            k=(await carrinho()).json()['carrinho'];assert (k['horario'],k['agendado_para'],k['horario_vencido'])==(None,None,False)
            # "o quanto antes" continua sem agendamento.
            k=(await entrega(modalidade='retirada',horario='o quanto antes')).json()['carrinho']
            assert (k['horario'],k['agendado_para'],k['horario_vencido'],k['pode_fechar'])==('o quanto antes',None,False,True)

            # Unidade ainda sem horário cadastrado: só dá para dizer se já passou.
            r=await entrega(horario='hoje às 15h');d=detalhes(r)
            assert (r.status_code,motivo(r),d['campo'])==(422,'horario_passado','horario') and d['agendado_para']=='2026-10-12T15:00:00-03:00' and d['agora']=='2026-10-12T17:56:00-03:00'
            assert d['proxima_abertura'] is None and r.json()['carrinho']['horario']=='o quanto antes'      # recusado: o carrinho fica como estava
            k=(await entrega(agendado_para='2026-10-12T17:53')).json()['carrinho'];assert k['horario_vencido'] is False     # 5 minutos de tolerância
            assert motivo(await entrega(agendado_para='2026-10-12T17:50'))=='horario_passado'
            k=(await entrega(horario='hoje às 18h')).json()['carrinho'];assert (k['horario'],k['agendado_para'])==('hoje às 18h','2026-10-12T18:00:00-03:00')

            # Com a grade: seg, qua, qui 19–23h; sex 19h–02h (cruza a meia-noite); ter, sáb e dom fechada. Quinta 15/10 fechada por exceção.
            await conn.execute("UPDATE tenant_unidades SET horarios=$1::jsonb WHERE tenant_id='bykoji' AND codigo='patio_guedala'",
                               json.dumps({'seg':[['19:00','23:00']],'qua':[['19:00','23:00']],'qui':[['19:00','23:00']],'sex':[['19:00','02:00']]}))
            uid=await conn.fetchval("SELECT id FROM tenant_unidades WHERE tenant_id='bykoji' AND codigo='patio_guedala'")
            await conn.execute("INSERT INTO unidade_excecoes(tenant_id,unidade_id,data,fechado) VALUES ('bykoji',$1,'2026-10-15',true)",uid)
            abre=lambda data,dia: {'unidade':'Pátio Guedala','codigo':'patio_guedala','dia_semana':dia,'data':data,'hora':'19:00'}
            # No passado: diz quando a unidade abre.
            r=await entrega(horario='hoje às 15h');assert motivo(r)=='horario_passado' and detalhes(r)['proxima_abertura']==abre('2026-10-12','segunda-feira') and detalhes(r)['aberta_agora'] is False
            # Antes de abrir, depois de fechar, dia fechado e exceção por data.
            r=await entrega(horario='hoje às 18h30');d=detalhes(r)
            assert (r.status_code,motivo(r))==(422,'horario_fora_funcionamento') and d['aberta_no_dia']==[['19:00','23:00']] and d['proxima_abertura']==abre('2026-10-12','segunda-feira')
            assert d['agendado_para']=='2026-10-12T18:30:00-03:00'
            r=await entrega(horario='hoje às 23h30');assert motivo(r)=='horario_fora_funcionamento' and detalhes(r)['proxima_abertura']==abre('2026-10-14','quarta-feira')
            r=await entrega(agendado_para='2026-10-13T20:00');assert motivo(r)=='horario_fora_funcionamento' and detalhes(r)['aberta_no_dia']==[] and detalhes(r)['proxima_abertura']==abre('2026-10-14','quarta-feira')
            r=await entrega(horario='dia 15/10 às 20h');assert motivo(r)=='horario_fora_funcionamento' and detalhes(r)['proxima_abertura']==abre('2026-10-16','sexta-feira')
            # Dentro do funcionamento; turno que cruza a meia-noite (sábado 01:00 ainda é o turno de sexta); só agendado_para escreve o texto.
            k=(await entrega(agendado_para='2026-10-14T19:30')).json()['carrinho'];assert (k['horario'],k['agendado_para'])==('quarta-feira às 19h30','2026-10-14T19:30:00-03:00')
            assert (await entrega(agendado_para='2026-10-17T01:00:00-03:00')).status_code==200 and motivo(await entrega(agendado_para='2026-10-17T02:30'))=='horario_fora_funcionamento'
            # Texto que não dá para entender, data inválida e mais de 7 dias.
            for ruim in ({'horario':'mais tarde'},{'horario':'à noite'},{'agendado_para':'amanhã'},{'agendado_para':'2026-10-14'}):
                r=await entrega(**ruim);assert (r.status_code,motivo(r))==(422,'horario_invalido'),ruim
            r=await entrega(agendado_para='2026-10-21T20:00');assert motivo(r)=='horario_muito_distante' and detalhes(r)['max_dias']==7
            assert (await carrinho()).json()['carrinho']['agendado_para']=='2026-10-17T01:00:00-03:00'       # nenhuma recusa mexeu no carrinho

            # O caso do piloto: válido ao marcar (17:56), vencido ao fechar (22:06).
            k=(await entrega(horario='hoje às 19h')).json()['carrinho'];assert (k['agendado_para'],k['horario_vencido'],k['pode_fechar'])==('2026-10-12T19:00:00-03:00',False,True)
            relogio[0]=AGORA.replace(hour=22,minute=6)
            k=(await carrinho()).json()['carrinho'];assert (k['horario'],k['horario_vencido'],k['pode_fechar'],k['faltando'])==('hoje às 19h',True,False,['horario'])
            repo=Repositorio(conn);cfg=await Configuracoes(repo).efetiva(T,A)
            p=await skyone.pacote(repo,T,c,10**9,'oi',cfg);kp=p['contexto_modulos']['pedidos']['carrinho']
            assert (kp['horario_vencido'],kp['agendado_para'],kp['pode_fechar'])==(True,'2026-10-12T19:00:00-03:00',False) and p['aviso_do_gateway'] is None
            r=await api('POST','/pedidos',json={'conversation_id':c});d=detalhes(r)
            assert (r.status_code,motivo(r))==(422,'horario_passado') and d['aberta_agora'] is True and d['proxima_abertura'] is None and r.json()['carrinho']['horario_vencido'] is True
            assert await conn.fetchval('SELECT count(*) FROM pedidos')==0          # nenhum pedido criado
            # Mudar só a modalidade não é barrado pelo horário vencido (o agente ainda vai perguntar o horário).
            assert (await entrega(modalidade='retirada')).status_code==200
            # Carrinho gravado antes do PED2 (só o texto): lido em relação ao dia em que foi escrito.
            await conn.execute("UPDATE carrinhos SET agendado_para=NULL,atualizado_em=$1 WHERE conversation_id=$2",AGORA,c)
            k=(await carrinho()).json()['carrinho'];assert (k['agendado_para'],k['horario_vencido'])==('2026-10-12T19:00:00-03:00',True)
            assert motivo(await api('POST','/pedidos',json={'conversation_id':c}))=='horario_passado'
            # Cliente escolhe outro horário: fecha, com o número na raiz e no pedido.
            k=(await entrega(horario='hoje às 22h30')).json()['carrinho'];assert (k['horario_vencido'],k['pode_fechar'],k['faltando'])==(False,True,[])
            r=await api('POST','/pedidos',json={'conversation_id':c});j=r.json()
            assert r.status_code==201 and j['numero'] and j['numero']==j['pedido']['numero'] and j['numero'].isdigit()
            assert j['pedido']['horario']=='hoje às 22h30' and datetime.fromisoformat(j['pedido']['agendado_para'])==AGORA.replace(hour=22,minute=30)
            assert (await client.get(f'/admin/v1/tenants/{T}/pedidos/{j["numero"]}')).json()['entrega']['agendado_para'].startswith('2026-10-1')
            # Fechado fora do funcionamento naquela hora (sem ter passado): horario_fora_funcionamento também no fechamento.
            c2=await conversa('cliente2')
            await api('POST',f'/carrinho/{c2}/itens',json={'item_id':ids['GUE-0001']});await api('PUT',f'/carrinho/{c2}/entrega',json={'modalidade':'retirada','horario':'quarta às 20h'})
            await conn.execute("INSERT INTO unidade_excecoes(tenant_id,unidade_id,data,fechado) VALUES ('bykoji',$1,'2026-10-14',true)",uid)
            r=await api('POST','/pedidos',json={'conversation_id':c2});assert (r.status_code,motivo(r))==(422,'horario_fora_funcionamento') and detalhes(r)['proxima_abertura']==abre('2026-10-16','sexta-feira')
            # Esvaziar limpa o agendamento junto com o resto.
            k=(await api('DELETE',f'/carrinho/{c2}')).json()['carrinho'];assert (k['horario'],k['agendado_para'],k['horario_vencido'])==(None,None,False)
            # Sem horário nenhum, o pedido fecha como sempre.
            await api('POST',f'/carrinho/{c2}/itens',json={'item_id':ids['GUE-0001']});await api('PUT',f'/carrinho/{c2}/entrega',json={'modalidade':'retirada'})
            j=(await api('POST','/pedidos',json={'conversation_id':c2})).json();assert j['numero']==j['pedido']['numero'] and j['pedido']['agendado_para'] is None
    banco(cenario)


def test_busca_com_categorias_e_registro_de_todas_as_chamadas():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,so_catalogo=await montar(conn,client,app)
            c=await conversa('cliente1')
            busca=lambda **p: api('GET','/catalogo/busca',params={'conversation_id':c,**p})
            for n in range(12):
                await conn.execute("""INSERT INTO catalogo_itens(tenant_id,codigo,unidade,categoria,nome,preco,tipo,delivery)
                    VALUES ('bykoji',$1,'patio_guedala',$2,$3,$4,'item','sim')""",f'TMK-{n:02d}','Temakis — cone' if n<9 else 'Temakis — hand roll',f'Temaki {n:02d}',20+n)
            # Sem os parâmetros novos, a busca de sempre.
            r=(await busca(q='edamame',unidade='patio_guedala')).json();assert set(r)=={'itens','data_referencia'} and r['itens'][0]['nome']=='Edamame' and r['itens'][0]['categoria']=='Entradas quentes'
            # Categorias da unidade: só o que pode ser pedido (com preço, ativo, não exclusivo do salão).
            r=(await busca(categorias='true',unidade='patio_guedala')).json()
            # Ordem do cardápio importado (DISP1): a categoria fica na posição do seu primeiro item.
            assert (r['unidade'],r['total_itens'],r['total_categorias'],r['pagina'],r['tem_mais'],r['tem_cardapio'])==('patio_guedala',16,6,None,False,True)
            assert r['categorias']==[{'categoria':'Entradas quentes','qtd_itens':1},{'categoria':'Pratos quentes','qtd_itens':1},{'categoria':'Cervejas','qtd_itens':1},
                {'categoria':'Carpaccios','qtd_itens':1},{'categoria':'Temakis — cone','qtd_itens':9},{'categoria':'Temakis — hand roll','qtd_itens':3}]
            assert (await busca(categorias='true',unidade='morumbi')).json()['categorias']==[{'categoria':'Sashimi','qtd_itens':1}]
            # Itens de uma categoria: sem acento e sem maiúsculas, 10 por página, sempre com preço.
            r=(await busca(categoria='ENTRADAS QUENTES',unidade='patio_guedala')).json()
            assert (r['categoria'],r['categorias_encontradas'],r['total'],r['pagina'],r['por_pagina'],r['tem_mais'])==('ENTRADAS QUENTES',['Entradas quentes'],1,1,10,False)
            assert [(i['nome'],i['preco'],i['item_id']) for i in r['itens']]==[('Edamame',34.0,ids['GUE-0001'])]
            r=(await busca(categoria='temaki',unidade='patio_guedala')).json()       # "temaki" acha as duas categorias de temaki
            assert (r['total'],r['tem_mais'],len(r['itens']),r['categorias_encontradas'])==(12,True,10,['Temakis — cone','Temakis — hand roll']) and all(i['preco'] is not None for i in r['itens'])
            r2=(await busca(categoria='temaki',unidade='patio_guedala',pagina=2)).json();assert (r2['pagina'],r2['tem_mais'],len(r2['itens']))==(2,False,2)
            assert not {i['item_id'] for i in r['itens']}&{i['item_id'] for i in r2['itens']}
            r=(await busca(categoria='Temakis — cone',unidade='patio_guedala',q='temaki 03')).json();assert [i['nome'] for i in r['itens']][0]=='Temaki 03'
            # Item sem preço ou só do salão não aparece; categoria que não existe devolve as parecidas.
            r=(await busca(categoria='Temakis',unidade='patio_guedala',q='toro')).json();assert r['itens']==[]
            r=(await busca(categoria='Cervejaa',unidade='patio_guedala')).json();assert (r['itens'],r['total'],r['sugestoes'])==([],0,['Cervejas'])
            assert (await busca(categoria='Sobremesas',unidade='patio_guedala')).json()['itens']==[]
            # Sem unidade: vale a do carrinho; sem carrinho, 400 dizendo o campo. Unidade que não existe: 404.
            r=await busca(categorias='true');assert r.status_code==400 and r.json()['erro']['detalhes']['campo']=='unidade'
            await api('POST',f'/carrinho/{c}/itens',json={'item_id':ids['GUE-0001']})
            assert (await busca(categorias='true')).json()['unidade']=='patio_guedala' and (await busca(categoria='cervejas')).json()['total']==1
            assert (await busca(categorias='true',unidade='nao_existe')).status_code==404 and (await busca(categoria='x',unidade='patio_guedala',pagina=0)).status_code==400
            # Escopo e isolamento: chave só de pedidos não busca; conversa de outra empresa não existe para esta chave.
            so_pedidos=next(v for k,v in (await client.post(f'/admin/v1/tenants/{T}/api-keys',json={'nome':'so-pedidos','escopos':['ferramentas:pedidos']})).json().items() if isinstance(v,str) and k in ('chave','api_key','valor','segredo','key'))
            assert (await api('GET','/catalogo/busca',chave=so_pedidos,params={'conversation_id':c,'categorias':'true','unidade':'patio_guedala'})).status_code==403
            assert (await api('GET','/catalogo/busca',params={'conversation_id':'outra:empresa:1','categorias':'true','unidade':'patio_guedala'})).status_code==404

            # Registro de todas as chamadas: consultas, sucesso, recusa de regra e recusa na entrada. Só nomes de campos.
            await conn.execute('DELETE FROM skill_registro')
            await busca(q='edamame',unidade='patio_guedala');await api('GET',f'/carrinho/{c}')
            await api('PUT',f'/carrinho/{c}/entrega',json={'modalidade':'delivery','endereco':'Rua Secreta, 99','horario':'mais tarde'})      # recusa de regra
            await api('PUT',f'/carrinho/{c}/entrega',json={'modalidade':'delivery','endereco':'Rua Secreta, 99','telefone':'5511999990000'})  # campo fora do contrato
            await api('POST',f'/carrinho/{c}/itens',json={'quantidade':1})                                                                    # falta item_id
            await api('POST','/pedidos',json={'conversation_id':'conversa:que:nao:existe'})                                                   # conversa inexistente
            await api('POST','/pedidos',json={'conversation_id':c})                                                                           # recusa: falta modalidade
            await api('GET','/pedidos',chave=so_catalogo,params={'conversation_id':c})                                                        # chave sem o escopo
            await api('GET','/unidades',params={'conversation_id':c})
            await client.get('/api/v1/catalogo/busca',params={'conversation_id':c},headers={'X-API-Key':'chave-que-nao-existe','Authorization':''})   # sem empresa: não entra
            rows=[dict(r) for r in await conn.fetch('SELECT skill,metodo,campos,status,motivo,conversation_id,duracao_ms FROM skill_registro ORDER BY id')]
            assert [(r['skill'],r['metodo'],r['campos'],r['status'],r['motivo']) for r in rows]==[
                ('buscar_catalogo','GET',['q','unidade'],200,'ok'),('ver_carrinho','GET',[],200,'ok'),
                ('definir_entrega','PUT',['endereco','horario','modalidade'],422,'horario_invalido'),
                ('definir_entrega','PUT',['endereco','modalidade','telefone'],400,'campo:corpo'),
                ('adicionar_item','POST',['quantidade'],400,'campo:item_id'),
                ('fechar_pedido','POST',[],404,'NOT_FOUND'),('fechar_pedido','POST',[],422,'falta_modalidade'),
                ('consultar_pedido','GET',[],403,'FORBIDDEN'),('info_unidades','GET',[],200,'ok')]
            assert rows[5]['conversation_id']=='conversa:que:nao:existe' and all(r['conversation_id']==c for n,r in enumerate(rows) if n!=5) and all(r['duracao_ms']>=0 for r in rows)
            tudo=json.dumps(rows,ensure_ascii=False,default=str);assert 'Rua Secreta' not in tudo and '5511999990000' not in tudo and 'mais tarde' not in tudo
            # Consulta pelo painel, com filtro por conversa e por Skill; outra empresa não vê.
            r=(await client.get(f'/admin/v1/tenants/{T}/skills/registro',params={'conversation_id':c,'skill':'definir_entrega'})).json()
            assert [(i['status'],i['motivo']) for i in r['itens']]==[(400,'campo:corpo'),(422,'horario_invalido')] and r['proximo_cursor'] is None
            assert (await client.get('/admin/v1/tenants/postman_teste/skills/registro')).status_code in (200,404)
            assert (await client.get('/admin/v1/tenants/nao_existe/skills/registro')).status_code==404
            # O registro curto das Skills que mudam carrinho ou pedido (usado no turno refeito) continua igual: consulta não entra nele.
            assert {r['skill'] for r in await conn.fetch('SELECT skill FROM skill_chamadas')}<={'adicionar_item','definir_entrega','fechar_pedido'}
    banco(cenario)


def test_pedido_anunciado_sem_fechar_refaz_com_aviso_e_segura_se_repetir(tmp_path,monkeypatch):
    async def cenario(pool):
        app,c,r,p,pref=await montar_fila(pool,True,tmp_path)
        pacotes=[];roteiro=[];alertas=[]
        async def agente(http,repo,cofre,tenant,cfg,payload):
            pacotes.append(payload);return await roteiro.pop(0)(payload)
        p.chamar=agente
        from nucleo import alertas as OPS
        async def avisar(self,nivel,titulo,detalhe=''): alertas.append((nivel,titulo,detalhe));return True
        monkeypatch.setattr(OPS.Alertas,'avisar',avisar)
        def fala(texto,antes=None,**campos):
            async def passo(payload):
                if antes: await antes(payload)
                return {'resposta':[texto],'acao':'nenhuma',**campos}
            return passo
        saidas=lambda conv: pool.fetch("SELECT conteudo,meta FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' AND origem='bot' ORDER BY id",T,conv)
        turnos=lambda conv: pool.fetch("SELECT status,anuncio_falso,refeito_de,acao,desfecho,resposta,id FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id",T,conv)
        quadro=lambda ts: [(t['status'],t['anuncio_falso']) for t in ts]
        try:
            async with c:
                conversa,ids,api,_=await montar(pool,c,app)
                async def rodar(conv): assert await p.processar_duravel(T,conv)=='concluido'
                async def chegar(conv,texto): await pool.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ($1,$2,'entrada','cliente',$3)",T,conv,texto)
                async def com_carrinho(usuario):
                    conv=await conversa(usuario)
                    assert (await api('POST',f'/carrinho/{conv}/itens',json={'item_id':ids['GUE-0001'],'quantidade':1})).status_code==200
                    assert (await api('PUT',f'/carrinho/{conv}/entrega',json={'modalidade':'retirada'})).status_code==200
                    return conv
                async def fecha(payload):
                    r=await api('POST','/pedidos',json={'conversation_id':payload['conversation_id']});assert r.status_code==201,r.text
                pedidos=lambda conv: pool.fetchval('SELECT count(*) FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
                FALSA='Acabei de fechar seu pedido para retirada hoje às 19h no Pátio Guedala 😊'

                # (a) Anunciou sem fechar → turno refeito com o aviso no pacote; na segunda vez o agente fecha de verdade e a resposta sai.
                c1=await com_carrinho('a1')
                roteiro[:]=[fala(FALSA,desfecho='pedido_feito'),fala('Agora sim: pedido enviado para a unidade!',fecha,acao='pedido_fechado')]
                await rodar(c1)
                assert len(pacotes)==2 and pacotes[0]['aviso_do_gateway'] is None and pacotes[1]['aviso_do_gateway']==AN.AVISO_AO_AGENTE
                assert 'fechar_pedido não foi chamado com sucesso' in pacotes[1]['aviso_do_gateway']
                assert [m['conteudo'] for m in await saidas(c1)]==['Agora sim: pedido enviado para a unidade!'] and await pedidos(c1)==1
                ts=await turnos(c1);assert quadro(ts)==[('refeito','refeito'),('ok',None)] and ts[1]['refeito_de']==ts[0]['id'] and not alertas
                # Depois de um pedido real, "obrigado" com desfecho pedido_feito não é anúncio falso.
                pacotes.clear();await chegar(c1,'Obrigado!')
                roteiro[:]=[fala('Por nada! Seu pedido já foi registrado.',desfecho='pedido_feito')]
                await rodar(c1);assert len(pacotes)==1 and [m['conteudo'] for m in await saidas(c1)][-1]=='Por nada! Seu pedido já foi registrado.'
                # Citar o número de um pedido que existe também não dispara, mesmo com carrinho novo em aberto.
                numero=await pool.fetchval('SELECT numero FROM pedidos WHERE conversation_id=$1',c1)
                await api('POST',f'/carrinho/{c1}/itens',json={'item_id':ids['GUE-0002']});pacotes.clear();await chegar(c1,'meu pedido de antes foi?')
                roteiro[:]=[fala(f'Sim! O pedido {numero} foi enviado para a unidade.')]
                await rodar(c1);assert len(pacotes)==1 and (await turnos(c1))[-1]['anuncio_falso'] is None

                # (b) Repetiu o anúncio no turno refeito → o texto do agente NÃO sai; vai o texto fixo com o botão, e a operação é avisada.
                pacotes.clear();c2=await com_carrinho('b1')
                roteiro[:]=[fala(FALSA,desfecho='pedido_feito'),fala('Seu pedido está confirmado! 🎉',desfecho='pedido_feito')]
                await rodar(c2)
                enviadas=await saidas(c2);assert len(pacotes)==2 and len(enviadas)==1 and await pedidos(c2)==0
                assert enviadas[0]['conteudo'].startswith(AN.TEXTO_AO_CLIENTE) and 'Confirmar pedido' in enviadas[0]['conteudo'] and 'confirmado' not in enviadas[0]['conteudo']
                assert [o['id'] for o in json.loads(enviadas[0]['meta'])['botoes']['opcoes']]==['confirmar_pedido']
                ts=await turnos(c2);assert quadro(ts)==[('refeito','refeito'),('ok','segurado')] and (ts[1]['acao'],ts[1]['desfecho'])==('nenhuma',None)
                guardado=json.loads(ts[1]['resposta'])['segurado'];assert guardado=={'resposta':['Seu pedido está confirmado! 🎉'],'acao':'nenhuma','desfecho':'pedido_feito','motivo':None}
                assert len(alertas)==1 and alertas[0][1]=='pedido anunciado sem fechar' and 'bykoji' in alertas[0][2] and c2 not in alertas[0][2]
                assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='pedido.anuncio_segurado' AND alvo=$2",T,c2)==1
                # O cliente toca em Confirmar: turno comum; o agente fecha e a resposta sai sem aviso nenhum.
                pacotes.clear();await chegar(c2,'Confirmar pedido')
                roteiro[:]=[fala('Pedido enviado para a unidade!',fecha,acao='pedido_fechado')]
                await rodar(c2);assert len(pacotes)==1 and pacotes[0]['aviso_do_gateway'] is None and await pedidos(c2)==1 and (await turnos(c2))[-1]['anuncio_falso'] is None

                # Só pelo texto, com número de pedido inventado e sem carrinho nenhum: também é segurado.
                pacotes.clear();c3=await conversa('c1')
                roteiro[:]=[fala('Pedido nº 99999 registrado!'),fala('O número do seu pedido é 99999.')]
                await rodar(c3);assert quadro(await turnos(c3))==[('refeito','refeito'),('ok','segurado')] and len(alertas)==2
                # No turno refeito o agente explica o que falta em vez de anunciar: a resposta dele sai.
                pacotes.clear();c4=await com_carrinho('d1')
                roteiro[:]=[fala(FALSA),fala('Ainda não consegui fechar: preciso que você confirme o horário de retirada.')]
                await rodar(c4);assert quadro(await turnos(c4))==[('refeito','refeito'),('ok',None)] and [m['conteudo'] for m in await saidas(c4)]==['Ainda não consegui fechar: preciso que você confirme o horário de retirada.']

                # Falso positivo que não pode acontecer: "vou fechar seu pedido" antes de confirmar passa direto, sem refazer.
                pacotes.clear();c5=await com_carrinho('e1')
                roteiro[:]=[fala('Vou fechar seu pedido: 1 Edamame para retirada. Posso confirmar?')]
                await rodar(c5)
                assert len(pacotes)==1 and quadro(await turnos(c5))==[('ok',None)] and [m['conteudo'] for m in await saidas(c5)]==['Vou fechar seu pedido: 1 Edamame para retirada. Posso confirmar?']
                # Fechou de verdade no mesmo turno: nada é refeito nem segurado.
                pacotes.clear();c6=await com_carrinho('f1')
                roteiro[:]=[fala('Pedido feito! A unidade vai confirmar.',fecha,acao='pedido_fechado',desfecho='pedido_feito')]
                await rodar(c6);assert len(pacotes)==1 and quadro(await turnos(c6))==[('ok',None)] and len(alertas)==2

                m=(await c.get(f'/admin/v1/tenants/{T}/metricas')).json()
                assert (m['anuncios_falsos_refeitos'],m['anuncios_falsos_segurados'])==(4,2)
        finally:
            await limpar(r,pref)
    banco_real(cenario)
