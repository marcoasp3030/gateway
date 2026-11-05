"""TELAS1: a plataforma bloqueia telas do painel por empresa. Vazio = tudo liberado; a rota que só serve a tela bloqueada recusa
quem não é a plataforma; rota de várias telas não entra no mapa."""
import pytest
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


def com(papel,escopo='alpha'):
    return {'X-Role':papel,'X-Tenant-Scope':escopo,'X-Actor':'teste'}


def test_mapa_das_rotas():
    from nucleo.telas import tela_da_rota,normalizar
    assert tela_da_rota('/tenants/{tenant_id}/pedidos/{numero}/status')=='pedidos'
    assert tela_da_rota('/tenants/{tenant_id}/pedidos/avisos/{status}')=='avisos_de_pedido'
    assert tela_da_rota('/tenants/{tenant_id}/pedidos/{numero}/avisos/{status}/reenviar')=='avisos_de_pedido'
    assert tela_da_rota('/tenants/{tenant_id}/catalogo/itens')=='cardapio'
    assert tela_da_rota('/tenants/{tenant}/cardapio/aparencia')=='aparencia'
    assert tela_da_rota('/tenants/{tenant}/cardapio/redes')=='aparencia'
    assert tela_da_rota('/tenants/{tenant_id}/webhooks-saida/{wid}')=='webhooks'
    # Usadas por mais de uma tela (enviar cardápio na conversa, agentes, canais, unidades...): ficam fora.
    for rota in ('/tenants/{tenant}/cardapio/previa','/tenants/{tenant}/cardapio/loja','/tenants/{tenant_id}/agentes',
                 '/tenants/{tenant}/conversas','/tenants/{tenant_id}','/tenants','/tenants/{tenant}/chaves-formulario'):
        assert tela_da_rota(rota) is None,rota
    assert normalizar(['webhooks','pedidos','pedidos'])==['pedidos','webhooks']


def test_bloqueia_e_libera_a_tela_da_empresa():
    from nucleo import telas
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        telas.invalidar()
        async with c:
            await criar(c)
            webhooks='/admin/v1/tenants/alpha/webhooks-saida'
            modulos='/admin/v1/tenants/alpha/modulos'
            # Padrão: nada bloqueado.
            assert (await c.get(modulos,headers=com('gestor'))).json()['bloqueadas']==[]
            assert (await c.get(webhooks,headers=com('gestor'))).status_code==200
            # Só a plataforma grava, e só telas conhecidas.
            r=await c.put('/admin/v1/tenants/alpha/telas',json={'bloqueadas':['webhooks']},headers=com('gestor'))
            assert r.status_code==403
            r=await c.put('/admin/v1/tenants/alpha/telas',json={'bloqueadas':['inventada']},headers={'X-Role':'plataforma','X-Actor':'teste'})
            assert r.status_code==400
            r=await c.put('/admin/v1/tenants/alpha/telas',json={'bloqueadas':['webhooks','pedidos','webhooks']},
                          headers={'X-Role':'plataforma','X-Actor':'teste'})
            assert (r.status_code,r.json())==(200,{'bloqueadas':['pedidos','webhooks']})
            assert (await c.get(modulos,headers=com('funcionario'))).status_code==403  # leitura do catálogo segue com o gestor
            assert (await c.get(modulos,headers=com('gestor'))).json()['bloqueadas']==['pedidos','webhooks']
            # Bloqueada: a empresa recebe 403 com o motivo; a plataforma continua vendo.
            r=await c.get(webhooks,headers=com('gestor'))
            assert r.status_code==403 and r.json()['erro']['detalhes']=={'motivo':'tela_bloqueada','tela':'webhooks'}
            assert (await c.get(webhooks,headers={'X-Role':'plataforma'})).status_code==200
            r=await c.get('/admin/v1/tenants/alpha/pedidos/contagem',headers=com('funcionario'))
            assert r.status_code==403 and r.json()['erro']['detalhes']['motivo']=='tela_bloqueada'
            # Rota de várias telas não é afetada.
            assert (await c.get('/admin/v1/tenants/alpha',headers=com('funcionario'))).status_code==200
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id='alpha' AND acao='telas.alteradas'")==1
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='acesso.negado' AND dados->>'motivo'='tela_bloqueada'")>=1
            # Liberada de novo.
            assert (await c.put('/admin/v1/tenants/alpha/telas',json={'bloqueadas':[]},headers={'X-Role':'plataforma','X-Actor':'teste'})).json()=={'bloqueadas':[]}
            assert (await c.get(webhooks,headers=com('gestor'))).status_code==200
    banco(cenario)
