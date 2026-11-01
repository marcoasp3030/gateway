"""Responsável da conversa e notas internas: quem pode responder, quando a responsabilidade acaba, e o que a nota nunca alcança."""
import pytest
from nucleo.banco import Repositorio
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila

ANA={'X-Role':'funcionario','X-Actor':'ana@exemplo.test'}
BIA={'X-Role':'funcionario','X-Actor':'bia@exemplo.test'}
SUP={'X-Role':'supervisor','X-Actor':'sup@exemplo.test'}
GESTOR={'X-Role':'gestor','X-Actor':'gestor@exemplo.test'}
pular=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


async def montar(conn,cliente='5511900000501'):
    await migrar.aplicar(conn);app,c=preparar(conn);a,ca=await criar(c)
    await conn.execute("UPDATE tenant_canais SET ativo=true WHERE tenant_id='alpha'")
    conv=f'alpha:{ca}:{cliente}';ct='ct_'+cliente
    await conn.execute("INSERT INTO contatos(id,tenant_id,telefone) VALUES ($1,'alpha',$2)",ct,cliente)
    await conn.execute("INSERT INTO conversas(conversation_id,canal,tenant_id,agente_id,canal_id,contato_id,telefone) VALUES ($1,'api','alpha',$2,$3,$4,$5)",conv,a,ca,ct,cliente)
    B=f'/admin/v1/tenants/alpha/conversas/{conv}'
    return app,c,conv,ct,B


async def responsavel(c,conv,quem):
    itens=(await c.get('/admin/v1/tenants/alpha/conversas',headers=quem)).json()['itens']
    i=next(x for x in itens if x['conversation_id']==conv)
    return i['atribuida_a'],i['atribuida_minha']


@pular
def test_quem_assume_vira_responsavel_e_ninguem_toma_por_engano():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        assert (await c.post(B+'/assumir',headers=ANA,json={'minutos':30})).status_code==200
        assert await responsavel(c,conv,ANA)==('ana@exemplo.test',True)
        assert await responsavel(c,conv,BIA)==('ana@exemplo.test',False)
        # Bia não assume nem responde sem pedir; a conversa continua com Ana.
        for r in (await c.post(B+'/assumir',headers=BIA,json={'minutos':30}),await c.post(B+'/mensagens',headers=BIA,json={'texto':'oi'})):
            assert r.status_code==409 and r.json()['erro']['detalhes']['campo']=='atribuida',r.text
        assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1",conv)==0
        assert (await c.post(B+'/mensagens',headers=ANA,json={'texto':'oi'})).status_code==201
        # Transferir é a partir de supervisor (ESPEC_USUARIOS1), e sempre pedido explícito.
        assert (await c.post(B+'/assumir',headers=BIA,json={'minutos':30,'transferir':True})).status_code==403
        assert await responsavel(c,conv,BIA)==('ana@exemplo.test',False)
        assert (await c.post(B+'/assumir',headers=SUP,json={'minutos':30})).status_code==409
        assert (await c.post(B+'/assumir',headers=SUP,json={'minutos':30,'transferir':True})).status_code==200
        assert await responsavel(c,conv,SUP)==('sup@exemplo.test',True)
        assert (await c.post(B+'/mensagens',headers=ANA,json={'texto':'oi'})).status_code==409
    banco(cenario)


@pular
def test_responder_sem_assumir_tambem_atribui():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        assert (await c.post(B+'/mensagens',headers=ANA,json={'texto':'oi'})).status_code==201
        assert await responsavel(c,conv,ANA)==('ana@exemplo.test',True)
    banco(cenario)


@pular
def test_aba_minhas_mostra_so_as_da_pessoa():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        L='/admin/v1/tenants/alpha/conversas?minhas=true'
        assert (await c.get(L,headers=ANA)).json()['itens']==[]
        await c.post(B+'/assumir',headers=ANA,json={'minutos':30})
        assert [i['conversation_id'] for i in (await c.get(L,headers=ANA)).json()['itens']]==[conv]
        assert (await c.get(L,headers=BIA)).json()['itens']==[]
        # A conversa aberta se atualiza sozinha, mesmo fora da aba que a mostrava.
        U=f'/admin/v1/tenants/alpha/conversas?conversa={conv}'
        assert [i['conversation_id'] for i in (await c.get(U,headers=BIA)).json()['itens']]==[conv]
        assert (await c.get('/admin/v1/tenants/alpha/conversas?conversa=outra',headers=BIA)).json()['itens']==[]
    banco(cenario)


@pular
def test_responsabilidade_acaba_ao_devolver_vencer_a_pausa_ou_encerrar():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        await c.post(B+'/assumir',headers=ANA,json={'minutos':30})
        assert (await c.post(B+'/devolver',headers=SUP)).status_code==200
        assert await responsavel(c,conv,ANA)==(None,False)
        # Pausa vencida: a marca fica na linha, mas não vale nem trava ninguém.
        await c.post(B+'/assumir',headers=ANA,json={'minutos':30})
        await conn.execute("UPDATE conversas SET bot_pausado_ate=now()-interval '1 minute' WHERE conversation_id=$1",conv)
        assert await responsavel(c,conv,ANA)==(None,False)
        assert (await c.get('/admin/v1/tenants/alpha/conversas?minhas=true',headers=ANA)).json()['itens']==[]
        assert (await c.post(B+'/assumir',headers=BIA,json={'minutos':30})).status_code==200
        # Encerrar limpa de vez: conversa reaberta não volta presa a ninguém.
        await conn.execute("INSERT INTO atendimentos(id,tenant_id,conversation_id,numero,motivo_inicio) VALUES ($1,'alpha',$2,1,'primeiro_contato')",'at_'+'0'*24,conv)
        assert (await c.post(B+'/encerrar',headers=SUP)).status_code==200,'encerrar'
        assert await conn.fetchval('SELECT atribuida_a FROM conversas WHERE conversation_id=$1',conv) is None
    banco(cenario)


@pular
def test_transferir_para_colega_a_partir_de_supervisor():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        para={'para':'bia@exemplo.test'}
        await c.post(B+'/assumir',headers=ANA,json={'minutos':30})
        # Nem quem está com a conversa passa adiante sem supervisor.
        assert (await c.post(B+'/atribuir',headers=ANA,json=para)).status_code==403
        assert (await c.post(B+'/atribuir',headers=BIA,json=para)).status_code==403
        assert (await c.post(B+'/atribuir',headers=SUP,json=para)).status_code==200
        assert await responsavel(c,conv,BIA)==('bia@exemplo.test',True)
        assert (await c.post(B+'/atribuir',headers=SUP,json={'para':'ana@exemplo.test'})).status_code==200
        # Conversa com o agente passada a alguém sai do agente e da fila.
        await conn.execute("UPDATE conversas SET bot_pausado_ate=NULL,atribuida_a=NULL,aguardando_atendente_desde=now(),aguardando_motivo='erro_agente' WHERE conversation_id=$1",conv)
        assert (await c.post(B+'/atribuir',headers=SUP,json=para)).status_code==200
        r=await conn.fetchrow('SELECT aguardando_atendente_desde,bot_pausado_ate>now() AS pausada FROM conversas WHERE conversation_id=$1',conv)
        assert r['aguardando_atendente_desde'] is None and r['pausada']
        assert (await c.post(B+'/atribuir',headers=SUP,json={'para':'x'})).status_code in (400,422)
        await conn.execute('UPDATE conversas SET encerrada_em=now() WHERE conversation_id=$1',conv)
        assert (await c.post(B+'/atribuir',headers=SUP,json=para)).status_code==409
    banco(cenario)


@pular
def test_nota_e_so_da_equipe_nao_pausa_e_vale_com_a_conversa_de_outra_pessoa():
    async def cenario(conn):
        app,c,conv,_,B=await montar(conn)
        await c.post(B+'/assumir',headers=ANA,json={'minutos':30})
        antes=await conn.fetchrow('SELECT atualizado_em,bot_pausado_ate,atribuida_a FROM conversas WHERE conversation_id=$1',conv)
        r=await c.post(B+'/notas',headers=BIA,json={'texto':'  Cliente prefere ligação à tarde  '})
        assert r.status_code==201 and r.json()['texto']=='Cliente prefere ligação à tarde' and r.json()['minha'],r.text
        nid=r.json()['id']
        # Nada muda na conversa, e nada entra em mensagens: é de lá que saem o histórico do agente e o envio.
        assert dict(antes)==dict(await conn.fetchrow('SELECT atualizado_em,bot_pausado_ate,atribuida_a FROM conversas WHERE conversation_id=$1',conv))
        assert await conn.fetchval('SELECT count(*) FROM mensagens WHERE conversation_id=$1',conv)==0
        assert await Repositorio(conn).historico('alpha',conv,10**9,20)==[]
        vista=(await c.get(B+'/notas',headers=ANA)).json()['itens']
        assert [(n['autor'],n['minha'],n['apagavel']) for n in vista]==[('bia@exemplo.test',False,False)]
        assert (await c.delete(f'{B}/notas/{nid}',headers=ANA)).status_code==403
        assert (await c.delete(f'{B}/notas/{nid}',headers=SUP)).status_code==403
        assert (await c.delete(f'{B}/notas/{nid}',headers=GESTOR)).status_code==200
        for ruim in ({'texto':'   '},{'texto':'x'*2001},{'texto':'ok','autor':'outro'}):
            assert (await c.post(B+'/notas',headers=ANA,json=ruim)).status_code in (400,422)
        auditoria=await conn.fetch("SELECT dados::text FROM audit_log WHERE acao LIKE 'conversa.nota%'")
        assert auditoria and not any('ligação' in a['dados'] for a in auditoria)
    banco(cenario)


@pular
def test_nota_isolada_por_empresa_e_por_conversa():
    async def cenario(conn):
        _,c,conv,_,B=await montar(conn)
        nid=(await c.post(B+'/notas',headers=ANA,json={'texto':'x'})).json()['id']
        await c.post('/admin/v1/tenants',json={'id':'bravo','nome':'b'})
        O=B.replace('/alpha/','/bravo/')
        assert (await c.get(O+'/notas',headers=ANA)).status_code==404
        assert (await c.post(O+'/notas',headers=ANA,json={'texto':'x'})).status_code==404
        assert (await c.delete(f'{O}/notas/{nid}',headers=ANA)).status_code==404
    banco(cenario)


@pular
def test_lgpd_nota_entra_na_exportacao_e_sai_na_anonimizacao():
    async def cenario(conn):
        app,c,conv,ct,B=await montar(conn,'5511900000502')
        fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        await c.post(B+'/assumir',headers=ANA,json={'minutos':30})
        await c.post(B+'/notas',headers=ANA,json={'texto':'Mora na rua Tal, 100'})
        U=f'/admin/v1/tenants/alpha/contatos/{ct}'
        exportado=(await c.get(U+'/exportar',headers={'X-Role':'gestor'})).json()
        assert [n['texto'] for n in exportado['notas']]==['Mora na rua Tal, 100']
        r=await c.request('DELETE',U,json={'confirmacao':'5511900000502'},headers={'X-Role':'gestor'})
        assert r.status_code==200,r.text
        assert await conn.fetchval("SELECT count(*) FROM conversa_notas WHERE tenant_id='alpha'")==0
        assert await conn.fetchval("SELECT count(*) FROM conversas WHERE tenant_id='alpha' AND atribuida_a IS NOT NULL")==0
    banco(cenario)
