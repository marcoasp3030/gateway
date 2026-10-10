"""INV07: a vaga da empresa/agente só é devolvida por quem está processando a conversa.

Reproduz o estouro do limite por empresa: um segundo item da MESMA conversa (mensagem nova
durante o processamento, ou item retomado por outro worker) não pode liberar a vaga de quem
ainda está chamando o agente."""
import asyncio,random
import pytest
from nucleo.fila import FilaDuravel
from test_fila import banco_real,montar,limpar,conversas,esperar_processadas,parar
from test_migrations import DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


async def limitar(c,conn,tenant,**limites):
    agente=await conn.fetchval('SELECT id FROM agentes WHERE tenant_id=$1 ORDER BY id LIMIT 1',tenant)
    assert (await c.patch(f'/admin/v1/tenants/{tenant}/agentes/{agente}',json=limites)).status_code==200
    return agente


def test_INV07_item_repetido_da_conversa_nao_devolve_a_vaga_de_quem_processa(tmp_path):
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path,concorrencia=20,ocioso_ms=600000)
        fila=app.state.fila;soltar=asyncio.Event();andamento=[0];pico=[0]
        async def chamar(http,repo,cofre,tenant,cfg,payload):
            andamento[0]+=1;pico[0]=max(pico[0],andamento[0])
            try: await soltar.wait()
            finally: andamento[0]-=1
            return {'resposta':['ok'],'acao':'nenhuma'}
        p.chamar=chamar
        try:
            async with c:
                (_,x),(_,y)=await conversas(c,('alpha',),2)
                await limitar(c,conn,'alpha',max_lotes_simultaneos=1,max_concorrencia=1)
                s=fila.stream('alpha');kx,ky=p.chave('alpha',x),p.chave('alpha',y)
                primeira=asyncio.create_task(fila.tratar(s,'0-1',kx))
                for _ in range(100):
                    if andamento[0]: break
                    await asyncio.sleep(.05)
                assert andamento[0]==1
                # Segundo item da mesma conversa enquanto o agente ainda responde: sai sem processar...
                assert await asyncio.wait_for(fila.tratar(s,'0-2',kx),10)=='ocupado'
                # ...e a vaga continua com quem está processando: outra conversa da empresa não entra.
                assert await asyncio.wait_for(fila.tratar(s,'0-3',ky),10)=='sem_vaga'
                assert pico[0]==1 and await r.zcard(fila.k_lotes('alpha'))==1
                soltar.set();assert await asyncio.wait_for(primeira,20)=='concluido'
                # Terminou: a vaga foi devolvida e a outra conversa é atendida.
                assert await r.zcard(fila.k_lotes('alpha'))==0
                assert await asyncio.wait_for(fila.tratar(s,'0-4',ky),20)=='concluido' and pico[0]==1
                assert await r.zcard(fila.k_lotes('alpha'))==0 and await r.zcard(fila.k_ativos('alpha',await limitar(c,conn,'alpha')))==0
        finally: await limpar(r,pref)
    banco_real(cenario)


@pytest.mark.parametrize('rodada',range(3))
def test_INV07_estresse_limite_por_empresa_com_itens_retomados(tmp_path,rodada):
    """Três workers, agente de duração variável, retomada agressiva de itens (ocioso de 30 ms) e itens
    repetidos da mesma conversa chegando no meio do processamento. O limite de 5 nunca pode ser passado."""
    async def cenario(conn):
        app,c,r,p,pref=await montar(conn,True,tmp_path,concorrencia=20,ocioso_ms=30)
        outros=[FilaDuravel(p,True,concorrencia=20,prefixo=pref,ocioso_ms=30,arquivo_vivo=tmp_path/f'w{i}') for i in (2,3)]
        andamento={};pico={};chamadas=[0];sorte=random.Random(rodada)
        async def chamar(http,repo,cofre,tenant,cfg,payload):
            chamadas[0]+=1
            for k in (tenant,(tenant,cfg['id'])):
                andamento[k]=andamento.get(k,0)+1;pico[k]=max(pico.get(k,0),andamento[k])
            try:
                await asyncio.sleep(sorte.uniform(.02,.1))
                # No meio do processamento chega outro item da MESMA conversa (como quando o cliente manda
                # mais uma mensagem enquanto o agente responde): outro worker vai pegá-lo e sair como "ocupado".
                if sorte.random()<.7: await r.xadd(app.state.fila.stream(tenant),{'chave':p.chave(tenant,payload['conversation_id'])})
                await asyncio.sleep(sorte.uniform(.1,.35))
            finally:
                for k in (tenant,(tenant,cfg['id'])): andamento[k]-=1
            return {'resposta':['ok'],'acao':'nenhuma'}
        p.chamar=chamar
        try:
            async with c:
                await conversas(c,('alpha',),40);await conversas(c,('bravo',),6)
                a=await limitar(c,conn,'alpha',max_lotes_simultaneos=5,max_concorrencia=5)
                b=await limitar(c,conn,'bravo',max_lotes_simultaneos=2,max_concorrencia=2)
                await asyncio.sleep(1.2)
                tarefas=[asyncio.create_task(app.state.fila.agendador(intervalo=.05)),asyncio.create_task(app.state.fila.worker(bloqueio_ms=50))]
                tarefas+=[asyncio.create_task(o.worker(bloqueio_ms=50)) for o in outros]
                assert await esperar_processadas(conn,90)
                await asyncio.sleep(.5);await parar(*tarefas)
            assert pico['alpha']<=5 and pico[('alpha',a)]<=5,pico
            assert pico['bravo']<=2 and pico[('bravo',b)]<=2,pico
            assert pico['alpha']>=4      # o limite foi de fato alcançado (o teste exercita a disputa)
            # Nenhuma conversa respondida duas vezes, nenhuma perdida, nenhuma vaga presa.
            assert await conn.fetchval("SELECT count(*) FROM turnos WHERE etapa='concluido'")==46 and chamadas[0]==46
            assert await conn.fetchval("SELECT count(*) FROM (SELECT conversation_id FROM mensagens WHERE origem='bot' GROUP BY 1 HAVING count(*)>1) x")==0
            assert await r.zcard(app.state.fila.k_lotes('alpha'))==0 and await r.zcard(app.state.fila.k_lotes('bravo'))==0
        finally: await limpar(r,pref)
    banco_real(cenario)
