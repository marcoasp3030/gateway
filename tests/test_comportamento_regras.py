"""Análise de comportamento (decisões de 26/10/2026): guarda da ação (falha 4), pedido com a unidade fechada (falha 5), emojis "nenhum"
(falha 7), campos obsoletos (falha 3) e invalidação do cache de configuração entre processos (falha 9)."""
import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from test_migrations import DSN
from test_enc1a import cenario_by_koji,T,A,H

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


def test_sem_emojis_tira_so_os_emojis():
    from nucleo.pipeline import sem_emojis
    assert sem_emojis('Olá! 😊 Tudo bem?')==('Olá! Tudo bem?',1)
    assert sem_emojis('Família 👨‍👩‍👧 e 👍🏽 e bandeira 🇧🇷!')==('Família e e bandeira !',3)
    assert sem_emojis('1️⃣ Edamame\n2️⃣ Guioza')==('Edamame\nGuioza',2)
    assert sem_emojis('❤️ ✅ ⭐')==('',3)
    assert sem_emojis('Ação © 2026 — R$ 68,00 (até 22h)')==('Ação © 2026 — R$ 68,00 (até 22h)',0)
    assert sem_emojis('Sem emoji aqui.')==('Sem emoji aqui.',0)


def test_guarda_da_acao_refaz_uma_vez_e_segura(tmp_path):
    async def corpo(pool,c,p,turno,fala,pacotes,**_):
        async def ultimo(conv):
            return dict(await pool.fetchrow("SELECT * FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 AND status<>'refeito' ORDER BY id DESC LIMIT 1",T,conv))
        # Motivo válido do perfil: nada muda (uma consulta só).
        conv=await turno('5511900000501','quero falar com alguém',fala('Vou chamar a equipe.',acao='transferir_humano',motivo='reclamacao'))
        assert len(pacotes)==1 and (await ultimo(conv))['acao_fora_da_regra'] is None
        # Motivo fora da lista, duas vezes: refaz com aviso; repetiu → transferir por pediu_humano, texto mantido.
        conv=await turno('5511900000502','me passa pra alguém',fala('Transferindo.',acao='transferir_humano',motivo='inventado'),
                         fala('Transferindo de novo.',acao='transferir_humano',motivo='inventado'))
        assert len(pacotes)==2 and pacotes[0]['aviso_do_gateway'] is None and 'pediu_humano' in pacotes[1]['aviso_do_gateway']
        t=await ultimo(conv);r=json.loads(t['resposta'])
        assert (t['acao'],t['acao_fora_da_regra'],r['motivo'],r['resposta'])==('transferir_humano','segurado','pediu_humano',['Transferindo de novo.'])
        assert r['segurado']['motivo']=='inventado'
        assert await pool.fetchval('SELECT aguardando_motivo FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv)=='transferido_pelo_agente'
        assert await pool.fetchval("SELECT count(*) FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 AND acao_fora_da_regra='refeito'",T,conv)==1
        # notificar_equipe com o recurso desligado (padrão): refaz; o agente corrige → segue a correção.
        conv=await turno('5511900000503','quero fazer um evento',fala('Aviso a equipe.',acao='notificar_equipe',motivo='evento_ou_encomenda'),
                         fala('Me conta a data e quantas pessoas.'))
        t=await ultimo(conv)
        assert (t['acao'],t['acao_fora_da_regra'])==('nenhuma',None) and json.loads(t['resposta'])['resposta']==['Me conta a data e quantas pessoas.']
        # Insistiu em notificar_equipe desligado: vira "nenhuma", nenhuma solicitação registrada.
        conv=await turno('5511900000504','evento de novo',fala('Aviso a equipe.',acao='notificar_equipe'),fala('Aviso a equipe.',acao='notificar_equipe'))
        t=await ultimo(conv)
        assert (t['acao'],t['acao_fora_da_regra'])==('nenhuma','segurado')
        assert await pool.fetchval('SELECT count(*) FROM solicitacoes WHERE tenant_id=$1 AND conversation_id=$2',T,conv)==0
        assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='turno.acao_fora_da_regra_segurada'",T)==2
    cenario_by_koji(tmp_path,corpo)


def test_pedido_para_ja_com_unidade_fechada_recusado(tmp_path):
    async def corpo(pool,c,api,ids,turno,fala,**_):
        cli='5511900000511'
        async def adicionar(payload):
            assert (await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})).status_code==200
        conv=await turno(cli,'quero 2 edamame',fala('Anotei.',adicionar))
        assert (await api('PUT',f'/carrinho/{conv}/entrega',json={'unidade':'patio_guedala','modalidade':'retirada'})).status_code==200
        # Pausada (mesmo dentro do horário): recusa unidade_fechada, com o motivo da unidade.
        await pool.execute("UPDATE tenant_unidades SET pausa_indefinida=true WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        r=await api('POST','/pedidos',json={'conversation_id':conv})
        assert r.status_code==422 and r.json()['erro']['detalhes']['motivo']=='unidade_fechada' and r.json()['erro']['detalhes']['motivo_unidade']=='pausa'
        await pool.execute("UPDATE tenant_unidades SET pausa_indefinida=false WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        # Fechada hoje por exceção: recusa, com a próxima abertura.
        uid=await pool.fetchval("SELECT id FROM tenant_unidades WHERE tenant_id=$1 AND codigo='patio_guedala'",T)
        hoje=datetime.now(ZoneInfo('America/Sao_Paulo')).date()
        await pool.execute('INSERT INTO unidade_excecoes(tenant_id,unidade_id,data,fechado,faixas,motivo) VALUES ($1,$2,$3,true,$4::jsonb,$5)',T,uid,hoje,'[]','Teste')
        r=await api('POST','/pedidos',json={'conversation_id':conv})
        assert r.status_code==422 and r.json()['erro']['detalhes']['motivo']=='unidade_fechada' and r.json()['erro']['detalhes']['proxima_abertura']
        await pool.execute('DELETE FROM unidade_excecoes WHERE tenant_id=$1 AND unidade_id=$2',T,uid)
        # Aberta: fecha normalmente.
        assert (await api('POST','/pedidos',json={'conversation_id':conv})).status_code==201
    cenario_by_koji(tmp_path,corpo)


def test_emojis_nenhum_tira_dos_baloes_e_registra(tmp_path):
    async def corpo(pool,c,p,turno,**_):
        await pool.execute("""UPDATE agentes SET perfil=coalesce(perfil,'{}'::jsonb)||'{"emojis":"nenhum"}'::jsonb WHERE tenant_id=$1 AND id=$2""",T,A)
        p.cfg.invalidar(T)
        cli='5511900000522'
        async def resposta(payload): return {'resposta':['Olá! 😊👍 Tudo bem? 🍣','👍','Pode escolher 1️⃣ ou 2️⃣.'],'acao':'nenhuma'}
        conv=await turno(cli,'oi',resposta)
        t=await pool.fetchrow('SELECT id,resposta,emojis_removidos FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1',T,conv)
        assert t['emojis_removidos']==6 and json.loads(t['resposta'])['resposta'][1]=='👍'
        saidas=[r['conteudo'] for r in await pool.fetch("SELECT conteudo FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' AND origem='bot' ORDER BY id",T,conv)]
        assert saidas==['Olá! Tudo bem?','Pode escolher ou .']
        # Perfil com emojis: nada muda.
        await pool.execute("""UPDATE agentes SET perfil=perfil||'{"emojis":"moderado"}'::jsonb WHERE tenant_id=$1 AND id=$2""",T,A)
        p.cfg.invalidar(T)
        conv=await turno('5511900000523','oi',resposta)
        assert await pool.fetchval('SELECT emojis_removidos FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1',T,conv) is None
        assert (await pool.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' AND origem='bot'",T,conv))==3
    cenario_by_koji(tmp_path,corpo)


def test_campos_obsoletos_marcados_e_aceitos(tmp_path):
    async def corpo(pool,c,**_):
        r=(await c.get(f'/admin/v1/tenants/{T}/agentes/{A}/atendimento',headers=H)).json()['esquema']['comportamentos']
        for campo in ('aviso_demora','media_wait_s','fora_do_horario_aceita_agendado'):
            assert r[campo]['obsoleto'] is True and r[campo]['disponivel'] is False,campo
        assert 'obsoleto' not in r['debounce_s']
        # O painel antigo continua conseguindo mandar (aceito e ignorado pelo gateway).
        assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'aviso_demora':'Um instante.'},headers=H)).status_code==200
        q=f'/admin/v1/tenants/{T}/agentes/{A}/atendimento/rascunho'
        assert (await c.put(q,json={'comportamentos':{'media_wait_s':10,'fora_do_horario_aceita_agendado':True}},headers=H)).status_code==200
    cenario_by_koji(tmp_path,corpo)


def test_publicar_limpa_o_cache_dos_outros_processos(tmp_path):
    """O worker (outra instância de Configuracoes, com a escuta) vê a mudança na hora, sem esperar os 30 s."""
    async def corpo(pool,c,p,**_):
        from nucleo.banco import Repositorio
        from nucleo.configuracao import Configuracoes,escutar_invalidacoes
        worker=Configuracoes(Repositorio(pool),ttl=3600)
        escuta=asyncio.create_task(escutar_invalidacoes(p.redis,worker,p.prefixo))
        try:
            await asyncio.sleep(0.3)
            antes=(await worker.efetiva(T,A))['debounce_s']
            assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'debounce_s':antes+1},headers=H)).status_code==200
            for _ in range(40):
                if (await worker.efetiva(T,A))['debounce_s']==antes+1: break
                await asyncio.sleep(0.05)
            assert (await worker.efetiva(T,A))['debounce_s']==antes+1
        finally:
            escuta.cancel();await asyncio.gather(escuta,return_exceptions=True)
    cenario_by_koji(tmp_path,corpo)
