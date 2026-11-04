"""CANAL-EXC (docs/ESPEC_CANAL_EXC.md): a plataforma exclui um canal desativado e todo o histórico das conversas dele;
os pedidos ficam sem dados pessoais. Gestor não exclui; canal ativo e confirmação errada são recusados."""
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
T='bykoji'
GESTOR={'X-Role':'gestor','X-Actor':'gestor@exemplo.test'}


def test_excluir_canal_apaga_historico_e_mantem_pedido_sem_dados_pessoais():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,api,_=await montar(conn,client,app)
            com=await conversa('5511900000071');sem=await conversa('5511900000072')
            canal=await conn.fetchval('SELECT canal_id FROM conversas WHERE conversation_id=$1',com)
            nome=await conn.fetchval('SELECT nome FROM tenant_canais WHERE id=$1',canal)
            # Pedido fechado na primeira conversa, com endereço e observação; nota da equipe na segunda.
            base=f'/carrinho/{com}'
            await api('POST',base+'/itens',json={'item_id':ids['GUE-0001'],'observacao':'sem cebola'})
            await api('PUT',base+'/entrega',json={'modalidade':'retirada','observacao':'chego às 12h'})
            await conn.execute("UPDATE tenant_unidades SET whatsapp_pedidos='5511900000091' WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
            r=await api('POST','/pedidos',json={'conversation_id':com});assert r.status_code==201,r.text
            numero=r.json()['numero']
            assert (await client.post(f'/admin/v1/tenants/{T}/conversas/{sem}/notas',json={'texto':'cliente pediu desconto'})).status_code in (200,201)
            antes_msgs=await conn.fetchval('SELECT count(*) FROM mensagens WHERE conversation_id=ANY($1::text[])',[com,sem]);assert antes_msgs>=2
            outro=await conn.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id NOT LIKE $2",T,f'{T}:{canal}:%')
            q=f'/admin/v1/tenants/{T}/canais/{canal}'
            # Só plataforma; canal ativo e confirmação errada recusados; nada muda.
            r=await client.request('DELETE',q,json={'confirmacao':nome},headers=GESTOR);assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(403,'papel_insuficiente')
            r=await client.request('DELETE',q,json={'confirmacao':nome});assert (r.status_code,r.json()['erro']['detalhes'])==(409,{'campo':'ativo'})
            assert (await client.patch(q,json={'ativo':False})).status_code==200
            r=await client.request('DELETE',q,json={'confirmacao':'outro nome'});assert (r.status_code,r.json()['erro']['detalhes']['campo'])==(400,'confirmacao')
            assert await conn.fetchval('SELECT count(*) FROM conversas WHERE canal_id=$1',canal)==2
            # Exclui.
            r=await client.request('DELETE',q,json={'confirmacao':f'  {nome} '});assert r.status_code==200,r.text
            assert r.json()=={'ok':True,'conversas_apagadas':1,'conversas_com_pedido':1,'mensagens_apagadas':antes_msgs,'pedidos_mantidos':1,
                              'contatos_apagados':2,'canal':'marcado'}
            # Nada da conversa sobra; a do pedido virou vazia e anônima.
            assert await conn.fetchval('SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id LIKE $2',T,f'{T}:{canal}:%')==0
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id NOT LIKE $2",T,f'{T}:{canal}:%')==outro
            for tabela in ('turnos','conversa_notas','carrinhos','atendimentos','skill_chamadas','skill_registro','catalogo_vistos','cardapio_links','avisos_internos'):
                assert await conn.fetchval(f'SELECT count(*) FROM {tabela} WHERE tenant_id=$1 AND conversation_id LIKE $2',T,f'{T}:{canal}:%')==0,tabela
            restante=await conn.fetch('SELECT * FROM conversas WHERE canal_id=$1',canal)
            assert len(restante)==1 and ':excluida_' in restante[0]['conversation_id'] and restante[0]['telefone'] is None and restante[0]['contato_id'] is None
            assert await conn.fetchval("SELECT count(*) FROM contatos WHERE tenant_id=$1 AND telefone IN ('5511900000071','5511900000072')",T)==0
            p=await conn.fetchrow('SELECT * FROM pedidos WHERE tenant_id=$1 AND numero=$2',T,numero)
            assert p['conversation_id']==restante[0]['conversation_id'] and float(p['total'])>0 and p['carrinho_id'] is None
            assert (p['endereco'],p['observacao'],p['cliente_nome'],p['cliente_telefone'])==(None,None,None,None)
            assert all(i.get('observacao') is None for i in (p['itens'] if isinstance(p['itens'],list) else __import__('json').loads(p['itens'])))
            texto_auditoria=str([dict(x) for x in await conn.fetch('SELECT alvo,dados FROM audit_log WHERE tenant_id=$1',T)])
            assert '5511900000071' not in texto_auditoria and '5511900000072' not in texto_auditoria
            a=await conn.fetchrow("SELECT actor,dados FROM audit_log WHERE tenant_id=$1 AND acao='canal.excluido'",T);assert a['actor']=='teste' and '"pedidos_mantidos": 1' in a['dados']
            # O canal some: lista, consulta, alteração, webhook e lista de conversas; credenciais saem.
            k=await conn.fetchrow('SELECT * FROM tenant_canais WHERE id=$1',canal)
            assert k['excluido_em'] and not k['ativo'] and k['webhook_secret'] is None and k['credenciais_enc'] in ({},'{}')
            assert canal not in [c['id'] for c in (await client.get(f'/admin/v1/tenants/{T}/canais?limite=99')).json()['itens']]
            assert (await client.get(q)).status_code==404 and (await client.patch(q,json={'ativo':True})).status_code==404
            assert (await client.request('DELETE',q,json={'confirmacao':nome})).status_code==404
            assert (await client.post(f'/webhooks/api/{canal}?s=x',json={})).status_code==404
            assert all(not c['conversation_id'].startswith(f'{T}:{canal}:') for c in (await client.get(f'/admin/v1/tenants/{T}/conversas?limite=100')).json()['itens'])
            # O pedido continua no painel.
            assert (await client.get(f'/admin/v1/tenants/{T}/pedidos/{numero}')).status_code==200
            # Canal sem nenhuma conversa: a linha é apagada.
            novo=(await client.post(f'/admin/v1/tenants/{T}/canais',json={'nome':'Sem uso','tipo':'api','agente_id':'bykoji_atendimento','ativo':False})).json()['id']
            r=await client.request('DELETE',f'/admin/v1/tenants/{T}/canais/{novo}',json={'confirmacao':'Sem uso'})
            assert r.json()['canal']=='apagado' and await conn.fetchval('SELECT count(*) FROM tenant_canais WHERE id=$1',novo)==0
    banco(cenario)
