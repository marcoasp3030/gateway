"""UX3: aviso de demora com janela, expiração de carrinho e atualizado_em no pacote."""
import asyncio,json
from datetime import datetime
import pytest
from modulos.pedidos import servico as pedidos
from test_migrations import DSN
from test_fila import banco_real,montar as montar_fila,limpar
from test_fase4_pedidos import montar as montar_pedidos
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
T,A='bykoji','bykoji_atendimento'
AVISO='Só um instante, estou verificando.'


async def preparar(pool,tmp_path):
    app,c,r,p,pref=await montar_fila(pool,True,tmp_path)
    return app,c,r,p,pref


# O aviso de demora mudou no AVS1 ("digitando…" por padrão; texto só no meio de algo): os testes dele estão em tests/test_avs1.py.


def test_carrinho_parado_expira_antes_do_pacote_e_o_agente_fica_sabendo(tmp_path):
    async def cenario(pool):
        app,c,r,p,pref=await preparar(pool,tmp_path)
        try:
            async with c:
                conversa,ids,api,_=await montar_pedidos(pool,c,app)
                async def chegar(conv,texto): await pool.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ($1,$2,'entrada','cliente',$3)",T,conv,texto)
                parado=lambda conv,intervalo: pool.execute("UPDATE carrinhos SET atualizado_em=now()-$3::text::interval WHERE tenant_id=$1 AND conversation_id=$2 AND status='aberto'",T,conv,intervalo)
                estado=lambda conv: pool.fetch("SELECT status,expirado_em FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id",T,conv)
                pacotes=[]
                async def agente(http,repo,cofre,tenant,cfg,payload):
                    pacotes.append(payload);return {'resposta':['ok'],'acao':'nenhuma'}
                p.chamar=agente
                async def turno(conv):
                    assert await p.processar_duravel(T,conv)=='concluido';return pacotes[-1]['contexto_modulos']['pedidos']
                async def por(conv,codigo,qtd=1):
                    assert (await api('POST',f'/carrinho/{conv}/itens',json={'item_id':ids[codigo],'quantidade':qtd})).status_code==200

                # 1) Carrinho recente: nada expira; atualizado_em vem no fuso da empresa.
                c1=await conversa('x1');await por(c1,'GUE-0001',2);await por(c1,'GUE-0002')
                ctx=await turno(c1)
                assert ctx['carrinho_expirado'] is False and ctx['itens_expirados']==[] and len(ctx['carrinho']['itens'])==2
                quando=datetime.fromisoformat(ctx['carrinho']['atualizado_em'])
                assert quando.tzinfo is not None and ctx['carrinho']['atualizado_em'].endswith('-03:00')
                # A Skill também devolve a data (com fuso).
                assert datetime.fromisoformat((await api('GET',f'/carrinho/{c1}')).json()['carrinho']['atualizado_em']).tzinfo is not None
                # 2) Parado há 5 h 59 min: não expira.
                await parado(c1,'5 hours 59 minutes');await chegar(c1,'oi de novo');ctx=await turno(c1)
                assert ctx['carrinho_expirado'] is False and len(ctx['carrinho']['itens'])==2 and [e['status'] for e in await estado(c1)]==['aberto']
                # 3) Parado há 6 h 1 min: é arquivado antes do pacote; o agente recebe o que havia.
                await parado(c1,'6 hours 1 minute');await chegar(c1,'voltei');ctx=await turno(c1)
                assert ctx['carrinho_expirado'] is True and ctx['itens_expirados']==[{'nome':'Edamame','quantidade':2},{'nome':'Ebi no Tempurá','quantidade':1}]
                assert ctx['carrinho']['itens']==[] and ctx['carrinho']['atualizado_em'] is None and ctx['carrinho']['total'] is None
                e=await estado(c1);assert [x['status'] for x in e]==['expirado'] and e[0]['expirado_em'] is not None
                assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='carrinho.expirado' AND alvo=$2",T,c1)==1
                # Os itens continuam guardados (nada é apagado) e a Skill mostra carrinho vazio.
                assert await pool.fetchval("SELECT count(*) FROM carrinho_itens l JOIN carrinhos k ON k.id=l.carrinho_id WHERE k.conversation_id=$1",c1)==2
                assert (await api('GET',f'/carrinho/{c1}')).json()['carrinho']['itens']==[]
                # O aviso continua nos turnos seguintes até o cliente montar outro carrinho; não arquiva duas vezes.
                await chegar(c1,'hm');ctx=await turno(c1);assert ctx['carrinho_expirado'] is True and len(ctx['itens_expirados'])==2
                await por(c1,'GUE-0001');await chegar(c1,'quero um');ctx=await turno(c1)
                assert ctx['carrinho_expirado'] is False and ctx['itens_expirados']==[] and [(i['nome'],i['quantidade']) for i in ctx['carrinho']['itens']]==[('Edamame',1)]
                assert [x['status'] for x in await estado(c1)]==['expirado','aberto']
                assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='carrinho.expirado'",T)==1

                # 4) Pedido já fechado não é afetado, por mais antigo que seja.
                c2=await conversa('x2');await por(c2,'GUE-0001')
                assert (await api('PUT',f'/carrinho/{c2}/entrega',json={'modalidade':'retirada'})).status_code==200
                r_p=await api('POST','/pedidos',json={'conversation_id':c2});assert r_p.status_code==201,r_p.text
                numero=r_p.json()['pedido']['numero']
                await pool.execute("UPDATE carrinhos SET atualizado_em=now()-interval '3 days' WHERE conversation_id=$1",c2)
                await chegar(c2,'e o meu pedido?');ctx=await turno(c2)
                assert ctx['carrinho_expirado'] is False and ctx['ultimo_pedido']['numero']==numero and [x['status'] for x in await estado(c2)]==['fechado']
                assert await pool.fetchval("SELECT status FROM pedidos WHERE numero=$1",numero)=='aguardando_loja'

                # 5) Prazo configurável por agente; isolamento: outra conversa e outra empresa não são tocadas.
                c3=await conversa('x3');await por(c3,'GUE-0001');c4=await conversa('x4');await por(c4,'GUE-0002')
                await parado(c3,'2 hours');await parado(c4,'2 hours')
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'carrinho_expira_h':1})).status_code==200
                assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'carrinho_expira_h':0})).status_code==400
                await chegar(c3,'oi');ctx=await turno(c3)
                assert ctx['carrinho_expirado'] is True and [x['status'] for x in await estado(c3)]==['expirado']
                assert [x['status'] for x in await estado(c4)]==['aberto']          # só expira na conversa que recebeu mensagem
                async with pool.acquire() as conn,conn.transaction():
                    assert await pedidos.expirar(conn,'postman_teste',c4,1) is None   # a empresa errada não alcança o carrinho
                    assert await pedidos.expirado(conn,'postman_teste',c3) is None
                assert [x['status'] for x in await estado(c4)]==['aberto']
        finally:
            await limpar(r,pref)
    banco_real(cenario)
