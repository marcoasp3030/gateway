"""Respostas rápidas: quem vê, quem escreve, isolamento por empresa e atalho repetido."""
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar

T='bykoji';B=f'/admin/v1/tenants/{T}/respostas-rapidas'
FUNCIONARIO={'X-Role':'funcionario','X-Actor':'ana@exemplo.test'}
OUTRA={'X-Role':'funcionario','X-Actor':'bia@exemplo.test'}
SUPERVISOR={'X-Role':'supervisor','X-Actor':'sup@exemplo.test'}
PLATAFORMA={'X-Role':'plataforma','X-Actor':'plat@exemplo.test'}
pular=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')


@pular
def test_equipe_a_partir_de_supervisor_e_pessoal_para_qualquer_papel():
    async def cenario(conn):
        await migrar.aplicar(conn);_,c=preparar(conn)
        r=await c.post(B,headers=FUNCIONARIO,json={'atalho':'pix','texto':'Chave PIX','escopo':'equipe'})
        assert r.status_code==403
        r=await c.post(B,headers=SUPERVISOR,json={'atalho':'/PIX','texto':'  Chave PIX  ','escopo':'equipe'})
        assert r.status_code==201,r.text
        equipe=r.json();assert (equipe['atalho'],equipe['texto'],equipe['escopo'])==('pix','Chave PIX','equipe')
        r=await c.post(B,headers=FUNCIONARIO,json={'atalho':'oi','texto':'Oi {nome}'})
        assert r.status_code==201 and r.json()['escopo']=='pessoal' and r.json()['editavel']
        pessoal=r.json()
        vistos=(await c.get(B,headers=FUNCIONARIO)).json()['itens']
        assert {i['atalho']:i['editavel'] for i in vistos}=={'pix':False,'oi':True}
        # Funcionário usa a da equipe, mas não altera nem apaga.
        assert (await c.patch(f"{B}/{equipe['id']}",headers=FUNCIONARIO,json={'texto':'x'})).status_code==403
        assert (await c.delete(f"{B}/{equipe['id']}",headers=FUNCIONARIO)).status_code==403
        assert (await c.post(f"{B}/{equipe['id']}/uso",headers=FUNCIONARIO)).status_code==200
        assert (await c.patch(f"{B}/{equipe['id']}",headers=SUPERVISOR,json={'texto':'Nova chave'})).json()['texto']=='Nova chave'
        # A resposta pessoal não existe para mais ninguém, nem para a plataforma.
        for quem in (OUTRA,SUPERVISOR,PLATAFORMA):
            assert [i['atalho'] for i in (await c.get(B,headers=quem)).json()['itens']]==['pix']
            assert (await c.patch(f"{B}/{pessoal['id']}",headers=quem,json={'texto':'x'})).status_code==404
            assert (await c.delete(f"{B}/{pessoal['id']}",headers=quem)).status_code==404
            assert (await c.post(f"{B}/{pessoal['id']}/uso",headers=quem)).status_code==404
        assert (await c.delete(f"{B}/{pessoal['id']}",headers=FUNCIONARIO)).status_code==200
    banco(cenario)


@pular
def test_mais_usadas_primeiro_atalho_repetido_e_validacao():
    async def cenario(conn):
        await migrar.aplicar(conn);_,c=preparar(conn)
        a=(await c.post(B,headers=SUPERVISOR,json={'atalho':'a','texto':'1','escopo':'equipe'})).json()
        b=(await c.post(B,headers=SUPERVISOR,json={'atalho':'b','texto':'2','escopo':'equipe'})).json()
        await c.post(f"{B}/{b['id']}/uso",headers=FUNCIONARIO)
        assert [i['atalho'] for i in (await c.get(B,headers=FUNCIONARIO)).json()['itens']]==['b','a']
        assert (await c.post(B,headers=SUPERVISOR,json={'atalho':'a','texto':'3','escopo':'equipe'})).status_code==409
        assert (await c.patch(f"{B}/{b['id']}",headers=SUPERVISOR,json={'atalho':'a'})).status_code==409
        # O mesmo atalho pode existir na equipe e na lista pessoal, e em pessoas diferentes.
        assert (await c.post(B,headers=FUNCIONARIO,json={'atalho':'a','texto':'minha'})).status_code==201
        assert (await c.post(B,headers=OUTRA,json={'atalho':'a','texto':'dela'})).status_code==201
        assert (await c.post(B,headers=FUNCIONARIO,json={'atalho':'a','texto':'de novo'})).status_code==409
        for ruim in ({'atalho':'com espaço','texto':'x'},{'atalho':'','texto':'x'},{'atalho':'ok','texto':'   '},
                     {'atalho':'ok','texto':'x'*2001},{'atalho':'ok','texto':'x','escopo':'outro'},{'atalho':'ok','texto':'x','dono':'y'}):
            assert (await c.post(B,headers=SUPERVISOR,json=ruim)).status_code in (400,422),ruim
    banco(cenario)


@pular
def test_isolada_por_empresa_e_sem_texto_na_auditoria():
    async def cenario(conn):
        await migrar.aplicar(conn);_,c=preparar(conn)
        assert (await c.post('/admin/v1/tenants',headers=PLATAFORMA,json={'id':'outra','nome':'Outra'})).status_code==201
        r=(await c.post(B,headers=SUPERVISOR,json={'atalho':'x','texto':'segredo da casa','escopo':'equipe'})).json()
        O='/admin/v1/tenants/outra/respostas-rapidas'
        assert (await c.get(O,headers=SUPERVISOR)).json()['itens']==[]
        assert (await c.patch(f"{O}/{r['id']}",headers=SUPERVISOR,json={'texto':'x'})).status_code==404
        assert (await c.delete(f"{O}/{r['id']}",headers=SUPERVISOR)).status_code==404
        assert (await c.get('/admin/v1/tenants/nao-existe/respostas-rapidas',headers=SUPERVISOR)).status_code==404
        dados=await conn.fetch("SELECT dados::text,alvo FROM audit_log WHERE acao LIKE 'resposta_rapida%'")
        assert dados and not any('segredo' in d['dados']+d['alvo'] for d in dados)
    banco(cenario)
