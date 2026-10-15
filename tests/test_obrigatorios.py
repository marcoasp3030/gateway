"""Null em campo obrigatório e erros de integridade sempre indicam o campo."""
import asyncpg,pytest
from erros_api import campo_integridade
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')

OBRIGATORIOS_AGENTE=['timeout_s','instrucoes_agente','provedor','skyone_auth_tipo','nome','status',
                     'skyone_estrategia','modo_resposta','modelo_id','modelo_versao']


def erro(r):
    return r.status_code,r.json()['erro']['codigo'],r.json()['erro']['detalhes'].get('campo')


def test_null_em_campo_obrigatorio_vira_400_com_campo_sem_tocar_o_banco(monkeypatch):
    from nucleo.banco import Repositorio
    original=Repositorio.alterar;gravacoes=[]
    async def alterar(self,*args):
        gravacoes.append(args);return await original(self,*args)
    monkeypatch.setattr(Repositorio,'alterar',alterar)
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,can=await criar(c)
            antes=(await c.get(f'/admin/v1/tenants/alpha/agentes/{a}')).json()
            for campo in OBRIGATORIOS_AGENTE:
                # Trocar o modelo do agente é só da plataforma (MULTI1-a).
                papel={'X-Role':'plataforma'} if campo.startswith('modelo_') else {}
                r=await c.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={campo:None},headers=papel)
                assert erro(r)==(400,'VALIDATION_ERROR',campo),(campo,r.text)
            # Junto com outros campos válidos, nada é gravado.
            r=await c.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'fallback':'novo','timeout_s':None})
            assert erro(r)==(400,'VALIDATION_ERROR','timeout_s')
            assert (await c.get(f'/admin/v1/tenants/alpha/agentes/{a}')).json()==antes
            assert gravacoes==[]  # recusado na validação, antes de qualquer UPDATE
            # Campos herdáveis continuam aceitando null (= usar padrão).
            r=await c.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'fallback':None,'agente_mode':None})
            assert r.status_code==200,r.text
            for rota,campo in [('/admin/v1/tenants/alpha','nome'),('/admin/v1/tenants/alpha','timezone'),
                               ('/admin/v1/tenants/alpha','tenant_teste'),
                               (f'/admin/v1/tenants/alpha/canais/{can}','nome'),
                               (f'/admin/v1/tenants/alpha/canais/{can}','ativo')]:
                r=await c.patch(rota,json={campo:None})
                assert erro(r)==(400,'VALIDATION_ERROR',campo),(rota,campo,r.text)
            r=await c.post('/admin/v1/tenants/alpha/unidades',json={'nome':None})
            assert erro(r)==(400,'VALIDATION_ERROR','nome')
            r=await c.post('/admin/v1/tenants/alpha/api-keys',json={'nome':'k','escopos':None})
            assert erro(r)==(400,'VALIDATION_ERROR','escopos')
    banco(cenario)


def test_integridade_do_banco_indica_o_campo():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,_=await criar(c)
            # UNIQUE: tenant repetido → 409 com o campo, sem ecoar o valor.
            r=await c.post('/admin/v1/tenants',json={'id':'alpha','nome':'outra'})
            assert erro(r)==(409,'CONFLICT','id') and 'outra' not in r.text
            # CHECK composto do banco: autenticação sem credencial cifrada.
            r=await c.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'skyone_auth_tipo':'bearer'})
            assert erro(r)==(400,'VALIDATION_ERROR','skyone_auth'),r.text
    banco(cenario)


def test_campo_integridade_so_devolve_nomes_conhecidos():
    def exc(classe,**attrs):
        e=classe('mensagem com valor-secreto');[setattr(e,k,v) for k,v in attrs.items()];return e
    assert campo_integridade(exc(asyncpg.NotNullViolationError,column_name='timeout_s',table_name='agentes'))=='timeout_s'
    assert campo_integridade(exc(asyncpg.CheckViolationError,constraint_name='agentes_debounce_s_check',table_name='agentes'))=='debounce_s'
    assert campo_integridade(exc(asyncpg.ForeignKeyViolationError,constraint_name='agentes_modelo_id_modelo_versao_fkey',table_name='agentes'))=='modelo_id'
    assert campo_integridade(exc(asyncpg.UniqueViolationError,constraint_name='tenants_pkey',table_name='tenants'))=='id'
    assert campo_integridade(exc(asyncpg.CheckViolationError,constraint_name='agentes_check1',table_name='agentes'))=='skyone_auth_cabecalho'
    assert campo_integridade(exc(asyncpg.NotNullViolationError,column_name='coluna_interna_x',table_name='t'))is None
