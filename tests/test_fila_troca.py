"""Fase 0.6, bloco 5: troca para a fila durável — queda do Redis no meio do lote (INV14),
parada graciosa e atraso do agente simulado para o teste de carga."""
import asyncio,time
import pytest
import redis.asyncio as redis
from agentes import skyone
from nucleo.pipeline import insistir
from test_admin import criar
from test_fila import banco_real,montar,limpar,esperar_processadas,parar
from test_migrations import DSN
from test_pipeline_multi import ativo
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


class RedisInstavel:
    """Redis real que recusa as chamadas enquanto `fora` estiver ligado (queda simulada)."""
    def __init__(self,real): self.real=real;self.fora=False
    def __getattr__(self,nome):
        alvo=getattr(self.real,nome)
        if not callable(alvo) or nome=='scan_iter': return alvo  # scan_iter é gerador assíncrono
        async def chamada(*a,**k):
            if self.fora: raise redis.ConnectionError('Redis fora')
            return await alvo(*a,**k)
        return chamada


def test_INV14_queda_do_redis_no_fim_do_lote_devolve_vaga_e_nao_duplica(tmp_path):
    async def cenario(conn):
        # ocioso alto: a recuperação tem que vir da liberação insistente, não da retomada por outro worker.
        app,c,r,p,pref=await montar(conn,True,tmp_path,ocioso_ms=600000)
        fila=app.state.fila;instavel=RedisInstavel(r)
        p.redis=fila.redis=instavel;p.insistir_s=15
        chamadas=[]
        async def agente(http,repo,cofre,tenant,cfg,payload):
            chamadas.append(payload['conversation_id'])
            if len(chamadas)==1:
                # O Redis cai com a primeira conversa em andamento e volta 1,5 s depois.
                instavel.fora=True
                asyncio.get_running_loop().call_later(1.5,lambda: setattr(instavel,'fora',False))
            return {'resposta':['Resposta simulada.'],'acao':'nenhuma'}
        p.chamar=agente
        try:
            async with c:
                a,can=await criar(c,'alpha')
                # Uma vaga só: se ela ficar presa, a segunda conversa nunca é atendida.
                await ativo(c,'alpha',a,can,debounce_s=1,max_wait_s=1,max_lotes_simultaneos=1)
                for i in range(2):
                    assert (await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':f'5511900{i}','texto':'oi'})).status_code==200
                await asyncio.sleep(1.3)
                tarefas=[asyncio.create_task(fila.agendador(intervalo=.05)),asyncio.create_task(fila.worker(bloqueio_ms=100))]
                assert await esperar_processadas(conn,12)
                await asyncio.sleep(.5);await parar(*tarefas)
            assert len(chamadas)==2 and len(set(chamadas))==2,chamadas
            assert await conn.fetchval("SELECT count(*) FROM turnos WHERE etapa='concluido' AND status='ok'")==2
            assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE origem='bot'")==2
            assert not await r.exists(fila.k_lotes('alpha'))          # vaga devolvida
            assert (await r.xpending(fila.stream('alpha'),'workers'))['pending']==0  # nada preso com o worker
        finally:
            await limpar(r,pref)
    banco_real(cenario)


class TravaFalhando:
    """Redis real em que só o SET da trava da conversa falha, numa quantidade contada de vezes (sem depender do relógio)."""
    def __init__(self,real,falhas=0): self.real=real;self.falhas=falhas;self.tentativas=0
    def __getattr__(self,nome): return getattr(self.real,nome)
    async def set(self,chave,*a,**k):
        if ':lock:' in chave:
            self.tentativas+=1
            if self.falhas>0:
                self.falhas-=1;raise redis.ConnectionError('Redis fora')
        return await self.real.set(chave,*a,**k)


def test_INV14_trava_com_redis_oscilando_tenta_de_novo_e_sem_redis_volta_para_a_fila(tmp_path):
    """Corrida do INV14, de forma determinística: a segunda conversa volta do "sem vaga" e encontra o Redis falhando ao pegar a trava.
    Antes: o item ficava pendente até a retomada (90 s em produção). Agora: tenta de novo com espera crescente (~10 s; aqui zero);
    se o Redis não voltar, a conversa volta para o agendamento e o worker segue — nada fica pendente nem preso."""
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path,ocioso_ms=600000)
        fila=app.state.fila;falha=TravaFalhando(r)
        p.redis=fila.redis=falha;p.esperas_trava=(0,0,0,0,0,0)
        chamadas=[]
        async def agente(http,repo,cofre,tenant,cfg,payload):
            chamadas.append(payload['conversation_id']);return {'resposta':['Resposta simulada.'],'acao':'nenhuma'}
        p.chamar=agente
        try:
            async with c:
                a,can=await criar(c,'alpha')
                await ativo(c,'alpha',a,can,debounce_s=1,max_wait_s=1,max_lotes_simultaneos=1)
                conv=(await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':'5511900001','texto':'oi'})).json()['conversation_id']
                # 1) Oscilação curta: 3 falhas seguidas e o Redis volta → mesma execução pega a trava e responde.
                falha.falhas=3
                assert await p.processar_duravel('alpha',conv)=='concluido' and falha.tentativas==4
                # 2) Redis fora durante todas as tentativas (1 + 6) → 'sem_trava'; nada do turno começou.
                await c.post(f'/admin/v1/tenants/alpha/agentes/{a}/simular',json={'canal_id':can,'identificador':'5511900001','texto':'de novo'})
                falha.falhas=7;falha.tentativas=0
                assert await p.processar_duravel('alpha',conv)=='sem_trava' and falha.tentativas==7
                assert await conn.fetchval("SELECT count(*) FROM turnos WHERE conversation_id=$1",conv)==1
                # 3) Pela fila: o item sem trava é confirmado (sai do pendente) e a conversa volta para o agendamento; depois é atendida.
                chave=p.chave('alpha',conv)
                await r.delete(p.prefixo+':first:'+chave);await r.zrem(fila.k_due,chave)
                await fila.garantir_grupo('alpha')
                item=await r.xadd(fila.stream('alpha'),{'chave':chave})
                await r.xreadgroup('workers',fila.id,{fila.stream('alpha'):'>'},count=1)
                falha.falhas=7
                assert await fila.tratar(fila.stream('alpha'),item,chave)=='sem_trava'
                assert (await r.xpending(fila.stream('alpha'),'workers'))['pending']==0
                assert await r.zscore(fila.k_due,chave) is not None
                assert await p.processar_duravel('alpha',conv)=='concluido'
                assert await conn.fetchval("SELECT count(*) FROM turnos WHERE conversation_id=$1 AND etapa='concluido' AND status='ok'",conv)==2
                assert not await r.exists(fila.k_lotes('alpha'))
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_insistir_desiste_no_limite_e_devolve_o_resultado():
    async def cenario():
        n=[]
        async def falha_duas():
            n.append(1)
            if len(n)<3: raise redis.ConnectionError('fora')
            return 'ok'
        assert await insistir(falha_duas,limite_s=5,intervalo=.01)=='ok' and len(n)==3
        async def sempre(): raise redis.ConnectionError('fora')
        with pytest.raises(redis.ConnectionError): await insistir(sempre,limite_s=.05,intervalo=.01)
    asyncio.run(cenario())


def test_parada_graciosa_termina_o_lote_em_andamento(tmp_path):
    """Recepção (caminho atual) e worker (fila durável): o que está em andamento termina antes de sair."""
    async def cenario(conn):
        for ligada in (False,True):
            app,c,r,p,pref=await montar(conn,ligada,tmp_path)
            async def agente(http,repo,cofre,tenant,cfg,payload):
                await asyncio.sleep(1);return {'resposta':['Resposta simulada.'],'acao':'nenhuma'}
            p.chamar=agente
            try:
                async with c:
                    t='alpha' if not ligada else 'bravo'
                    a,can=await criar(c,t)
                    await ativo(c,t,a,can,debounce_s=1,max_wait_s=1)
                    assert (await c.post(f'/admin/v1/tenants/{t}/agentes/{a}/simular',json={'canal_id':can,'identificador':'5511900','texto':'oi'})).status_code==200
                    await asyncio.sleep(1.2)
                    if ligada:
                        sinal=asyncio.Event()
                        tarefas=[asyncio.create_task(app.state.fila.agendador(intervalo=.05,parar=sinal)),
                                 asyncio.create_task(app.state.fila.worker(parar=sinal,bloqueio_ms=100))]
                        await asyncio.sleep(.6);sinal.set()        # SIGTERM com o agente no meio da resposta
                        await asyncio.wait_for(asyncio.gather(*tarefas),10)
                    else:
                        tarefa=asyncio.create_task(p.agendador())
                        await asyncio.sleep(.6);await parar(tarefa)  # recepção parando
                        await p.fechar(espera_s=10)
                assert await conn.fetchval("SELECT count(*) FROM turnos WHERE tenant_id=$1 AND etapa='concluido' AND status='ok'",t)==1
                assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND direcao='entrada' AND processada_em IS NULL",t)==0
            finally:
                await limpar(r,pref)
    banco_real(cenario)


def test_atraso_do_agente_simulado_vem_do_ambiente(monkeypatch):
    async def cenario():
        cfg={'tenant_id':'alpha','id':'ag','agente_mode':'mock'};payload={'tenant_id':'alpha','agente_id':'ag'}
        inicio=time.monotonic()
        assert (await skyone.chamar(None,None,None,'alpha',cfg,payload))['resposta']==['Resposta simulada.']
        assert time.monotonic()-inicio<.2          # padrão: sem atraso
        monkeypatch.setenv('MOCK_ATRASO_S','0.4');inicio=time.monotonic()
        await skyone.chamar(None,None,None,'alpha',cfg,payload)
        assert .35<=time.monotonic()-inicio<1.5
    asyncio.run(cenario())
