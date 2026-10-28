"""Preparação para ligar ESCOPO_EMPRESA_OBRIGATORIO (MULTI1-a): com a exigência ligada só neste teste, confere
1. rota de empresa sem X-Tenant-Scope: só o papel plataforma passa; admin, membro, papel ausente ou desconhecido → 403, nada gravado;
2. a credencial do painel é uma só para todos os papéis: o papel vem do X-Role (risco registrado aqui, para a separação proposta);
3. /modelos-agente e /tenants com sessão de empresa: escritas recusadas; leituras só com o cabeçalho e só o que é da empresa."""
import pytest
from nucleo import escopo_empresa
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')

PLAT={'X-Role':'plataforma'}
# Rotas de empresa de várias famílias (leitura e escrita).
ROTAS_DA_EMPRESA=[('GET',''),('GET','/agentes'),('PATCH','/agentes/{a}'),('GET','/canais'),('GET','/conversas'),('GET','/config'),
                  ('PUT','/config'),('GET','/auditoria'),('GET','/atendimento'),('GET','/modulos'),('GET','/contatos'),('POST','/api-keys'),
                  ('DELETE','/api-keys/x')]
SEM_ESCOPO=[{'X-Role':'admin'},{'X-Role':'membro'},{'X-Role':''},{'X-Role':'superadmin'},{'X-Role':'plataforma-x'}]


async def _montar(conn,c,monkeypatch):
    """Cadastro com a exigência desligada (o helper cria sem cabeçalho); depois liga a exigência só neste teste."""
    monkeypatch.delenv('ESCOPO_EMPRESA_OBRIGATORIO',raising=False)
    a,_=await criar(c);b,_=await criar(c,'bravo')
    # Modelo próprio da empresa bravo (não pode aparecer para outra empresa).
    base=await conn.fetchval("SELECT id FROM modelos_agente WHERE tenant_id IS NULL AND ativo ORDER BY id LIMIT 1")
    await conn.execute("""CREATE TEMP TABLE m AS SELECT * FROM modelos_agente WHERE id=$1 ORDER BY versao DESC LIMIT 1;
        UPDATE m SET id='privado_bravo',tenant_id='bravo',versao=1; INSERT INTO modelos_agente SELECT * FROM m; DROP TABLE m""".replace('$1',"'"+base+"'"))
    monkeypatch.setenv('ESCOPO_EMPRESA_OBRIGATORIO','on')
    escopo_empresa._ultimo_log.clear()
    return a,b


def test_1_rota_de_empresa_sem_cabecalho_so_passa_como_plataforma(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,b=await _montar(conn,c,monkeypatch)
            antes=await conn.fetchval('SELECT count(*) FROM audit_log')
            for h in SEM_ESCOPO:
                for metodo,rota in ROTAS_DA_EMPRESA:
                    r=await c.request(metodo,'/admin/v1/tenants/alpha'+rota.format(a=a),json={'nome':'x'},headers=h)
                    assert r.status_code==403,(h,metodo,rota,r.status_code)
            # Nada foi gravado pelas chamadas recusadas (só a recusa na auditoria, PAPEL1-a).
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao<>'acesso.negado'")==antes
            assert await conn.fetchval("SELECT nome FROM agentes WHERE tenant_id='alpha' AND id=$1",a)=='A'
            # Com o cabeçalho da própria empresa: normal. Outra empresa: 404.
            for papel in ('admin','supervisor'):
                assert (await c.get('/admin/v1/tenants/alpha/agentes',headers={'X-Role':papel,'X-Tenant-Scope':'alpha'})).status_code==200
                assert (await c.get('/admin/v1/tenants/alpha/agentes',headers={'X-Role':papel,'X-Tenant-Scope':'bravo'})).status_code==404
            # Plataforma sem cabeçalho: vê e muda qualquer empresa.
            assert (await c.get('/admin/v1/tenants/bravo/agentes',headers=PLAT)).status_code==200
            assert (await c.patch(f'/admin/v1/tenants/bravo/agentes/{b}',json={'nome':'B2'},headers=PLAT)).status_code==200
            # Rotas fora da API admin não mudam.
            assert (await c.get('/health')).status_code in (200,503)
    banco(cenario)


def test_2_credencial_unica_o_papel_vem_do_cabecalho(monkeypatch):
    """Hoje há uma chave do painel só (PANEL_SERVICE_KEY, e a próxima na rotação); o gateway não sabe se a sessão é de plataforma
    ou de empresa: quem tem a chave e manda X-Role: plataforma passa sem escopo. Sem a chave, nada passa, nem como plataforma."""
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,b=await _montar(conn,c,monkeypatch)
            assert (await c.get('/admin/v1/tenants/bravo/agentes',headers={'X-Role':'plataforma','X-Tenant-Scope':'alpha'})).status_code==200
            for chave in ('','Bearer errada','Bearer '+'x'*32,'Basic '+app.state.infra.panel_key):
                r=await c.get('/admin/v1/tenants/bravo/agentes',headers={**PLAT,'Authorization':chave})
                assert r.status_code==401,(chave[:6],r.status_code)
                r=await c.get('/admin/v1/tenants',headers={**PLAT,'Authorization':chave})
                assert r.status_code==401
    banco(cenario)


def test_3_modelos_agente_e_tenants_com_sessao_de_empresa(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,b=await _montar(conn,c,monkeypatch)
            globais=[('GET','/admin/v1/modelos-agente'),('GET','/admin/v1/modelos-agente/privado_bravo'),('POST','/admin/v1/modelos-agente'),
                     ('PATCH','/admin/v1/modelos-agente/privado_bravo'),('GET','/admin/v1/tenants'),('POST','/admin/v1/tenants'),
                     ('GET','/admin/v1/fila'),('GET','/admin/v1/auditoria/plataforma'),('GET','/admin/v1/segmentos'),('GET','/admin/v1/modulos')]
            # Sem o cabeçalho, sessão de empresa: 403 em todas as rotas globais.
            for h in SEM_ESCOPO:
                for metodo,rota in globais:
                    r=await c.request(metodo,rota,json={'id':'novo','nome':'x'},headers=h)
                    assert r.status_code==403,(h,metodo,rota,r.status_code)
            for papel in ('admin','membro'):
                h={'X-Role':papel,'X-Tenant-Scope':'alpha'}
                # Escritas da plataforma: 403, com ou sem cabeçalho.
                for metodo,rota in [('POST','/admin/v1/modelos-agente'),('PATCH','/admin/v1/modelos-agente/privado_bravo'),('POST','/admin/v1/tenants'),
                                    ('GET','/admin/v1/fila'),('GET','/admin/v1/auditoria/plataforma')]:
                    r=await c.request(metodo,rota,json={'id':'novo','nome':'x'},headers=h)
                    assert r.status_code==403,(papel,metodo,rota,r.status_code)
                # PAPEL1-a: rotas globais só da plataforma, também as leituras. A empresa vê os modelos em modelos-disponiveis:
                # os globais e os seus, nunca o modelo próprio de outra.
                for rota in ('/admin/v1/tenants','/admin/v1/modelos-agente','/admin/v1/modelos-agente/delivery_restaurante'):
                    assert (await c.get(rota,headers=h)).status_code==403,(papel,rota)
                if papel=='admin':
                    ids={m['id'] for m in (await c.get('/admin/v1/tenants/alpha/modelos-disponiveis',headers=h)).json()['itens']}
                    assert 'privado_bravo' not in ids and ids
                    assert 'privado_bravo' in {m['id'] for m in (await c.get('/admin/v1/tenants/bravo/modelos-disponiveis',headers=PLAT)).json()['itens']}
                assert (await c.get('/admin/v1/modelos-agente?tenant=bravo',headers=h)).status_code==404
                assert (await c.get('/admin/v1/modelos-agente/privado_bravo',headers=h)).status_code==403
                assert (await c.get('/admin/v1/modelos-agente/privado_bravo?tenant=bravo',headers=h)).status_code==404
            assert await conn.fetchval("SELECT count(*) FROM tenants WHERE id='novo'")==0
            assert await conn.fetchval("SELECT count(*) FROM modelos_agente WHERE id='novo'")==0
            # Plataforma sem cabeçalho: tudo.
            assert {t['id'] for t in (await c.get('/admin/v1/tenants',headers=PLAT)).json()['itens']}>={'alpha','bravo'}
            assert (await c.get('/admin/v1/modelos-agente/privado_bravo?tenant=bravo',headers=PLAT)).status_code==200
            assert (await c.get('/admin/v1/auditoria/plataforma',headers=PLAT)).status_code==200
    banco(cenario)
