"""Voltar um passo do pedido: o clique errado é desfeito, o aviso que não saiu é cancelado e o passo pode ser refeito."""
from datetime import datetime,timedelta,timezone
import pytest
from modulos.pedidos import avisos,servico
from test_migrations import banco,DSN
from test_fase4_avisos import montar,UNIDADE
from test_ped1a import B,ambiente,detalhes

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


def test_regra_de_quem_pode_voltar():
    agora=datetime.now(timezone.utc)
    assert servico.voltar_para('confirmado','aguardando_loja',agora,agora)=='aguardando_loja'
    assert servico.voltar_para('concluido','pronto_retirada',agora,agora)=='pronto_retirada'
    for status in ('aguardando_loja','recusado','cancelado'):
        assert servico.voltar_para(status,'confirmado',agora,agora) is None
    assert servico.voltar_para('confirmado','aguardando_loja',agora-timedelta(minutes=servico.JANELA_VOLTAR_MIN+1),agora) is None
    assert servico.voltar_para('confirmado',None,agora,agora) is None


def test_desfaz_o_passo_cancela_o_aviso_pendente_e_refaz(monkeypatch):
    async def cenario(conn):
        app,client,p=await ambiente(conn,monkeypatch)
        async with client:
            webhook,api,pedido,enviados,live,canal=await montar(conn,client,app,monkeypatch,whatsapp=None)
            await live()
            mudar=lambda n,**c: client.post(f'{B}/{n}/status',json=c,headers={'X-Actor':'joana'})
            voltar=lambda n,de: client.post(f'{B}/{n}/voltar',json={'de':de},headers={'X-Actor':'joana'})
            async def despachar():
                antes=len(enviados);await avisos.despachar(p);await avisos.despachar(p);return [e[1] for e in enviados[antes:] if e[0]!=UNIDADE]
            conv,n=await pedido()
            assert (await client.get(f'{B}/{n}')).json()['voltar_para'] is None
            # Confirmou sem querer: o aviso ainda está na fila e some; o pedido volta a aguardar a decisão.
            d=(await mudar(n,status='confirmado')).json();assert d['voltar_para']=='aguardando_loja' and d['decidido_por']=='joana'
            r=await voltar(n,'confirmado');assert r.status_code==200,r.text
            d=r.json();assert (d['status'],d['decidido_por'],d['voltar_para'],d['desfeito'])==('aguardando_loja',None,None,
                {'de':'confirmado','para':'aguardando_loja','aviso_ja_enviado':False})
            assert await despachar()==[]
            assert [e['para'] for e in d['linha_do_tempo']]==['aguardando_loja']
            # Refeito o passo, ele volta à linha do tempo e o aviso sai.
            d=(await mudar(n,status='confirmado')).json();assert [e['para'] for e in d['linha_do_tempo']]==['aguardando_loja','confirmado']
            assert len(await despachar())==1
            # Aviso já entregue: o passo volta, e o painel fica sabendo que o cliente já recebeu.
            assert (await mudar(n,status='pronto_retirada')).status_code==200 and len(await despachar())==1
            d=(await voltar(n,'pronto_retirada')).json();assert (d['status'],d['desfeito']['aviso_ja_enviado'])==('confirmado',True)
            # Refeito: o aviso já entregue não sai de novo.
            assert (await mudar(n,status='pronto_retirada')).status_code==200 and await despachar()==[]
            # O painel viu outro status: nada é desfeito.
            r=await voltar(n,'confirmado');assert r.status_code==409 and detalhes(r)['motivo']=='pedido_mudou'
            # Fora da janela, não volta mais.
            await conn.execute("UPDATE pedido_eventos SET criado_em=now()-interval '2 hours' WHERE para='pronto_retirada'")
            assert (await client.get(f'{B}/{n}')).json()['voltar_para'] is None
            r=await voltar(n,'pronto_retirada');assert r.status_code==409 and detalhes(r)['motivo']=='nao_pode_voltar'
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id='bykoji' AND acao='pedido.status_desfeito'")==2
            # Cancelado não volta.
            conv2,n2=await pedido()
            assert (await mudar(n2,status='cancelado',motivo='cliente desistiu')).status_code==200
            assert (await client.get(f'{B}/{n2}')).json()['voltar_para'] is None
            r=await client.post(f'{B}/{n2}/voltar',json={'de':'cancelado'});assert r.status_code in (400,422)
    banco(cenario)
