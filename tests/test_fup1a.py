"""FUP1-a: follow-up — pendência declarada, agendamento, cancelamento, opt-out e vencimento em ensaio (docs/ESPEC_FUP1A.md).

Turnos completos pela fila durável com o agente simulado (cenário do By Koji de tests/test_enc1a.py). O relógio do vencimento é
controlado: `follow_up.vencer(..., agora=...)`."""
import json
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import httpx
import pytest
from test_migrations import DSN,banco
from test_enc1a import cenario_by_koji,T,A,H

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
DIA_TODO={d:[['00:00','23:59']] for d in ('seg','ter','qua','qui','sex','sab','dom')}
SP=ZoneInfo('America/Sao_Paulo')


def test_pendencia_do_retorno_lista_fechada_e_pergunta_sem_dado_pessoal():
    from nucleo import follow_up as F
    assert F.do_retorno(None) is None and F.do_retorno('confirmar') is None and F.do_retorno(['x']) is None
    assert F.do_retorno({'tipo':'confirmar_pedido','pergunta':'Confirma este resumo?'})=={'tipo':'confirmar_pedido','pergunta':'Confirma este resumo?'}
    assert F.do_retorno({'tipo':'escolher_item'})=={'tipo':'escolher_item','pergunta':None}
    # Tipo fora da lista: grava "outro" e avisa.
    assert F.do_retorno({'tipo':'pagar_agora','pergunta':'Ok?'})=={'tipo':'outro','pergunta':'Ok?','aviso':'tipo_fora_da_lista'}
    assert F.do_retorno({'tipo':7})['tipo']=='outro'
    limpa=F.limpar_pergunta
    assert limpa('Confirmo no (11) 90000-1234 ou no +55 11 900001234?')=='Confirmo no [número] ou no [número]?'
    assert limpa('Mando para ana.paula@exemplo.com.br?')=='Mando para [email]?'
    assert limpa('CPF 123.456.789-09 ou CNPJ 11.222.333/0001-81 na nota?')=='CPF [documento] ou CNPJ [documento] na nota?'
    assert limpa('Entrego no CEP 04567-000?')=='Entrego no CEP [cep]?'
    assert limpa('Entrego na Rua das Flores, 123?')=='Entrego na [endereço]?'
    assert limpa('Entrego na Av. Paulista 1000, apto 5?')=='Entrego na [endereço], apto 5?'
    assert limpa('Ana Paula, confirma 2 temakis por R$ 68,00?',['Ana Paula'])=='[nome] [nome], confirma 2 temakis por R$ 68,00?'
    assert limpa('Quantos?\n\x00 ')=='Quantos?' and limpa('   ') is None and limpa(None) is None
    longa=limpa('Confirma '+'temaki '*40+'?')
    assert len(longa)==120 and longa.endswith('…')
    assert set(F.TIPOS)=={'confirmar_pedido','escolher_item','informar_quantidade','informar_horario','informar_endereco','escolher_unidade',
                          'informar_modalidade','outro'}


def test_frases_de_opt_out_que_contam_e_que_nao_contam():
    from nucleo.follow_up import e_opt_out
    for frase in ('pare','PARE!','Parar','para','chega','Não quero mais mensagens','nao quero mais mensagens.','Para de mandar mensagem',
                  'pare de me mandar mensagens, por favor','por favor, pare','Não me mande mais mensagens 🙏','stop'):
        assert e_opt_out(frase),frase
    for frase in ('não quero mais esse item','para de mandar o molho','pare de colocar cebola','quero parar de pedir refrigerante',
                  'oi','não quero','não quero mais','obrigado','pare no portão 2','para quando fica pronto?','chega que horas?'):
        assert not e_opt_out(frase),frase


def test_retorno_sem_os_campos_novos_nao_quebra_e_com_eles_e_lido(monkeypatch):
    """Skyone ainda não atualizado: sem `pendencia` e sem `opt_out_follow_up` o resultado é o de sempre. Com eles: pendência limpa
    (o nome do cliente do pacote sai da pergunta) e opt-out só com true."""
    from agentes import skyone
    from test_skyone_adaptador import preparar_agente
    async def validar(url,dominios): pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',validar)
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'Oi',cfg)
        assert body['recursos']['follow_up']=={'ativo':False,'escopo':'pedido_em_andamento'} and body['cliente']['opt_out_follow_up'] is False
        body['cliente']['nome']='Marina'
        casos=(({},{}),
               ({'pendencia':{'tipo':'confirmar_pedido','pergunta':'Marina, confirma?'},'opt_out_follow_up':True},
                {'pendencia':{'tipo':'confirmar_pedido','pergunta':'[nome], confirma?'},'opt_out_follow_up':True}),
               ({'pendencia':'sim','opt_out_follow_up':'true'},{}),
               ({'pendencia':{'tipo':'xyz'}},{'pendencia':{'tipo':'outro','pergunta':None,'aviso':'tipo_fora_da_lista'}}))
        for extra,esperado in casos:
            def responder(req):
                p=json.loads(req.content)
                return httpx.Response(200,json={'resposta':'Ok','acao':'nenhuma','status':'success','tipo':'text',
                                                'request_id':p['request_id'],'conversation_id':p['conversation_id'],**extra})
            async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
                r=await skyone.chamar(client,repo,cofre,'bykoji',cfg,body)
            assert r=={'resposta':['Ok'],'acao':'nenhuma',**esperado},extra
    banco(cenario)


def _ligar(**extra):
    return {'follow_up_ativo':'on','follow_up_janela':DIA_TODO,**extra}


def kit_follow_up(pool,c,p,api,ids,turno,fala):
    """Passos do follow-up sobre o cenário do By Koji."""
    async def configurar(**campos):
        r=await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json=campos);assert r.status_code==200,r.text

    async def com_item(cli,texto='quero 2 edamame',**retorno):
        """Turno que põe um item no carrinho e devolve a resposta com os campos pedidos."""
        async def adicionar(payload):
            assert (await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})).status_code==200
        return await turno(cli,texto,fala('Anotei 2 edamames. Confirma?',adicionar,**retorno))

    async def tarefas(conv):
        return [dict(r) for r in await pool.fetch('SELECT * FROM follow_up_tarefas WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id',T,conv)]

    async def ultimo_turno(conv):
        r=await pool.fetchval('SELECT pendencia FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1',T,conv)
        return json.loads(r) if isinstance(r,str) else r

    async def vencer(agora=None):
        from nucleo import follow_up
        return await follow_up.vencer(pool,p.cfg,agora=agora)

    return configurar,com_item,tarefas,ultimo_turno,vencer


PEND={'pendencia':{'tipo':'confirmar_pedido','pergunta':'Confirma 2 edamames?'}}


def test_arma_so_com_pendencia_valida_e_vence_em_ensaio(tmp_path):
    async def corpo(pool,c,p,api,ids,turno,fala,pacotes,**_):
        configurar,com_item,tarefas,ultimo_turno,vencer=kit_follow_up(pool,c,p,api,ids,turno,fala)
        # Desligado (padrão): a pendência fica registrada no turno, nada é agendado; o pacote diz que está desligado.
        conv=await com_item('5511900000301',**PEND)
        assert pacotes[-1]['recursos']['follow_up']=={'ativo':False,'escopo':'pedido_em_andamento'}
        assert await tarefas(conv)==[] and (await ultimo_turno(conv))['follow_up']=='desligado'
        await configurar(**_ligar())
        # Skyone sem o campo novo: nada arma, nada quebra.
        await com_item('5511900000301','e mais um')
        assert await tarefas(conv)==[] and await ultimo_turno(conv) is None
        # Pendência válida, resposta inteira enviada: arma o 1º follow-up para daqui a 15 min.
        await com_item('5511900000301','mais 2',**PEND)
        assert pacotes[-1]['recursos']['follow_up']=={'ativo':True,'escopo':'pedido_em_andamento'}
        [t]=await tarefas(conv)
        assert (t['estado'],t['ordinal'],json.loads(t['pendencia']))==('agendado',1,{'tipo':'confirmar_pedido','pergunta':'Confirma 2 edamames?'})
        assert timedelta(minutes=14)<t['previsto_em']-t['criado_em']<=timedelta(minutes=15,seconds=5)
        assert await ultimo_turno(conv)=={'tipo':'confirmar_pedido','pergunta':'Confirma 2 edamames?','follow_up':'agendado'}
        # Antes da hora: nada. Na hora: ensaio (registra que enviaria; nada sai).
        assert await vencer()=={}
        enviadas=await pool.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida'",T,conv)
        assert await vencer(t['previsto_em']+timedelta(seconds=1))=={'ensaio_enviaria':1}
        [t]=await tarefas(conv)
        assert (t['estado'],t['motivo'])==('ensaio_enviaria',None)
        assert await pool.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida'",T,conv)==enviadas
        acoes=[r['acao'] for r in await pool.fetch("SELECT acao FROM audit_log WHERE tenant_id=$1 AND acao LIKE 'follow_up.%' ORDER BY id",T)]
        assert acoes==['follow_up.agendado','follow_up.ensaio']
    cenario_by_koji(tmp_path,corpo)


def test_nao_arma_sem_condicoes(tmp_path):
    async def corpo(pool,c,p,api,ids,turno,fala,**_):
        configurar,com_item,tarefas,ultimo_turno,vencer=kit_follow_up(pool,c,p,api,ids,turno,fala)
        await configurar(**_ligar())
        # Carrinho vazio no escopo "pedido em andamento".
        conv=await turno('5511900000311','oi',fala('Olá! O que vai querer?',**PEND))
        assert (await ultimo_turno(conv))['follow_up']=='pendencia_invalida' and await tarefas(conv)==[]
        # Tipo "outro" (ou fora da lista) só vale no escopo "pergunta pendente".
        # (o agente simulado devolve já como o adaptador entrega: tipo fora da lista vira "outro" com aviso)
        await com_item('5511900000311',pendencia={'tipo':'outro','pergunta':'Ok?','aviso':'tipo_fora_da_lista'})
        assert (await ultimo_turno(conv))=={'tipo':'outro','pergunta':'Ok?','aviso':'tipo_fora_da_lista','follow_up':'tipo_fora_do_escopo'}
        assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='follow_up.tipo_fora_da_lista'",T)==1
        # Ação diferente de "nenhuma" (transferência) não arma.
        conv2=await com_item('5511900000312',acao='transferir_humano',motivo='pediu_humano',**PEND)
        assert (await ultimo_turno(conv2))['follow_up']=='turno' and await tarefas(conv2)==[]
        # Limite do dia 0: não arma.
        await configurar(follow_up_limite_dia=0)
        conv3=await com_item('5511900000313',**PEND)
        assert (await ultimo_turno(conv3))['follow_up']=='limite_do_dia'
        # Máximo 0 = desligado.
        await configurar(follow_up_limite_dia=2,follow_up_maximo=0)
        await com_item('5511900000313','mais',**PEND)
        assert (await ultimo_turno(conv3))['follow_up']=='desligado'
        # Opt-out pedido pelo agente: registrado; a pendência do mesmo turno não arma.
        await configurar(follow_up_maximo=2)
        conv4=await com_item('5511900000314',opt_out_follow_up=True,**PEND)
        assert (await ultimo_turno(conv4))['follow_up']=='opt_out' and await tarefas(conv4)==[]
        contato=await pool.fetchval('SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv4)
        assert await pool.fetchval('SELECT origem FROM follow_up_opt_out WHERE tenant_id=$1 AND contato_id=$2 AND removido_em IS NULL',T,contato)=='agente'
        # Escopo "pergunta pendente": "outro" arma, mesmo sem carrinho.
        await configurar(follow_up_escopo='pergunta_pendente')
        conv5=await turno('5511900000315','vocês abrem amanhã?',fala('Abrimos às 11h. Quer reservar?',pendencia={'tipo':'outro','pergunta':'Quer reservar?'}))
        assert [t['estado'] for t in await tarefas(conv5)]==['agendado']
        # Agente sem módulo que confirme a pendência: o escopo "pedido em andamento" nunca vale.
        from nucleo import follow_up
        assert not await follow_up._pendencia_valida(pool,T,conv5,None,'confirmar_pedido',{'modulos':[]})
    cenario_by_koji(tmp_path,corpo)


def test_cancela_por_cada_motivo(tmp_path):
    async def corpo(pool,c,p,api,ids,turno,fala,enviar,**_):
        configurar,com_item,tarefas,ultimo_turno,vencer=kit_follow_up(pool,c,p,api,ids,turno,fala)
        await configurar(**_ligar())
        async def motivo(conv):
            ts=await tarefas(conv);assert ts and ts[-1]['estado']=='cancelado',ts
            return ts[-1]['motivo']
        # Cliente respondeu (qualquer mensagem, inclusive reação): cancela na recepção, antes do turno.
        conv=await com_item('5511900000321',**PEND)
        await enviar('5511900000321','sim')
        assert await motivo(conv)=='cliente_respondeu'
        # "pare" sozinho: opt-out pela frase.
        conv2=await com_item('5511900000322',**PEND)
        await enviar('5511900000322','Pare!')
        assert await motivo(conv2)=='opt_out'
        contato=await pool.fetchval('SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv2)
        assert await pool.fetchval('SELECT origem FROM follow_up_opt_out WHERE tenant_id=$1 AND contato_id=$2',T,contato)=='frase'
        # Atendente assumiu pelo painel.
        conv3=await com_item('5511900000323',**PEND)
        q=httpx.URL(f'/admin/v1/tenants/{T}/conversas/{conv3}').path
        assert (await c.post(q+'/assumir',json={'minutos':30},headers=H)).status_code==200
        assert await motivo(conv3)=='com_atendente'
        # Encerrado pelo painel.
        conv4=await com_item('5511900000324',**PEND)
        q=httpx.URL(f'/admin/v1/tenants/{T}/conversas/{conv4}').path
        assert (await c.post(q+'/encerrar',json={'motivo':'manual'},headers=H)).status_code==200
        assert await motivo(conv4)=='encerrado'
        # Pedido fechado (fora de um turno, pela API).
        conv5=await com_item('5511900000325')
        async def entrega(payload):
            assert (await api('PUT',f'/carrinho/{payload["conversation_id"]}/entrega',json={'unidade':'patio_guedala','modalidade':'retirada'})).status_code==200
        await turno('5511900000325','retiro no pátio',fala('Total R$ 68,00. Posso fechar?',entrega,**PEND))
        assert [t['estado'] for t in await tarefas(conv5)]==['agendado']
        assert (await api('POST','/pedidos',json={'conversation_id':conv5})).status_code==201
        assert await motivo(conv5)=='pedido_fechado'
        # Follow-up desligado no agente: o agendador cancela no ciclo seguinte.
        conv6=await com_item('5511900000326',**PEND)
        await configurar(follow_up_ativo='off')
        from nucleo import follow_up
        assert await follow_up.cancelar_desligados(pool,p.cfg)==1
        assert await motivo(conv6)=='desligado'
        eventos=[r['acao'] for r in await pool.fetch("SELECT acao FROM audit_log WHERE tenant_id=$1 AND acao='follow_up.cancelado'",T)]
        assert len(eventos)==6
    cenario_by_koji(tmp_path,corpo)


def test_vencimento_revalida_janela_fuso_adiar_idade_e_corrida(tmp_path):
    async def corpo(pool,c,p,api,ids,turno,fala,enviar,**_):
        configurar,com_item,tarefas,ultimo_turno,vencer=kit_follow_up(pool,c,p,api,ids,turno,fala)
        await configurar(**_ligar(follow_up_janela={d:[['09:00','22:00']] for d in ('seg','ter','qua','qui','sex','sab','dom')}))
        async def armado(cli,quando):
            """Arma e reescreve a criação para 20 min antes de `quando` (relógio controlado)."""
            conv=await com_item(cli,**PEND)
            await pool.execute("UPDATE conversas SET unidade_preferida='patio_guedala' WHERE tenant_id=$1 AND conversation_id=$2",T,conv)
            await pool.execute('UPDATE mensagens SET criado_em=$3 WHERE tenant_id=$1 AND conversation_id=$2',T,conv,quando-timedelta(minutes=25))
            await pool.execute("""UPDATE follow_up_tarefas SET criado_em=$3,previsto_em=$4 WHERE tenant_id=$1 AND conversation_id=$2""",
                               T,conv,quando-timedelta(minutes=20),quando-timedelta(minutes=5))
            return conv
        async def estado(conv):
            t=(await tarefas(conv))[-1];return t['estado'],t['motivo'],t['previsto_em']
        # Fora da janela (23:30 em São Paulo) → cancela.
        noite=datetime(2026,10,21,23,30,tzinfo=SP)
        conv=await armado('5511900000331',noite)
        await vencer(noite)
        assert (await estado(conv))[:2]==('cancelado','fora_da_janela')
        # Dentro (21:30 em São Paulo) → ensaio.
        conv=await armado('5511900000332',datetime(2026,10,21,21,30,tzinfo=SP))
        await vencer(datetime(2026,10,21,21,30,tzinfo=SP))
        assert (await estado(conv))[:2]==('ensaio_enviaria',None)
        # Fuso da unidade: 22:30 em São Paulo são 21:30 em Manaus → dentro da janela da unidade.
        await pool.execute("UPDATE tenant_unidades SET fuso='America/Manaus' WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        quando=datetime(2026,10,21,22,30,tzinfo=SP)
        conv=await armado('5511900000333',quando)
        await vencer(quando)
        assert (await estado(conv))[:2]==('ensaio_enviaria',None)
        await pool.execute("UPDATE tenant_unidades SET fuso=NULL WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        # Janela que cruza a meia-noite (20:00–02:00): 01:00 está dentro.
        await configurar(follow_up_janela={d:[['20:00','02:00']] for d in ('seg','ter','qua','qui','sex','sab','dom')})
        madrugada=datetime(2026,10,22,1,0,tzinfo=SP)
        conv=await armado('5511900000334',madrugada)
        await vencer(madrugada)
        assert (await estado(conv))[:2]==('ensaio_enviaria',None)
        # Unidade fechada (pausada) dentro da janela → cancela.
        await configurar(follow_up_janela=DIA_TODO)
        await pool.execute("UPDATE tenant_unidades SET pausa_ate=$2 WHERE tenant_id=$1 AND codigo='patio_guedala'",T,datetime(2026,10,23,12,0,tzinfo=SP))
        quando=datetime(2026,10,21,15,0,tzinfo=SP)
        conv=await armado('5511900000335',quando)
        await vencer(quando)
        assert (await estado(conv))[:2]==('cancelado','fora_da_janela')
        await pool.execute("UPDATE tenant_unidades SET pausa_ate=NULL WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        # adiar_abertura: fora da janela às 23:30 → adia para as 09:00 do dia seguinte (dentro de 24 h) e segue agendado.
        await configurar(follow_up_fora_unidade='adiar_abertura',follow_up_janela={d:[['09:00','22:00']] for d in ('seg','ter','qua','qui','sex','sab','dom')})
        conv=await armado('5511900000336',noite)
        await vencer(noite)
        assert await estado(conv)==('agendado',None,datetime(2026,10,22,9,0,tzinfo=SP))
        assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='follow_up.adiado'",T)==1
        # Idade máxima: pendência de mais de 24 h não vale.
        tarde=datetime(2026,10,22,15,0,tzinfo=SP)
        conv=await armado('5511900000337',tarde)
        await pool.execute("UPDATE follow_up_tarefas SET criado_em=$3 WHERE tenant_id=$1 AND conversation_id=$2",T,conv,tarde-timedelta(hours=25))
        await vencer(tarde)
        assert (await estado(conv))[:2]==('cancelado','pendencia_expirada')
        # Corrida: a mensagem do cliente chega antes do vencimento ser conferido → vence a mensagem.
        conv=await armado('5511900000338',tarde)
        await enviar('5511900000338','oi, ainda tá aí?')
        assert await vencer(tarde)=={} and (await estado(conv))[:2]==('cancelado','cliente_respondeu')
        # Limite do dia: o 3º do mesmo contato no dia não sai (2 por padrão, somando agentes).
        await configurar(follow_up_fora_unidade='cancelar',follow_up_janela=DIA_TODO)
        cli='5511900000339'
        for n in range(3):
            conv=await com_item(cli,f'pedido {n}',**PEND)
            t=(await tarefas(conv))[-1]
            r=await vencer(t['previsto_em']+timedelta(seconds=1))
            if n<2: assert r=={'ensaio_enviaria':1},(n,r)
        assert (await ultimo_turno(conv))['follow_up']=='limite_do_dia'
    cenario_by_koji(tmp_path,corpo)


def test_painel_lista_cancela_e_opt_out(tmp_path):
    async def corpo(pool,c,p,api,ids,turno,fala,enviar,**_):
        configurar,com_item,tarefas,ultimo_turno,vencer=kit_follow_up(pool,c,p,api,ids,turno,fala)
        await configurar(**_ligar())
        conv=await com_item('5511900000341',**PEND)
        q=httpx.URL(f'/admin/v1/tenants/{T}/conversas/{conv}').path
        # Lista de conversas: follow_up agendado; detalhe com a pergunta.
        lista=(await c.get(f'/admin/v1/tenants/{T}/conversas',headers=H)).json()['itens']
        item=next(i for i in lista if i['conversation_id']==conv)
        assert item['follow_up']['estado']=='agendado' and item['follow_up']['previsto_em']
        r=(await c.get(q+'/follow-ups',headers={'X-Role':'membro'})).json()
        assert [(i['tipo'],i['pergunta'],i['estado']) for i in r['itens']]==[('confirmar_pedido','Confirma 2 edamames?','agendado')]
        assert r['opt_out']['ativo'] is False
        # Supervisor cancela (auditado com o actor); funcionário não (PAPEL1-a).
        assert (await c.post(q+'/follow-ups/cancelar',json={},headers={'X-Role':'membro'})).status_code==403
        r=await c.post(q+'/follow-ups/cancelar',json={},headers={'X-Role':'supervisor'})
        assert r.status_code==200 and r.json()=={'ok':True,'cancelados':1}
        assert (await tarefas(conv))[-1]['motivo']=='painel'
        assert await pool.fetchval("SELECT actor FROM audit_log WHERE tenant_id=$1 AND acao='follow_up.cancelado' ORDER BY id DESC LIMIT 1",T)=='teste'
        assert (await c.post(q+'/follow-ups/cancelar',json={'motivo':'Não!'},headers=H)).status_code==400
        lista=(await c.get(f'/admin/v1/tenants/{T}/conversas',headers=H)).json()['itens']
        assert next(i for i in lista if i['conversation_id']==conv)['follow_up'] is None
        # Opt-out: supervisor registra (membro não, PAPEL1-a), supervisor não reativa, admin reativa; resposta do cliente nunca reativa.
        contato=await pool.fetchval('SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
        o=f'/admin/v1/tenants/{T}/contatos/{contato}/opt-out-follow-up'
        assert (await c.put(o,headers={'X-Role':'membro'})).status_code==403
        r=await c.put(o,headers={'X-Role':'supervisor'});assert r.status_code==200 and r.json()['ativo'] and r.json()['origem']=='painel'
        await enviar('5511900000341','oi')
        assert (await c.get(o,headers=H)).json()['ativo'] is True
        motivo={'motivo':'cliente pediu para voltar'}
        assert (await c.request('DELETE',o,json=motivo,headers={'X-Role':'membro'})).status_code==403
        r=await c.request('DELETE',o,json=motivo,headers={'X-Role':'admin'})
        assert r.status_code==200 and r.json()['ativo'] is False and r.json()['removido_por']=='teste' and r.json()['removido_motivo']=='cliente pediu para voltar'
        assert (await c.request('DELETE',o,json=motivo,headers=H)).status_code==404
        assert (await c.get(f'/admin/v1/tenants/{T}/contatos/ct_inexistente/opt-out-follow-up',headers=H)).status_code==404
        acoes=[r['acao'] for r in await pool.fetch("SELECT acao FROM audit_log WHERE tenant_id=$1 AND acao LIKE 'follow_up.opt%' ORDER BY id",T)]
        assert acoes==['follow_up.opt_out','follow_up.opt_out_removido']
    cenario_by_koji(tmp_path,corpo)


def test_anonimizacao_cancela_e_apaga_a_pergunta(tmp_path):
    async def corpo(pool,c,p,api,ids,turno,fala,**_):
        configurar,com_item,tarefas,ultimo_turno,vencer=kit_follow_up(pool,c,p,api,ids,turno,fala)
        await configurar(**_ligar())
        cli='5511900000351'
        conv=await com_item(cli,**PEND)
        contato=await pool.fetchval('SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
        r=await c.request('DELETE',f'/admin/v1/tenants/{T}/contatos/{contato}',json={'confirmacao':cli},headers=H)
        assert r.status_code==200,r.text
        [t]=[dict(x) for x in await pool.fetch('SELECT * FROM follow_up_tarefas WHERE tenant_id=$1 AND contato_id=$2',T,contato)]
        assert (t['estado'],t['motivo'],json.loads(t['pendencia']))==('cancelado','anonimizado',{'tipo':'confirmar_pedido','pergunta':None})
        assert cli not in t['conversation_id']
        assert await pool.fetchval('SELECT count(*) FROM turnos WHERE tenant_id=$1 AND pendencia IS NOT NULL',T)==0
    cenario_by_koji(tmp_path,corpo)
