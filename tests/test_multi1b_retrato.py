"""MULTI1-b: retrato do By Koji em cenários fixos, gravado ANTES de tirar o delivery do núcleo.

Cada cenário roda turnos completos pela fila durável com o agente simulado chamando as Skills como o Skyone chamaria:
saudação, cardápio, adicionar item, resumo, fechar pedido (com a decisão da unidade pelo WhatsApp interno), transferência,
fora do horário, e os casos que passam pelos pontos do núcleo que mudam de lugar (turno refeito com ações já executadas,
pedido anunciado sem fechar, transferência sem recusa, conversão em aviso à equipe, carrinho expirado, alerta de
transbordo, despacho de avisos, conversas e métricas do painel, anonimização).

O resultado (pacotes, respostas das Skills, mensagens, turnos, avisos, alertas, telas do painel) é comparado campo a campo
com tests/fixtures/multi1b_retrato.json. Para gravar de novo (só quando a mudança de comportamento for decidida):
RETRATO_SAIDA=<arquivo> pytest tests/test_multi1b_retrato.py"""
import json,os,re
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from test_migrations import DSN
from test_fase4_pedidos import montar
from test_fila import banco_real,montar as montar_fila,limpar

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
T,A='bykoji','bykoji_atendimento'
SP=ZoneInfo('America/Sao_Paulo')
# Terça-feira bem no futuro: o relógio do gateway é fixo; o do banco (now()) não é, e por isso sai do retrato.
DIA='2030-11-05'
RETRATO=Path(__file__).parent/'fixtures'/'multi1b_retrato.json'
UNIDADE_WHATS='5511900000091'
ALERTA_EQUIPE='5511900000092'
UUID=re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
HEX=re.compile(r'(anon|ct|ag|can|key|wh|uni|sol|at)_[0-9a-f]{12,32}')
ISO=re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}')
ISO_NO_TEXTO=re.compile(r'(?<!\d)(?!2030-)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00')
VOLATEIS={'request_id','session_id','ha_segundos','atualizado_ha_segundos','tempo_desde_ultima','retomada','criado_em','atualizado_em',
          'enviada_em','enviado_em','expirado_em','decidido_em','visto_em','duracao_ms','ms','ultima_em'}


def normalizar(v,chave=None):
    """Tira o que muda de uma execução para outra (UUID, hora do banco, ids aleatórios); o resto fica como veio."""
    if v is not None and (chave in VOLATEIS or (chave or '').endswith('_ms')): return '<variavel>'
    if isinstance(v,dict): return {k:normalizar(x,k) for k,x in sorted(v.items())}
    if isinstance(v,(list,tuple)): return [normalizar(x) for x in v]
    if isinstance(v,datetime): v=v.isoformat()
    if isinstance(v,str):
        v=UUID.sub('<uuid>',v);v=HEX.sub(lambda m: m.group(1)+'_<id>',v)
        v=ISO_NO_TEXTO.sub('<agora-do-banco>',v)
        if ISO.match(v) and not v.startswith(DIA[:4]): return '<agora-do-banco>'
    return v


def relogio_fixo(monkeypatch,quando):
    """Fixa o relógio do gateway (pacote, unidades, calendário, horário do pedido)."""
    from agentes import skyone
    from nucleo import expediente,horarios,calendario
    from modulos.pedidos import agendamento
    import modulos.unidades as MU,admin.unidades as AU
    class Relogio(datetime):
        @classmethod
        def now(cls,tz=None): return quando[0].astimezone(tz or timezone.utc)
    for modulo in (skyone,expediente,horarios,calendario,MU,AU):
        if hasattr(modulo,'datetime'): monkeypatch.setattr(modulo,'datetime',Relogio)
    monkeypatch.setattr(agendamento,'agora_utc',lambda: quando[0].astimezone(timezone.utc))


def test_retrato_do_by_koji_identico(tmp_path,monkeypatch):
    quando=[datetime.fromisoformat(DIA+'T13:00:00').replace(tzinfo=SP)]
    relogio_fixo(monkeypatch,quando)
    retrato={};numeros=[]
    async def cenario(pool):
        app,c,r,p,pref=await montar_fila(pool,True,tmp_path)
        pacotes=[];roteiro=[];alertas=[];skills=[]
        async def agente(http,repo,cofre,tenant,cfg,payload):
            pacotes.append(payload);return await roteiro.pop(0)(payload)
        p.chamar=agente
        from nucleo import alertas as OPS
        async def avisar(self,nivel,titulo,detalhe=''): alertas.append({'nivel':nivel,'titulo':titulo,'detalhe':detalhe});return True
        monkeypatch.setattr(OPS.Alertas,'avisar',avisar)
        try:
            async with c:
                conversa,ids,api,_=await montar(pool,c,app)
                canal=await pool.fetchval("SELECT id FROM tenant_canais WHERE tenant_id=$1 AND tipo='api' AND nome='API' AND ativo",T)
                # Cadastro fixo: unidade aberta todo dia 11h–23h, WhatsApp de pedidos da unidade, telefone de alerta da equipe,
                # refazer turno ligado (como no By Koji de produção).
                await pool.execute("UPDATE tenant_unidades SET horarios=$2::jsonb,whatsapp_pedidos=$3 WHERE tenant_id=$1 AND codigo='patio_guedala'",
                                   T,json.dumps({d:[['11:00','23:00']] for d in ('seg','ter','qua','qui','sex','sab','dom')}),UNIDADE_WHATS)
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'refazer_turno':'on','transbordo_alerta':ALERTA_EQUIPE,'notificar_equipe':'on'})).status_code==200
                mensagens_lidas={'id':0};avisos_lidos={'id':0};turnos_lidos={'id':0}

                async def skill(metodo,caminho,**kw):
                    resp=await api(metodo,caminho,**kw)
                    corpo=resp.json() if resp.content else None
                    skills.append({'skill':f'{metodo} {caminho.split("?")[0]}','status':resp.status_code,'corpo':corpo})
                    return resp

                def fala(texto,antes=None,**campos):
                    async def passo(payload):
                        if antes: await antes(payload)
                        return {'resposta':[texto] if isinstance(texto,str) else texto,'acao':'nenhuma',**campos}
                    return passo

                async def enviar(usuario,texto,n):
                    resp=await c.post(f'/admin/v1/tenants/{T}/agentes/{A}/simular',json={'canal_id':canal,'identificador':usuario,'texto':texto,'message_id':f'{usuario}-{n}'})
                    assert resp.status_code==200,resp.text
                    return resp.json()['conversation_id']

                async def coletar(conv):
                    msgs=await pool.fetch("""SELECT id,direcao,origem,tipo,conteudo,meta,parte FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND id>$3
                        AND direcao='saida' ORDER BY id""",T,conv,mensagens_lidas['id'])
                    if msgs: mensagens_lidas['id']=max(m['id'] for m in msgs)
                    ts=await pool.fetch("""SELECT id,status,origem,acao,desfecho,motivo_silencio,anuncio_falso,transferencia_sem_recusa,refazer_recusado,
                        aviso_demora,equipe_fora_do_horario,resposta FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 AND id>$3 ORDER BY id""",T,conv,turnos_lidos['id'])
                    if ts: turnos_lidos['id']=max(t['id'] for t in ts)
                    av=await pool.fetch("SELECT id,tipo,referencia,texto,destino,status,erro FROM avisos_internos WHERE tenant_id=$1 AND id>$2 ORDER BY id",T,avisos_lidos['id'])
                    if av: avisos_lidos['id']=max(a['id'] for a in av)
                    conv_row=await pool.fetchrow("""SELECT aguardando_motivo,aguardando_atendente_desde IS NOT NULL AS aguardando,bot_pausado_ate>now() AS pausada,
                        desfecho,unidade_preferida FROM conversas WHERE tenant_id=$1 AND conversation_id=$2""",T,conv)
                    def carregar(x): return json.loads(x) if isinstance(x,str) else x
                    return {'mensagens':[{k:(carregar(m[k]) if k=='meta' else m[k]) for k in ('direcao','origem','tipo','conteudo','meta','parte')} for m in msgs],
                            'turnos':[{**{k:t[k] for k in t.keys() if k not in ('id','resposta')},'resposta':carregar(t['resposta'])} for t in ts],
                            'avisos':[{k:a[k] for k in a.keys() if k!='id'} for a in av],
                            'conversa':dict(conv_row) if conv_row else None}

                async def passo(nome,usuario,texto,n,*fases):
                    pacotes.clear();skills.clear();alertas.clear()
                    conv=await enviar(usuario,texto,n)
                    roteiro[:]=list(fases)
                    assert await p.processar_duravel(T,conv)=='concluido'
                    assert not roteiro,nome
                    retrato[nome]={'pacotes':list(pacotes),'skills':list(skills),'alertas':list(alertas),**await coletar(conv)}
                    return conv

                cli='5511900000001'
                # 1. Saudação.
                conv=await passo('saudacao',cli,'oi',1,fala('Olá! Bem-vindo à By Koji 🍣 Quer ver o cardápio?',botoes={'opcoes':[{'id':'cardapio','titulo':'Ver cardápio'}]}))
                # 2. Cardápio: categorias e busca.
                async def cardapio(payload):
                    await skill('GET','/catalogo/busca',params={'conversation_id':payload['conversation_id'],'modo':'categorias','unidade':'patio_guedala'})
                    await skill('GET','/catalogo/busca',params={'conversation_id':payload['conversation_id'],'q':'edamame','unidade':'patio_guedala'})
                await passo('cardapio',cli,'quero ver o cardápio',2,fala('Temos entradas, pratos quentes e mais. O Edamame sai por R$ 34,00.',cardapio))
                # 3. Adicionar item.
                async def adicionar(payload):
                    await skill('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})
                await passo('adicionar_item',cli,'quero 2 edamame',3,fala('Anotei 2 Edamame. Mais alguma coisa?',adicionar))
                # 4. Resumo: entrega e carrinho.
                async def resumo(payload):
                    cid=payload['conversation_id']
                    await skill('PUT',f'/carrinho/{cid}/entrega',json={'unidade':'patio_guedala','modalidade':'retirada','horario':'hoje às 14h'})
                    await skill('GET',f'/carrinho/{cid}')
                    await skill('GET','/unidades',params={'conversation_id':cid})
                await passo('resumo',cli,'retiro às 14h, qual o total?',4,fala('Total R$ 68,00, retirada às 14h no Pátio Guedala. Posso fechar?',resumo,
                                                                               botoes={'opcoes':[{'id':'confirmar','titulo':'Confirmar pedido'}]}))
                # 5. Fechar pedido, a unidade confirma pelo WhatsApp interno e os avisos saem.
                async def fechar(payload):
                    resp=await skill('POST','/pedidos',json={'conversation_id':payload['conversation_id']})
                    assert resp.status_code==201,resp.text
                    await skill('GET','/pedidos',params={'conversation_id':payload['conversation_id']})
                conv=await passo('fechar_pedido',cli,'pode fechar',5,fala('Pedido feito! Número {numero}.',fechar,acao='pedido_fechado'))
                numero=await pool.fetchval('SELECT numero FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
                numeros.append(numero)
                interno=await p.receber(T,canal,{'identificador':UNIDADE_WHATS,'texto':f'OK {numero}','message_id':'unidade-1','tipo':'texto'})
                from modulos.pedidos.avisos import despachar
                await despachar(p)
                retrato['decisao_da_unidade']={'resposta':interno,'pedido':dict(await pool.fetchrow('SELECT numero,status,unidade,modalidade,subtotal,total,horario FROM pedidos WHERE tenant_id=$1 AND numero=$2',T,numero)),
                    'eventos':[dict(e) for e in await pool.fetch('SELECT de,para,por,origem,motivo,aviso FROM pedido_eventos WHERE tenant_id=$1 ORDER BY id',T)],
                    **await coletar(conv)}
                # 6. Cliente pergunta do pedido ativo (consulta).
                async def consulta(payload): await skill('GET',f'/pedidos/{numero}',params={'conversation_id':payload['conversation_id']})
                await passo('consulta_pedido',cli,'e meu pedido?',6,fala('Seu pedido foi confirmado pela unidade.',consulta))

                # 6b. Transferência numa conversa com pedido: o alerta de transbordo traz a linha do pedido.
                await passo('transferencia_com_pedido',cli,'quero falar com alguém sobre o pedido',7,fala('Vou chamar a equipe.',acao='transferir_humano',motivo='pediu_humano'))
                await pool.execute('UPDATE conversas SET aguardando_atendente_desde=NULL,aguardando_motivo=NULL,bot_pausado_ate=NULL WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
                # 7. Transferência pedida pelo cliente (alerta de transbordo para a equipe).
                await passo('transferencia',cli+'2','quero falar com atendente',1,fala('Claro, vou chamar um atendente.',acao='transferir_humano',motivo='pediu_humano'))

                # 8. Turno refeito: chega mensagem nova enquanto o agente trabalha (ações já executadas no pacote seguinte).
                c3=cli+'3'
                async def adiciona_e_chega(payload):
                    await skill('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0004']})
                    await enviar(c3,'e um edamame também',2)
                async def adiciona_mais(payload): await skill('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001']})
                await passo('turno_refeito',c3,'quero uma sapporo',1,fala('Anotei 1 Sapporo.',adiciona_e_chega),fala('Anotei 1 Sapporo e 1 Edamame.',adiciona_mais))
                # 9. Transferência por evento com venda em andamento: vira aviso à equipe, o bot segue.
                await passo('aviso_a_equipe',c3,'é para um evento de 50 pessoas no sábado',3,fala('Vou avisar a equipe sobre o evento e sigo com seu pedido.',
                            acao='transferir_humano',motivo='evento_ou_encomenda',solicitacao={'tipo':'evento','data':'sábado','pessoas':50}))
                retrato['aviso_a_equipe']['solicitacoes']=[dict(s) for s in await pool.fetch("SELECT numero,motivo,tipo,dados,origem,status FROM solicitacoes WHERE tenant_id=$1 ORDER BY id",T)]

                # 10. Pedido anunciado sem fechar: refeito com recado; repetido, a resposta é segurada.
                await passo('anuncio_falso',cli+'4','fecha aí',1,fala('Pedido feito! Já mandei para a unidade.'),fala('Prontinho, seu pedido foi registrado.'))
                # 11. Transferência por pedido para confirmar sem recusa: refeita com recado; repetida, segue com alerta.
                await passo('transferencia_sem_recusa',cli+'5','quero um temaki',1,fala('Vou transferir para a equipe confirmar.',acao='transferir_humano',motivo='pedido_para_confirmar'),
                            fala('A equipe confirma esse item.',acao='transferir_humano',motivo='pedido_para_confirmar'))
                # 12. Recusa de Skill (item sem preço) e transferência legítima.
                async def sem_preco(payload): await skill('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0003']})
                await passo('transferencia_com_recusa',cli+'6','quero um toro',1,fala('Esse item é sob consulta; a equipe confirma.',sem_preco,acao='transferir_humano',motivo='pedido_para_confirmar'))
                # 13. Carrinho parado há mais de 6 h expira antes do turno.
                c7=cli+'7'
                async def carrinho_velho(payload): await skill('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0002']})
                conv7=await passo('carrinho_para_expirar',c7,'quero um ebi',1,fala('Anotei.',carrinho_velho))
                await pool.execute("UPDATE carrinhos SET atualizado_em=now()-interval '7 hours' WHERE tenant_id=$1 AND conversation_id=$2",T,conv7)
                async def ver(payload): await skill('GET',f'/carrinho/{payload["conversation_id"]}')
                await passo('carrinho_expirado',c7,'voltei',2,fala('Seu carrinho anterior expirou.',ver))
                # 14. Fora do horário (23h30): o agente atende (modo "atender"), com a próxima abertura no pacote.
                quando[0]=datetime.fromisoformat(DIA+'T23:30:00').replace(tzinfo=SP)
                async def info(payload): await skill('GET','/unidades',params={'conversation_id':payload['conversation_id']})
                await passo('fora_do_horario',cli+'8','vocês estão abertos?',1,fala('Estamos fechados agora; abrimos amanhã às 11h.',info))
                # 15. Modo "só avisar" com tudo fechado: quem tem pedido ativo é atendido pelo agente; quem não tem recebe o aviso do gateway.
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'fora_do_horario':'so_avisar'})).status_code==200
                await passo('so_avisar_com_pedido',cli,'e o meu pedido?',8,fala('Seu pedido está confirmado.'))
                await passo('so_avisar_sem_pedido',cli+'9','oi, estão abertos?',1)
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'fora_do_horario':'atender'})).status_code==200
                quando[0]=datetime.fromisoformat(DIA+'T13:00:00').replace(tzinfo=SP)
                await despachar(p)

                # Painel: conversas, métricas, avisos despachados e registro de Skills.
                h={'X-Role':'plataforma'}
                lista=(await c.get(f'/admin/v1/tenants/{T}/conversas',params={'limite':50},headers=h)).json()
                retrato['painel']={'conversas':sorted(lista['itens'],key=lambda i: i['conversation_id']),
                    'metricas':{k:v for k,v in (await c.get(f'/admin/v1/tenants/{T}/metricas',headers=h)).json().items()},
                    'avisos':[dict(a) for a in await pool.fetch('SELECT tipo,referencia,texto,destino,status,erro FROM avisos_internos WHERE tenant_id=$1 ORDER BY id',T)],
                    'skill_registro':[dict(s) for s in await pool.fetch('SELECT skill,status,motivo,campos FROM skill_registro WHERE tenant_id=$1 ORDER BY id',T)],
                    'unidade':(await c.get(f'/admin/v1/tenants/{T}/unidades',headers=h)).json(),
                    'solicitacoes':(await c.get(f'/admin/v1/tenants/{T}/solicitacoes',headers=h)).json()}

                # LGPD: anonimização do cliente que fez o pedido.
                cid=await pool.fetchval('SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
                resp=await c.request('DELETE',f'/admin/v1/tenants/{T}/contatos/{cid}',json={'confirmacao':cli},headers=h)
                assert resp.status_code==200,resp.text
                novo=await pool.fetchval("SELECT conversation_id FROM conversas WHERE tenant_id=$1 AND contato_id=$2",T,cid)
                retrato['anonimizacao']={'resposta':resp.json(),
                    'carrinhos':[dict(x) for x in await pool.fetch('SELECT status,unidade,modalidade,endereco,observacao,horario FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id',T,novo)],
                    'pedidos':[dict(x) for x in await pool.fetch('SELECT numero,status,endereco,observacao,cliente_nome,cliente_telefone,total FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2',T,novo)],
                    'pedido_eventos':[dict(x) for x in await pool.fetch('SELECT de,para,por,origem,motivo,aviso FROM pedido_eventos WHERE tenant_id=$1 ORDER BY id',T)],
                    'avisos':[dict(x) for x in await pool.fetch('SELECT tipo,texto,destino FROM avisos_internos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id',T,novo)]}
        finally:
            await limpar(r,pref)
    banco_real(cenario)
    # O número do pedido é sorteado: vira <numero> em qualquer lugar onde aparece.
    bruto=json.dumps(retrato,default=str,ensure_ascii=False)
    for n in numeros: bruto=bruto.replace(n,'<numero>')
    atual=normalizar(json.loads(bruto))
    if os.getenv('RETRATO_SAIDA'):
        Path(os.environ['RETRATO_SAIDA']).write_text(json.dumps(atual,ensure_ascii=False,indent=1,sort_keys=True))
        pytest.skip('retrato gravado em '+os.environ['RETRATO_SAIDA'])
    esperado=json.loads(RETRATO.read_text())
    # Campo a campo: a primeira diferença aparece com o caminho completo.
    def comparar(a,b,caminho='retrato'):
        if isinstance(a,dict) and isinstance(b,dict):
            assert sorted(a)==sorted(b),(caminho,sorted(set(a)^set(b)))
            for k in a: comparar(a[k],b[k],f'{caminho}.{k}')
        elif isinstance(a,list) and isinstance(b,list):
            assert len(a)==len(b),(caminho,len(a),len(b))
            for i,(x,y) in enumerate(zip(a,b)): comparar(x,y,f'{caminho}[{i}]')
        else: assert a==b,(caminho,a,b)
    comparar(atual,esperado)
