"""PAPEL1-a (docs/ESPEC_PAPEL1.md): toda rota admin na tabela de papéis, papel exato (e os nomes antigos na transição), nível mínimo por
rota com 403 e auditoria, credencial de plataforma (CHAVES1, fase A e exigência ligada), modelos da empresa e as regras por campo
(técnicos da empresa, supervisor no item, cancelamento de pedido confirmado)."""
import json
import httpx
import pytest
from cryptography.fernet import Fernet
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
from test_enc1a import cenario_by_koji,T
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


def com(papel,escopo='alpha'):
    return {'X-Role':papel,'X-Tenant-Scope':escopo}


def motivo(r):
    return r.json()['erro']['detalhes']['motivo']


def test_toda_rota_admin_esta_na_tabela_e_toda_regra_tem_rota():
    from nucleo import permissoes
    async def cenario(conn):
        app,_=preparar(conn)
        rotas=set()
        for r in app.routes:
            caminho=getattr(r,'path','')
            if not caminho.startswith('/admin/v1/'): continue
            for m in (getattr(r,'methods',None) or set())-{'HEAD'}:
                rotas.add((m,caminho[len('/admin/v1'):]))
        faltando=sorted(k for k in rotas if '{entidade}' not in k[1] and k not in permissoes.PERMISSOES)
        assert not faltando,faltando
        sobrando=sorted(k for k in permissoes.PERMISSOES if k not in rotas)
        assert not sobrando,sobrando
        assert all(v in permissoes.NIVEIS for v in permissoes.PERMISSOES.values())
        # Ajuste de 28/10: histórico de conversas para todos os papéis de empresa (o filtro por unidade é do PAPEL1-b).
        for rota in ('/tenants/{tenant}/conversas','/tenants/{tenant_id}/conversas/{conversa_id}/mensagens','/tenants/{tenant}/conversas/{conv}/atendimentos',
                     '/tenants/{tenant}/conversas/{conv}/follow-ups','/tenants/{tenant}/eventos','/tenants/{tenant}/eventos/cursor'):
            assert permissoes.minimo('GET',rota)=='funcionario',rota
        # Rota fora da tabela: só a plataforma.
        assert permissoes.minimo('GET','/admin/v1/rota-nova')=='plataforma'
        assert permissoes.minimo('DELETE','/tenants/{tenant_id}/{entidade}/{item_id}',{'entidade':'agentes'})=='plataforma'
    banco(cenario)


def test_papel_exato_legados_e_nivel_minimo(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        monkeypatch.setenv('ESCOPO_EMPRESA_OBRIGATORIO','on')
        async with c:
            await criar(c)
            q='/admin/v1/tenants/alpha'
            # Comparação exata: maiúscula, espaço, vazio ou papel inventado → 403 antes da rota.
            for v in ('Gestor','gestor ','','dono'):
                r=await c.get(q,headers=com(v))
                assert (r.status_code,motivo(r))==(403,'papel_desconhecido'),v
            for v in ('gestor','supervisor','funcionario','admin','membro'):
                assert (await c.get(q,headers=com(v))).status_code==200,v
            # Nível mínimo: funcionário não lista agentes; supervisor lista mas não cria; gestor cria.
            r=await c.get('/admin/v1/tenants/alpha/agentes',headers=com('funcionario'))
            assert (r.status_code,motivo(r),r.json()['erro']['detalhes']['minimo'])==(403,'papel_insuficiente','supervisor')
            assert (await c.get('/admin/v1/tenants/alpha/agentes',headers=com('membro'))).status_code==403
            assert (await c.get('/admin/v1/tenants/alpha/agentes',headers=com('supervisor'))).status_code==200
            r=await c.post('/admin/v1/tenants/alpha/agentes',json={'nome':'B','modelo_id':'generico_teste'},headers=com('supervisor'))
            assert (r.status_code,r.json()['erro']['detalhes']['minimo'])==(403,'gestor')
            assert (await c.post('/admin/v1/tenants/alpha/agentes',json={'nome':'B','modelo_id':'generico_teste'},headers=com('admin'))).status_code==201
            # Rota global: só a plataforma, mesmo para o gestor.
            r=await c.get('/admin/v1/tenants',headers=com('gestor'))
            assert (r.status_code,motivo(r))==(403,'papel_insuficiente')
            assert (await c.get('/admin/v1/tenants',headers={'X-Role':'plataforma'})).status_code==200
            # Auditoria das recusas: uma por minuto por sessão, rota e motivo; sem ids no alvo.
            await c.get('/admin/v1/tenants',headers=com('gestor'))
            reg=await conn.fetch("SELECT tenant_id,alvo,dados FROM audit_log WHERE acao='acesso.negado' AND alvo='/tenants'")
            assert len(reg)==1 and json.loads(reg[0]['dados'])=={'rota':'GET /tenants','papel':'gestor','motivo':'papel_insuficiente'}
            reg=await conn.fetch("SELECT tenant_id,alvo FROM audit_log WHERE acao='acesso.negado' AND alvo='/tenants/{tenant_id}/agentes'")
            assert reg and all(x['tenant_id']=='alpha' for x in reg)
            # Escopo da empresa agora obrigatório por padrão: sem X-Tenant-Scope, 403.
            assert (await c.get(q,headers={'X-Role':'gestor'})).status_code==403
            # Nomes antigos desligados: admin e membro viram papel desconhecido.
            monkeypatch.setenv('PAPEIS_LEGADOS','off')
            for v in ('admin','membro'):
                r=await c.get(q,headers=com(v))
                assert (r.status_code,motivo(r))==(403,'papel_desconhecido'),v
            assert (await c.get(q,headers=com('gestor'))).status_code==200
    banco(cenario)


def test_credencial_de_plataforma_fase_a_e_exigencia(monkeypatch):
    from plataforma import Infra,criar_app
    from test_admin import Pool,Redis
    PLAT,EMPRESA='d'*32,'a'*32
    with pytest.raises(ValueError): Infra(EMPRESA,'b'*32,'c'*32,Fernet.generate_key().decode(),panel_platform_key=EMPRESA).validar()
    async def cenario(conn):
        await migrar.aplicar(conn)
        infra=Infra(EMPRESA,'b'*32,'c'*32,Fernet.generate_key().decode(),panel_platform_key=PLAT)
        infra.validar()
        app=criar_app(infra,Pool(conn),Redis())
        c=httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url='http://teste')
        async with c:
            def h(chave,papel,**extra): return {'Authorization':'Bearer '+chave,'X-Actor':'teste','X-Role':papel,**extra}
            assert (await c.get('/admin/v1/tenants',headers=h(PLAT,'plataforma'))).status_code==200
            # Fase A: chave de empresa com papel de plataforma ainda vale (com aviso no log).
            assert (await c.get('/admin/v1/tenants',headers=h(EMPRESA,'plataforma'))).status_code==200
            # Chave errada: 401 da rota, sem olhar o papel.
            assert (await c.get('/admin/v1/tenants',headers=h('e'*32,'plataforma'))).status_code==401
            assert (await c.get('/admin/v1/tenants',headers=h('e'*32,'inventado'))).status_code==401
            # Exigência ligada: só a chave de plataforma dá o papel de plataforma.
            monkeypatch.setenv('CHAVE_PLATAFORMA_OBRIGATORIA','on')
            r=await c.get('/admin/v1/tenants',headers=h(EMPRESA,'plataforma'))
            assert (r.status_code,motivo(r))==(403,'papel_sem_credencial')
            assert (await c.get('/admin/v1/tenants',headers=h(PLAT,'plataforma'))).status_code==200
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE acao='acesso.negado' AND dados->>'motivo'='papel_sem_credencial'")==1
    banco(cenario)


def test_modelos_disponiveis_e_campos_tecnicos_da_empresa():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            await criar(c)
            r=await c.get('/admin/v1/tenants/alpha/modelos-disponiveis',headers=com('gestor'))
            assert r.status_code==200
            itens={m['id']:m for m in r.json()['itens']}
            assert 'generico_teste' in itens and set(itens['generico_teste'])=={'id','versao','tenant_id','nome','tipo','segmento','modulos_permitidos','capacidades'}
            assert (await c.get('/admin/v1/tenants/alpha/modelos-disponiveis',headers=com('supervisor'))).status_code==403
            # Empresa: o gestor muda o nome, não os campos técnicos.
            for campo,valor in (('status','suspenso'),('plano','pro'),('limite_turnos_mes',1),('retencao_dias',1),('tenant_teste',True),('timezone','UTC')):
                r=await c.patch('/admin/v1/tenants/alpha',json={campo:valor},headers=com('gestor'))
                assert (r.status_code,r.json()['erro']['detalhes'])==(403,{'motivo':'so_plataforma','campos':[campo]}),campo
            assert (await c.patch('/admin/v1/tenants/alpha',json={'nome':'Alpha Nova'},headers=com('gestor'))).status_code==200
            assert (await c.patch('/admin/v1/tenants/alpha',json={'nome':'Alpha'},headers=com('supervisor'))).status_code==403
            assert (await c.patch('/admin/v1/tenants/alpha',json={'timezone':'America/Sao_Paulo'},headers={'X-Role':'plataforma'})).status_code==200
    banco(cenario)


def test_supervisor_no_item_e_cancelamento_de_pedido_confirmado(tmp_path):
    async def corpo(pool,c,ids,pedido_fechado,**_):
        sup,fun,ges=com('supervisor',T),com('funcionario',T),com('gestor',T)
        q=f'/admin/v1/tenants/{T}/catalogo/itens/{ids["GUE-0001"]}'
        # Item: o supervisor só mexe na disponibilidade; o resto é do gestor; funcionário não edita.
        r=await c.patch(q,json={'preco':70.0,'nome':'Outro'},headers=sup)
        assert (r.status_code,r.json()['erro']['detalhes'])==(403,{'motivo':'papel_insuficiente','campos':['nome','preco']})
        assert (await c.patch(q,json={'preco':70.0},headers=fun)).status_code==403
        assert (await c.patch(q,json={'preco':70.0},headers=ges)).status_code==200
        # Pedido: funcionário cancela só o que a unidade ainda não confirmou.
        _,numero=await pedido_fechado('5511900000611')
        r=await c.post(f'/admin/v1/tenants/{T}/pedidos/{numero}/confirmar',headers=fun)
        assert r.status_code==200 and r.json()['status']=='confirmado',r.text
        cancelar={'status':'cancelado','motivo':'Cliente desistiu'}
        assert (await c.post(f'/admin/v1/tenants/{T}/pedidos/{numero}/status',json=cancelar,headers=fun)).status_code==403
        assert await pool.fetchval('SELECT status FROM pedidos WHERE tenant_id=$1 AND numero=$2',T,numero)=='confirmado'
        assert (await c.post(f'/admin/v1/tenants/{T}/pedidos/{numero}/status',json=cancelar,headers=sup)).status_code==200
        _,outro=await pedido_fechado('5511900000612')
        assert (await c.post(f'/admin/v1/tenants/{T}/pedidos/{outro}/status',json=cancelar,headers=fun)).status_code==200
    cenario_by_koji(tmp_path,corpo)
