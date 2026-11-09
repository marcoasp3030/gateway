"""CARD-LINK (docs/ESPEC_CARD_LINK.md): comportamento do agente "enviar_link_cardapio". on (padrão): o pacote traz o link do cardápio
digital; off: não traz (nem gera) e avisa cardapio_digital=desligado, para o agente apresentar as opções na conversa."""
import pytest
from agentes import skyone
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
T,A='bykoji','bykoji_atendimento'
GESTOR={'X-Role':'gestor','X-Actor':'gestor@exemplo.test'}
SUP={'X-Role':'supervisor','X-Actor':'sup@exemplo.test'}


def test_liga_e_desliga_o_link_do_cardapio(monkeypatch):
    monkeypatch.setenv('PAINEL_PUBLIC_URL','https://painel.exemplo.test');monkeypatch.setenv('API_KEY_PEPPER','p')
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            repo=Repositorio(conn)
            async def pedidos(c):
                cfg=await Configuracoes(repo).efetiva(T,A)
                return cfg,(await skyone.pacote(repo,T,c,10**9,'oi',cfg))['contexto_modulos']['pedidos']
            # Padrão: ligado, como era.
            c1=await conversa('cliente-link-1')
            cfg,p=await pedidos(c1)
            assert cfg['enviar_link_cardapio']=='on' and p['cardapio_digital']=='ligado' and p['link_cardapio'].startswith('https://painel.exemplo.test')
            esquema=(await client.get(f'/admin/v1/tenants/{T}/agentes/{A}/atendimento',headers=GESTOR)).json()['esquema']['comportamentos']
            assert 'enviar_link_cardapio' in esquema
            # Desligado pelo gestor (rascunho e publicação): sem link e sem gerar um; o agente apresenta as opções.
            q=f'/admin/v1/tenants/{T}/agentes/{A}/atendimento'
            v=(await client.get(q,headers=GESTOR)).json()
            r=await client.put(q+'/rascunho',json={'baseado_em':v['versao_publicada'] if 'versao_publicada' in v else 0,'comportamentos':{'enviar_link_cardapio':'off'}},headers=SUP)
            assert r.status_code==403
            r=await client.put(q+'/rascunho',json={'baseado_em':v.get('versao_publicada',0),'comportamentos':{'enviar_link_cardapio':'x'}},headers=GESTOR)
            assert r.status_code==400,r.text
            assert (await client.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'enviar_link_cardapio':'off'},headers=GESTOR)).status_code==200
            c2=await conversa('cliente-link-2')
            cfg,p=await pedidos(c2)
            assert cfg['enviar_link_cardapio']=='off' and p['cardapio_digital']=='desligado' and p['link_cardapio'] is None
            assert await conn.fetchval('SELECT count(*) FROM cardapio_links WHERE conversation_id=$1',c2)==0
            # Ligado de novo: volta o link.
            assert (await client.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'enviar_link_cardapio':'on'},headers=GESTOR)).status_code==200
            assert (await pedidos(c2))[1]['link_cardapio'] is not None
    banco(cenario)
