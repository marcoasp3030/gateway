"""LGPD1 + origem do pedido (docs/ESPEC_AN1.md, decisão 4): no fechamento o pedido grava em que condição nasceu
(real, piloto, simulado, teste) e por onde entrou; os pedidos anteriores à marca ficam como teste."""
from pathlib import Path
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
T,A='bykoji','bykoji_atendimento'
PLAT={'X-Role':'plataforma'}
MIGRACAO=Path(__file__).resolve().parents[1]/'app'/'migrations'/'044_lgpd1_origem_do_pedido.sql'


def test_origem_e_canal_gravados_no_fechamento_e_pedidos_antigos_como_teste():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with c:
            conversa,ids,api,_=await montar(conn,c,app)
            await conn.execute("UPDATE tenant_unidades SET aceita_retirada=true WHERE tenant_id=$1",T)
            async def agente(**cfg):
                # Direto no banco de teste: a API exige Skyone cadastrado para ligar o modo live, e aqui só importa a marca.
                for k,v in cfg.items(): await conn.execute(f'UPDATE agentes SET {k}=$3 WHERE tenant_id=$1 AND id=$2',T,A,v)
            # Conversas criadas antes de mexer no agente (o "Simular" do painel precisa do agente como está).
            convs={u:await conversa(u) for u in ('5511900000101','5511900000102','5511900000103','5511900000104','5511900000105','5511900000106','5511900000107')}
            async def fechar(usuario,simulada):
                conv=convs[usuario]
                await conn.execute('UPDATE conversas SET simulada=$3 WHERE tenant_id=$1 AND conversation_id=$2',T,conv,simulada)
                assert (await api('POST',f'/carrinho/{conv}/itens',json={'item_id':ids['GUE-0001']})).status_code==200
                assert (await api('PUT',f'/carrinho/{conv}/entrega',json={'unidade':'patio_guedala','modalidade':'retirada'})).status_code==200
                r=await api('POST','/pedidos',json={'conversation_id':conv});assert r.status_code==201,r.text
                return await conn.fetchrow('SELECT origem,canal FROM pedidos WHERE tenant_id=$1 AND numero=$2',T,r.json()['numero'])
            origem=lambda p: (p['origem'],p['canal'])

            # Conversa simulada pelo painel: simulado, mesmo com tudo de verdade.
            await agente(send_mode='live',agente_mode='live',modo_piloto=False)
            assert origem(await fechar('5511900000101',True))==('simulado','api')
            # Cliente de verdade, agente e envio de verdade: real.
            assert origem(await fechar('5511900000102',False))==('real','api')
            # Envio de teste (dry) ou agente simulado (mock): simulado.
            await agente(send_mode='dry');assert origem(await fechar('5511900000103',False))[0]=='simulado'
            await agente(send_mode='live',agente_mode='mock');assert origem(await fechar('5511900000104',False))[0]=='simulado'
            # Piloto ligado: quem está na lista é piloto; quem não está (não deveria nem ser atendido) conta como real.
            await agente(agente_mode='live',modo_piloto=True,contatos_permitidos=['5511900000105'])
            assert origem(await fechar('5511900000105',False))[0]=='piloto'
            assert origem(await fechar('5511900000106',False))[0]=='real'
            # Canal uazapi é o WhatsApp.
            await conn.execute("UPDATE tenant_canais SET tipo='uazapi' WHERE tenant_id=$1 AND nome='API'",T)
            assert origem(await fechar('5511900000105',False))==('piloto','whatsapp')
            # Empresa de teste: teste, acima de tudo.
            await conn.execute("UPDATE tenants SET tenant_teste=true WHERE id=$1",T)
            assert origem(await fechar('5511900000107',False))[0]=='teste'
            await conn.execute("UPDATE tenants SET tenant_teste=false WHERE id=$1",T)

            # Painel: origem e canal no pedido, filtro por origem.
            lista=(await c.get(f'/admin/v1/tenants/{T}/pedidos',headers=PLAT)).json()['itens']
            assert {(i['origem'],i['canal']) for i in lista}>={('real','api'),('piloto','whatsapp'),('simulado','api'),('teste','whatsapp')}
            reais=(await c.get(f'/admin/v1/tenants/{T}/pedidos',params={'origem':'real'},headers=PLAT)).json()['itens']
            assert len(reais)==2 and {i['origem'] for i in reais}=={'real'}
            assert len((await c.get(f'/admin/v1/tenants/{T}/pedidos',params={'origem':'piloto,teste'},headers=PLAT)).json()['itens'])==3
            r=await c.get(f'/admin/v1/tenants/{T}/pedidos',params={'origem':'falso'},headers=PLAT)
            assert r.status_code==400 and r.json()['erro']['detalhes']=={'campo':'origem','motivo':'opcao'}
            numero=reais[0]['numero'];assert (await c.get(f'/admin/v1/tenants/{T}/pedidos/{numero}',headers=PLAT)).json()['origem']=='real'
            # O agente não vê a marca (a Skill responde como antes).
            k=(await api('GET','/pedidos',params={'conversation_id':reais[0]['conversation_id']})).json()
            assert 'origem' not in str(k)

            # Migração: pedido anterior à marca (gravado pela imagem antiga, sem origem nem canal) vira teste, com o canal da conversa.
            await conn.execute("UPDATE pedidos SET origem=NULL,canal=NULL WHERE tenant_id=$1 AND numero=$2",T,numero)
            await conn.execute(MIGRACAO.read_text())
            assert tuple(await conn.fetchrow('SELECT origem,canal FROM pedidos WHERE tenant_id=$1 AND numero=$2',T,numero))==('teste','whatsapp')
            # E não mexe nos que já têm marca.
            assert await conn.fetchval("SELECT count(*) FROM pedidos WHERE tenant_id=$1 AND origem='real'",T)==1
    banco(cenario)
