"""MULTI1-b: núcleo sem regra de delivery. O módulo de pedidos entra pelos ganchos do registro; o retrato do By Koji
(tests/test_multi1b_retrato.py) prova que nada mudou para ele. Aqui: a anonimização (LGPD), o núcleo sem importar o módulo
e o agente sem módulos sem nada de delivery no pacote."""
import json,re
from pathlib import Path
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_pipeline_multi import RedisFila,ativo
from test_fase4_pedidos import montar

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
APP=Path(__file__).resolve().parents[1]/'app'
T,A='bykoji','bykoji_atendimento'
PLAT={'X-Role':'plataforma'}


def test_nucleo_nao_conhece_carrinho_nem_pedido():
    """Núcleo, adaptador do agente, canais e API admin não importam os módulos nem leem as tabelas deles."""
    proibido=re.compile(r'from modulos\.(pedidos|catalogo)|import modulos\.(pedidos|catalogo)|\b(FROM|JOIN|UPDATE|INTO)\s+(carrinhos|carrinho_itens|pedidos|pedido_eventos|catalogo_itens)\b')
    achados=[]
    for pasta in ('nucleo','agentes','canais','admin'):
        for arquivo in (APP/pasta).glob('*.py'):
            for n,linha in enumerate(arquivo.read_text().splitlines(),1):
                if proibido.search(linha): achados.append(f'{arquivo.relative_to(APP)}:{n}: {linha.strip()[:100]}')
    assert achados==[],achados
    from modulos import REGISTRO,declarados
    from nucleo import skill_registro,botoes
    from agentes import skyone
    # O que era fixo no núcleo agora é declarado pelo módulo.
    assert 'pedido_fechado' in skyone.ACOES and 'pedido_fechado' in botoes.ACOES_COM_BOTAO and declarados('motivos_com_recusa')==('pedido_para_confirmar',)
    assert skill_registro.SKILLS[('POST','/api/v1/pedidos')]=='fechar_pedido' and skill_registro.SKILLS[('GET','/api/v1/catalogo/busca')]=='buscar_catalogo'
    assert skill_registro.SKILLS[('GET','/api/v1/unidades')]=='info_unidades' and len(skill_registro.SKILLS)==14   # + ler_documento (ARQ1-a1, núcleo) e consultar_entrega (G4-b)
    assert set(REGISTRO['pedidos'].comportamentos)=={'carrinho_expira_h','acoes_recentes_min'}


def test_agente_sem_modulos_nao_recebe_nada_de_delivery():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with c:
            await criar(c)
            from agentes import skyone
            from nucleo.banco import Repositorio
            await c.post('/admin/v1/tenants/alpha/unidades',json={'nome':'Centro','codigo':'centro'})
            g=(await c.post('/admin/v1/tenants/alpha/agentes',json={'nome':'Geral','modelo_id':'atendimento_geral'},headers=PLAT)).json()['id']
            d=(await c.post('/admin/v1/tenants/alpha/agentes',json={'nome':'Delivery','modelo_id':'delivery_restaurante'},headers=PLAT)).json()['id']
            can=(await c.post('/admin/v1/tenants/alpha/canais',json={'nome':'G','tipo':'api','agente_id':g})).json()['id']
            await ativo(c,'alpha',g,can)
            conv=(await c.post(f'/admin/v1/tenants/alpha/agentes/{g}/simular',json={'canal_id':can,'identificador':'5511900000050','texto':'oi'})).json()['conversation_id']
            repo=Repositorio(conn)
            async def pacote(agente):
                cfg=await app.state.configuracoes.efetiva('alpha',agente)
                return await skyone.pacote(repo,'alpha',conv,10**9,'oi',cfg)
            p=await pacote(g)
            u=p['unidades'][0]
            for campo in ('taxa_entrega','pedido_minimo','area_entrega','aceita_retirada','formas_pagamento','tem_cardapio','itens_no_cardapio'): assert campo not in u,campo
            assert {'codigo','endereco','telefone','regras','aberta_agora','horarios'}<=set(u)
            assert not {'pedidos','catalogo'}&set(p['contexto_modulos']) and p['acoes_recentes']==[] and p['agente']['modulos']==[]
            # O mesmo cadastro, com um agente de delivery: os campos voltam.
            await conn.execute('UPDATE conversas SET agente_id=$2 WHERE conversation_id=$1',conv,d)
            u=(await pacote(d))['unidades'][0]
            assert {'taxa_entrega','pedido_minimo','area_entrega','aceita_retirada','formas_pagamento','tem_cardapio','itens_no_cardapio'}<=set(u)
            # Guardas de pedido não valem para o agente geral: "pedido feito" sem módulo não é barrado.
            from nucleo.pipeline import Pipeline
            assert Pipeline._recados_de_recusa(app.state.pipeline,await app.state.configuracoes.efetiva('alpha',g))=={}
            assert set(Pipeline._recados_de_recusa(app.state.pipeline,await app.state.configuracoes.efetiva('alpha',d)))=={'pedido_para_confirmar'}
    banco(cenario)


def test_anonimizacao_tira_dados_pessoais_de_carrinho_pedido_eventos_avisos_e_solicitacoes():
    """LGPD03 com o módulo de pedidos pelo gancho: tudo o que o módulo guarda da conversa perde os dados pessoais;
    os números (pedido, total, status) e a empresa vizinha ficam como estavam."""
    telefone='5511987650001';nome='Joana Teste';endereco='Rua das Acácias, 123';obs='portão azul'
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with c:
            conversa,ids,api,_=await montar(conn,c,app)
            conv=await conversa(telefone)
            await conn.execute('UPDATE conversas SET nome=$3 WHERE tenant_id=$1 AND conversation_id=$2',T,conv,nome)
            base=f'/carrinho/{conv}'
            assert (await api('POST',base+'/itens',json={'item_id':ids['GUE-0001'],'quantidade':2,'observacao':obs})).status_code==200
            await conn.execute("UPDATE tenant_unidades SET aceita_retirada=true WHERE tenant_id=$1",T)
            r=await api('PUT',base+'/entrega',json={'unidade':'patio_guedala','modalidade':'retirada','observacao':obs});assert r.status_code==200,r.text
            r=await api('POST','/pedidos',json={'conversation_id':conv});assert r.status_code==201,r.text
            numero=r.json()['numero']
            # Um segundo carrinho, aberto, com endereço; evento do pedido com motivo; aviso e solicitação da conversa.
            await api('POST',base+'/itens',json={'item_id':ids['GUE-0002']})
            await conn.execute('UPDATE carrinhos SET endereco=$3,observacao=$4 WHERE tenant_id=$1 AND conversation_id=$2 AND status=$5',T,conv,endereco,obs,'aberto')
            await conn.execute("UPDATE pedido_eventos SET motivo=$2 WHERE tenant_id=$1",T,f'cliente {nome} pediu')
            await conn.execute("""INSERT INTO solicitacoes(tenant_id,numero,conversation_id,agente_id,motivo,motivo_informado,tipo,dados,origem)
                VALUES ($1,'S-0001',$2,$3,'evento_ou_encomenda',$4,'evento',$5::jsonb,'agente')""",T,conv,A,f'{nome} quer festa',json.dumps({'local':endereco}))
            # A mesma pessoa em outra empresa não muda.
            b,cb=await criar(c,'bravo');await ativo(c,'bravo',b,cb)
            await c.post('/admin/v1/tenants/bravo/agentes/'+b+'/simular',json={'canal_id':cb,'identificador':telefone,'texto':'oi'})
            antes_bravo=await conn.fetchval("SELECT count(*) FROM conversas WHERE tenant_id='bravo' AND telefone=$1",telefone)
            cid=await conn.fetchval('SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
            r=await c.request('DELETE',f'/admin/v1/tenants/{T}/contatos/{cid}',json={'confirmacao':telefone},headers=PLAT);assert r.status_code==200,r.text
            novo=await conn.fetchval('SELECT conversation_id FROM conversas WHERE tenant_id=$1 AND contato_id=$2',T,cid)
            assert novo!=conv and telefone not in novo
            pessoais=(telefone,nome,'Joana',endereco,obs,'Acácias')
            for tabela,filtro in (('carrinhos','conversation_id=$2'),('carrinho_itens','carrinho_id IN (SELECT id FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2)'),
                                  ('pedidos','conversation_id=$2'),('pedido_eventos','pedido_id IN (SELECT id FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2)'),
                                  ('avisos_internos','conversation_id=$2'),('solicitacoes','conversation_id=$2'),
                                  # LGPD1: registros das Skills e do catálogo passam para o identificador novo.
                                  ('skill_chamadas','conversation_id=$2'),('skill_registro','conversation_id=$2'),('catalogo_vistos','conversation_id=$2')):
                linhas=await conn.fetch(f'SELECT row_to_json(t)::text AS dado FROM {tabela} t WHERE tenant_id=$1 AND {filtro}',T,novo)
                assert linhas or tabela in ('avisos_internos',),tabela
                for l in linhas:
                    # LGPD1: inclusive a observação de cada item (no carrinho e na lista de itens do pedido).
                    for dado in pessoais: assert dado not in l['dado'],(tabela,dado)
                # Nada ficou para trás com o identificador antigo.
                assert not await conn.fetchval(f"SELECT count(*) FROM {tabela} t WHERE tenant_id=$1 AND row_to_json(t)::text LIKE '%'||$2||'%'",T,telefone),tabela
            assert not await conn.fetchval("SELECT count(*) FROM carrinho_itens WHERE tenant_id=$1 AND observacao IS NOT NULL",T)
            itens=json.loads(await conn.fetchval('SELECT itens FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2',T,novo))
            assert [(i['nome'],i['quantidade'],i['observacao']) for i in itens]==[('Edamame',2,None)]   # a lista continua com o mesmo formato
            p=await conn.fetchrow('SELECT numero,status,total,cliente_nome,cliente_telefone,endereco,observacao FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2',T,novo)
            assert (p['numero'],p['status'],float(p['total']))==(numero,'aguardando_loja',68.0) and p['cliente_nome'] is None and p['cliente_telefone'] is None
            assert await conn.fetchval("SELECT count(*) FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2",T,novo)==2
            assert await conn.fetchval("SELECT bool_and(por='anonimizado' AND motivo IS NULL) FROM pedido_eventos WHERE tenant_id=$1",T)
            assert await conn.fetchval("SELECT count(*) FROM conversas WHERE tenant_id='bravo' AND telefone=$1",telefone)==antes_bravo==1
    banco(cenario)
