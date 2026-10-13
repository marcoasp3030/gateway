"""HOR2 (docs/ESPEC_HOR2.md): proxima_abertura só com unidades que têm cardápio; horário reconferido quando a unidade muda;
transferência por pedido_para_confirmar sem recusa de Skill é refeita uma vez com aviso."""
import json
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import pytest
from agentes import skyone
from nucleo import expediente,pipeline as PL
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from modulos.pedidos import agendamento as AG
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar,motivo
from test_fila import banco_real,montar as montar_fila,limpar

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
SP=ZoneInfo('America/Sao_Paulo')
T,A='bykoji','bykoji_atendimento'
def em(hora,minuto=0,dia=13): return datetime(2026,10,dia,hora,minuto,tzinfo=SP)
SEMANA=lambda faixa: json.dumps({d:[faixa] for d in ('seg','ter','qua','qui','sex','sab','dom')})


def test_proxima_abertura_so_com_cardapio_e_horario_reconferido_na_troca_de_unidade(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        relogio=[em(11,26)];monkeypatch.setattr(AG,'agora_utc',lambda: relogio[0].astimezone(timezone.utc))
        class Relogio(datetime):
            @classmethod
            def now(cls,tz=None): return relogio[0].astimezone(tz or timezone.utc)
        monkeypatch.setattr(skyone,'datetime',Relogio)
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            for nome,codigo in (('Einstein','einstein'),('Morumbi','morumbi')):
                assert (await client.post(f'/admin/v1/tenants/{T}/unidades',json={'nome':nome,'codigo':codigo})).status_code==201
            # Einstein abre 11h30 e não tem nenhum item com preço; Pátio Guedala abre 12h e Morumbi 12h30, os dois com cardápio.
            for codigo,faixa in (('einstein',['11:30','15:00']),('patio_guedala',['12:00','15:00']),('morumbi',['12:30','15:00'])):
                await conn.execute("UPDATE tenant_unidades SET horarios=$3::jsonb WHERE tenant_id=$1 AND codigo=$2",T,codigo,SEMANA(faixa))
            c=await conversa('cliente1');repo=Repositorio(conn);cfg=await Configuracoes(repo).efetiva(T,A)
            async def pacote(): return await skyone.pacote(repo,T,c,10**9,'oi',cfg)

            # 11h26, tudo fechado: a próxima abertura do pacote é a do Pátio Guedala (12h), não a do Einstein (11h30, sem cardápio).
            p=await pacote();assert p['fora_do_horario'] is True and p['agora'].startswith('2026-10-13T11:26')
            assert p['proxima_abertura']=={'unidade':'Pátio Guedala','codigo':'patio_guedala','dia_semana':'terça-feira','data':'2026-10-13','hora':'12:00'}
            assert {u['codigo']:(u['tem_cardapio'],u['proxima_abertura'][11:16]) for u in p['unidades']}=={'patio_guedala':(True,'12:00'),'einstein':(False,'11:30'),'morumbi':(True,'12:30')}
            # A função de base não mudou para quem não pede o filtro (fora do horário "só avisar" e horário da equipe usam todas as unidades).
            e=await expediente.da_empresa(conn,T,'America/Sao_Paulo',relogio[0]);assert (e['fora'],e['codigo'],e['do_cardapio'])==(True,'einstein',None)
            e=await expediente.da_empresa(conn,T,'America/Sao_Paulo',relogio[0],{'morumbi'});assert (e['codigo'],e['do_cardapio']['codigo'])==('einstein','morumbi')
            # Com alguma unidade aberta, a empresa não está fora do horário e o campo fica nulo, como antes.
            relogio[0]=em(11,40);p=await pacote();assert (p['fora_do_horario'],p['proxima_abertura'])==(False,None)
            # Empresa sem nenhum item com preço (sem cardápio em lugar nenhum): vale a próxima abertura de qualquer unidade.
            relogio[0]=em(11,26);await conn.execute("UPDATE catalogo_itens SET preco=NULL WHERE tenant_id=$1 AND unidade='morumbi'",T)
            assert (await pacote())['proxima_abertura']['codigo']=='patio_guedala'
            guardados=await conn.fetch("SELECT id,preco FROM catalogo_itens WHERE tenant_id=$1 AND preco IS NOT NULL",T)
            await conn.execute("UPDATE catalogo_itens SET preco=NULL WHERE tenant_id=$1",T)
            p=await pacote();assert p['proxima_abertura']['codigo']=='einstein' and not any(u['tem_cardapio'] for u in p['unidades'])
            for g in guardados: await conn.execute("UPDATE catalogo_itens SET preco=$2 WHERE id=$1",g['id'],g['preco'])
            await conn.execute("UPDATE catalogo_itens SET preco=30 WHERE tenant_id=$1 AND codigo='MOR-0001'",T)

            # Troca de unidade com horário já marcado (o caso do teste de 13/10): 12h15 vale no Pátio Guedala...
            base=f'/carrinho/{c}';entrega=lambda **d: api('PUT',base+'/entrega',json=d)
            k=(await api('GET',base)).json()['carrinho'];assert k['horario_aviso'] is None
            r=await entrega(unidade='patio_guedala',modalidade='retirada',horario='hoje às 12h15');assert r.status_code==200 and r.json()['aviso_horario'] is None and r.json()['carrinho']['horario_aviso'] is None
            # ...e não vale no Morumbi (abre 12h30): a troca é aceita e o aviso vem na hora, no formato dos motivos de horário.
            r=await entrega(unidade='morumbi');j=r.json();assert r.status_code==200,r.text
            aviso=j['aviso_horario'];assert aviso==j['carrinho']['horario_aviso'] and j['carrinho']['unidade']=='morumbi'
            assert (aviso['motivo'],aviso['agendado_para'],aviso['agora'],aviso['aberta_no_dia'])==('horario_fora_funcionamento','2026-10-13T12:15:00-03:00','2026-10-13T11:26:00-03:00',[['12:30','15:00']])
            assert aviso['proxima_abertura']=={'unidade':'Morumbi','codigo':'morumbi','dia_semana':'terça-feira','data':'2026-10-13','hora':'12:30'}
            assert (j['carrinho']['horario'],j['carrinho']['horario_vencido'],j['carrinho']['pode_fechar'])==('hoje às 12h15',False,False) and 'horario' in j['carrinho']['faltando']
            # O pacote leva o mesmo aviso; mudar só a modalidade continua avisando; o fechamento recusa como sempre.
            assert (await pacote())['contexto_modulos']['pedidos']['carrinho']['horario_aviso']['motivo']=='horario_fora_funcionamento'
            assert (await entrega(modalidade='retirada')).json()['aviso_horario']['motivo']=='horario_fora_funcionamento'
            await api('GET','/catalogo/busca',params={'conversation_id':c,'q':'shake'});assert (await api('POST',base+'/itens',buscado=False,json={'item_id':ids['MOR-0001']})).status_code==200
            r=await api('POST','/pedidos',json={'conversation_id':c});assert (r.status_code,motivo(r))==(422,'horario_fora_funcionamento') and r.json()['carrinho']['horario_aviso']['motivo']=='horario_fora_funcionamento'
            # Horário novo dentro do funcionamento: o aviso some.
            r=await entrega(horario='hoje às 13h');assert r.json()['aviso_horario'] is None and r.json()['carrinho']['pode_fechar'] is True and r.json()['carrinho']['faltando']==[]
            # O relógio passou do horário marcado: o aviso vira horario_passado (e horario_vencido continua valendo).
            relogio[0]=em(13,30);k=(await api('GET',base)).json()['carrinho']
            assert (k['horario_vencido'],k['horario_aviso']['motivo'],k['horario_aviso']['aberta_agora'],k['pode_fechar'],k['faltando'])==(True,'horario_passado',True,False,['horario'])
            # Sem horário marcado ("o quanto antes") não há aviso, com a unidade aberta ou fechada.
            r=await entrega(horario='o quanto antes');assert r.json()['aviso_horario'] is None and r.json()['carrinho']['pode_fechar'] is True
    banco(cenario)


def test_transferencia_por_pedido_para_confirmar_sem_recusa_de_skill(tmp_path,monkeypatch):
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
        transfere=lambda texto,antes=None,motivo='pedido_para_confirmar': fala(texto,antes,acao='transferir_humano',motivo=motivo)
        saidas=lambda conv: pool.fetch("SELECT conteudo FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' AND origem='bot' ORDER BY id",T,conv)
        turnos=lambda conv: pool.fetch("SELECT status,transferencia_sem_recusa,anuncio_falso,refeito_de,acao,id FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id",T,conv)
        quadro=lambda ts: [(t['status'],t['transferencia_sem_recusa']) for t in ts]
        aguardando=lambda conv: pool.fetchval('SELECT aguardando_atendente_desde IS NOT NULL FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
        try:
            async with c:
                conversa,ids,api,_=await montar(pool,c,app)
                async def rodar(conv): assert await p.processar_duravel(T,conv)=='concluido'

                # (a) Transferiu sem tentar nada → turno refeito com o recado; na segunda vez o agente atende, e ninguém é transferido.
                c1=await conversa('a1')
                roteiro[:]=[transfere('Vou transferir para a equipe confirmar o cardápio 🙏'),fala('Aqui estão as categorias do cardápio. Qual você quer ver?')]
                await rodar(c1)
                assert len(pacotes)==2 and pacotes[0]['aviso_do_gateway'] is None and pacotes[1]['aviso_do_gateway']==PL.AVISO_TRANSFERENCIA and 'pedido_para_confirmar' in PL.AVISO_TRANSFERENCIA
                assert [m['conteudo'] for m in await saidas(c1)]==['Aqui estão as categorias do cardápio. Qual você quer ver?'] and not await aguardando(c1) and not alertas
                ts=await turnos(c1);assert quadro(ts)==[('refeito','refeito'),('ok',None)] and ts[1]['refeito_de']==ts[0]['id']

                # (b) Repetiu a transferência no turno refeito → ela segue (cliente avisado, conversa aguardando atendente) e a operação é alertada.
                pacotes.clear();c2=await conversa('b1')
                roteiro[:]=[transfere('Não consegui incluir, vou chamar a equipe.'),transfere('Vou transferir para a equipe confirmar.')]
                await rodar(c2)
                assert len(pacotes)==2 and [m['conteudo'] for m in await saidas(c2)]==['Vou transferir para a equipe confirmar.'] and await aguardando(c2)
                ts=await turnos(c2);assert quadro(ts)==[('refeito','refeito'),('ok','mantida')] and ts[1]['acao']=='transferir_humano'
                assert len(alertas)==1 and alertas[0][1]=='transferência sem tentativa' and 'bykoji' in alertas[0][2] and c2 not in alertas[0][2]
                assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='transbordo.sem_recusa' AND alvo=$2",T,c2)==1

                # (c) Houve recusa de Skill no turno (item sem preço): a transferência é legítima e passa direto.
                pacotes.clear();c3=await conversa('c1')
                async def tenta(payload):
                    r=await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0003']});assert r.status_code==422 and r.json()['erro']['detalhes']['motivo']=='sem_preco'
                roteiro[:]=[transfere('Esse item não tem preço cadastrado; a equipe confirma por aqui.',tenta)]
                await rodar(c3);assert len(pacotes)==1 and quadro(await turnos(c3))==[('ok',None)] and await aguardando(c3) and len(alertas)==1
                # Qualquer recusa conta, inclusive a de item que não foi buscado.
                pacotes.clear();c4=await conversa('d1')
                async def sem_buscar(payload):
                    r=await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',buscado=False,json={'item_id':ids['GUE-0002']});assert r.json()['erro']['detalhes']['motivo']=='item_nao_buscado'
                roteiro[:]=[transfere('Vou chamar a equipe.',sem_buscar)]
                await rodar(c4);assert len(pacotes)==1 and quadro(await turnos(c4))==[('ok',None)]
                # Recusa na primeira tentativa vale para o turno refeito (conta desde o início).
                # (d) Outro motivo de transferência não é da conta desta guarda.
                pacotes.clear();c5=await conversa('e1')
                roteiro[:]=[transfere('Claro, vou chamar um atendente.',motivo='pediu_humano')]
                await rodar(c5);assert len(pacotes)==1 and quadro(await turnos(c5))==[('ok',None)] and await aguardando(c5)
                # (e) Resposta comum com Skill de sucesso: nada muda.
                pacotes.clear();c6=await conversa('f1')
                async def adiciona(payload): assert (await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001']})).status_code==200
                roteiro[:]=[fala('Anotei 1 Edamame. Quer mais alguma coisa?',adiciona)]
                await rodar(c6);assert len(pacotes)==1 and quadro(await turnos(c6))==[('ok',None)] and len(alertas)==1

                m=(await c.get(f'/admin/v1/tenants/{T}/metricas')).json()
                assert (m['transferencias_sem_recusa_refeitas'],m['transferencias_sem_recusa_mantidas'])==(2,1)
        finally:
            await limpar(r,pref)
    banco_real(cenario)
