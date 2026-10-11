"""OPS1: alertas (repetição, lembrete, normalizado, limites, sem dado de cliente), /health/ops e o script de alerta da VPS."""
import os,subprocess,pathlib
import pytest
from nucleo import alertas as AL
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar

RAIZ=pathlib.Path(__file__).resolve().parents[1]
LIM={'repetir_s':1800,'fila_espera_s':60,'fila_conversas':50,'skyone_erro_pct':20,'skyone_min_chamadas':5,'skyone_janela_min':10,'worker_s':90,'whatsapp_s':300,'workers_esperados':2}


class RedisFalso:
    def __init__(self): self.d={}
    async def hgetall(self,k): return dict(self.d.get(k) or {})
    async def hset(self,k,campo=None,valor=None,mapping=None):
        h=self.d.setdefault(k,{})
        if mapping: h.update({a:str(b) for a,b in mapping.items()})
        if campo is not None: h[campo]=str(valor)
    async def hsetnx(self,k,campo,valor):
        h=self.d.setdefault(k,{})
        if campo in h: return 0
        h[campo]=str(valor);return 1
    async def hget(self,k,campo): return (self.d.get(k) or {}).get(campo)
    async def hdel(self,k,*campos):
        for c in campos: (self.d.get(k) or {}).pop(c,None)
        if k in self.d and not self.d[k]: del self.d[k]
    async def expire(self,k,s): return 1
    async def delete(self,*ks):
        for k in ks: self.d.pop(k,None)
    async def exists(self,k): return int(k in self.d)
    async def set(self,k,v,**_): self.d[k]={'valor':v}
    async def ping(self): return True


class Telegram:
    def __init__(self,status=200): self.enviados=[];self.status=status;self.urls=[]
    async def post(self,url,json=None,timeout=None):
        self.urls.append(url)
        if self.status=='queda': raise RuntimeError('rede fora')
        if self.status==200: self.enviados.append(json['text'])
        return type('R',(),{'status_code':self.status})()


class Relogio:
    def __init__(self,t=1_800_000_000.0): self.t=t
    def __call__(self): return self.t


def novo(token='tok-de-teste',chat='-100',status=200):
    r,t,rel=RedisFalso(),Telegram(status),Relogio()
    return AL.Alertas(r,t,None,'gw',token=token,chat=chat,repetir_s=1800,relogio=rel),r,t,rel


def test_repeticao_lembrete_e_normalizado():
    async def cenario():
        a,r,t,rel=novo()
        assert await a.disparar('disco','aviso','disco acima de 80%','Uso: 83%')=='novo' and len(t.enviados)==1
        assert t.enviados[0].startswith('🟡 GATEWAY — disco acima de 80%\nUso: 83%\n')
        # O mesmo alerta dentro de 30 min não sai de novo; outro alvo não é bloqueado por ele.
        rel.t+=600;assert await a.disparar('disco','aviso','disco acima de 80%','Uso: 84%')=='suprimido' and len(t.enviados)==1
        assert await a.disparar('fila:bykoji','critico','fila acima do limite','Empresa: bykoji')=='novo' and t.enviados[1].startswith('🔴 GATEWAY — fila acima do limite')
        rel.t+=1199;assert await a.disparar('disco','aviso','disco acima de 80%')=='suprimido'
        # Passou o intervalo: lembrete, dizendo há quanto tempo dura.
        rel.t+=2;assert await a.disparar('disco','aviso','disco acima de 80%','Uso: 85%')=='lembrete' and 'Continua há 30 min.' in t.enviados[2] and 'Uso: 85%' in t.enviados[2]
        # Normalizado: uma mensagem só, com a duração; sem alerta aberto, nada sai.
        rel.t+=3600;assert await a.normalizar('disco') is True and t.enviados[3].startswith('🟢 GATEWAY — normalizado: disco acima de 80%') and 'Durou 1 h 30 min.' in t.enviados[3]
        assert await a.normalizar('disco') is False and await a.normalizar('nunca_houve') is False and len(t.enviados)==4
        # Depois de normalizado, o mesmo problema volta como alerta novo.
        assert await a.disparar('disco','critico','disco acima de 90%')=='novo' and len(t.enviados)==5
        assert all(u=='https://api.telegram.org/bottok-de-teste/sendMessage' for u in t.urls)
    import asyncio;asyncio.run(cenario())


def test_sem_bot_configurado_e_falha_de_envio_nao_quebram():
    async def cenario():
        # Sem credencial: nada é enviado, nada quebra, e o estado é mantido (o "normalizado" continua coerente).
        a,r,t,rel=novo(token='',chat='')
        assert not a.configurado and await a.disparar('x','critico','teste')=='novo' and t.urls==[] and await a.normalizar('x') is True and t.urls==[]
        # Telegram fora do ar ou recusando: sem exceção; como não saiu, a próxima conferência tenta de novo sem esperar 30 min.
        for status in (500,'queda'):
            a,r,t,rel=novo(status=status)
            assert await a.disparar('y','critico','teste')=='novo' and t.enviados==[]
            rel.t+=60;t.status=200;assert await a.disparar('y','critico','teste')=='lembrete' and len(t.enviados)==1
            rel.t+=60;assert await a.disparar('y','critico','teste')=='suprimido'
    import asyncio;asyncio.run(cenario())


def test_condicao_precisa_durar_para_disparar_e_para_normalizar():
    async def cenario():
        a,r,t,rel=novo()
        av=lambda ativa: a.avaliar('fila:alpha',ativa,'critico','fila acima do limite','Empresa: alpha',apos_s=30,calma_s=120)
        assert await av(True)=='aguardando' and t.enviados==[]
        rel.t+=15;assert await av(False)=='normal'                      # pico isolado: nada
        assert await av(True)=='aguardando';rel.t+=30;assert await av(True)=='novo' and len(t.enviados)==1
        rel.t+=30;assert await av(True)=='suprimido'
        # Para normalizar precisa ficar bem por 2 min seguidos; uma recaída zera a contagem.
        rel.t+=30;assert await av(False)=='acalmando';rel.t+=100;assert await av(True)=='suprimido'
        rel.t+=10;assert await av(False)=='acalmando';rel.t+=119;assert await av(False)=='acalmando' and len(t.enviados)==1
        rel.t+=2;assert await av(False)=='normalizado' and t.enviados[-1].startswith('🟢 GATEWAY — normalizado: fila acima do limite') and await av(False)=='normal'
    import asyncio;asyncio.run(cenario())


def test_limites_configuraveis(monkeypatch):
    for nome in ('ALERTA_REPETIR_MIN','ALERTA_FILA_ESPERA_S','ALERTA_FILA_CONVERSAS','ALERTA_SKYONE_ERRO_PCT','ALERTA_SKYONE_MIN_CHAMADAS','WORKERS'): monkeypatch.delenv(nome,raising=False)
    p=AL.limites();assert (p['repetir_s'],p['fila_espera_s'],p['fila_conversas'],p['skyone_erro_pct'],p['skyone_min_chamadas'],p['skyone_janela_min'],p['workers_esperados'])==(1800,60,50,20,5,10,2)
    monkeypatch.setenv('ALERTA_FILA_ESPERA_S','90');monkeypatch.setenv('ALERTA_SKYONE_ERRO_PCT','abc');monkeypatch.setenv('ALERTA_REPETIR_MIN','10');monkeypatch.setenv('WORKERS','3')
    p=AL.limites();assert (p['fila_espera_s'],p['skyone_erro_pct'],p['repetir_s'],p['workers_esperados'])==(90,20,600,3)
    assert [AL.duracao(s) for s in (10,60,3599,3600,5400)]==['menos de 1 min','1 min','59 min','1 h 00 min','1 h 30 min']


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_conferencias_do_gateway_e_health_ops():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn)
        async with client:
            a,canal_api=await criar(client)
            r,t,rel=RedisFalso(),Telegram(),Relogio()
            al=AL.Alertas(r,t,app.state.db,'gw',token='tok',chat='1',repetir_s=1800,relogio=rel)
            ind=lambda vivos=2,espera=0.0,aguardando=0: {'ligada':True,'workers_vivos':vivos,'empresas':{'alpha':{'aguardando':aguardando,'em_processamento':0,'mais_antiga_s':espera}}}
            rodar=lambda **k: AL.conferir(al,app.state.db,ind(**k),LIM)
            titulos=lambda: [x.split('\n')[0] for x in t.enviados]
            # Tudo em ordem: nenhuma mensagem.
            await rodar();rel.t+=200;await rodar();assert t.enviados==[]
            # Fila: logo abaixo do limite não dispara; logo acima dispara depois de duas conferências (30 s).
            await rodar(espera=60.0,aguardando=50);rel.t+=40;await rodar(espera=60.0,aguardando=50);assert t.enviados==[]
            await rodar(espera=61.0);rel.t+=31;await rodar(espera=61.0);assert titulos()==['🔴 GATEWAY — fila acima do limite']
            assert t.enviados[0].split('\n')[1]=='Empresa: alpha · 0 conversa(s) esperando · a mais antiga há 61 s'
            rel.t+=10;await rodar();rel.t+=121;await rodar();assert titulos()[-1]=='🟢 GATEWAY — normalizado: fila acima do limite'
            await rodar(aguardando=51);rel.t+=31;await rodar(aguardando=51);assert titulos()[-1]=='🔴 GATEWAY — fila acima do limite';rel.t+=1;await rodar();rel.t+=121;await rodar()
            # Worker: um a menos por mais de 90 s.
            n=len(t.enviados);await rodar(vivos=1);rel.t+=80;await rodar(vivos=1);assert len(t.enviados)==n
            rel.t+=11;await rodar(vivos=1);assert t.enviados[-1].split('\n')[:2]==['🔴 GATEWAY — worker parado','1 de 2 workers ativos']
            rel.t+=5;await rodar();rel.t+=121;await rodar();assert titulos()[-1]=='🟢 GATEWAY — normalizado: worker parado'
            # Skyone: 20% exatos não dispara; menos de 5 chamadas não dispara; acima de 20% com 5 ou mais dispara.
            async def turnos(ok,erro):
                await conn.execute("DELETE FROM turnos WHERE tenant_id='alpha'")
                conv=await conn.fetchval("INSERT INTO conversas(conversation_id,canal,tenant_id,canal_id,agente_id) VALUES ($1,'api','alpha',$2,$3) ON CONFLICT (conversation_id) DO UPDATE SET atualizado_em=now() RETURNING conversation_id",f'alpha:{canal_api}:x',canal_api,a)
                for i in range(ok+erro):
                    await conn.execute("""INSERT INTO turnos(tenant_id,conversation_id,agente_id,canal_id,request_id,n_mensagens,primeira_msg_em,ultima_msg_em,agente_inicio,agente_fim,modo_agente,status,origem,etapa)
                        VALUES ('alpha',$1,$2,$3,gen_random_uuid(),1,now(),now(),now()-interval '1 minute',now(),'live',$4,'cliente','concluido')""",conv,a,canal_api,'ok' if i<ok else 'erro')
            n=len(t.enviados)
            await turnos(4,1);await rodar();assert len(t.enviados)==n          # 20%: não passa do limite
            await turnos(1,3);await rodar();assert len(t.enviados)==n          # 75%, mas só 4 chamadas
            await turnos(3,2);await rodar();assert t.enviados[-1].split('\n')[:2]==['🔴 GATEWAY — erro do Skyone acima do limite','2 erro(s) em 5 chamada(s) nos últimos 10 min (40%)']
            # Entre a metade do limite e o limite não normaliza; abaixo da metade, por 10 min, normaliza.
            await turnos(17,3);rel.t+=700;await rodar();assert titulos()[-1]!='🟢 GATEWAY — normalizado: erro do Skyone acima do limite'
            await turnos(20,1);rel.t+=5;await rodar();rel.t+=601;await rodar();assert titulos()[-1]=='🟢 GATEWAY — normalizado: erro do Skyone acima do limite'
            # WhatsApp desconectado por mais de 5 min; canal de outro tipo, inativo ou sem estado conhecido não conta.
            c=(await client.post('/admin/v1/tenants/alpha/canais',json={'nome':'WhatsApp','tipo':'uazapi','agente_id':a})).json()['id']
            await conn.execute("UPDATE tenant_canais SET ativo=true,status_conexao='desconectado' WHERE id=$1",c)
            n=len(t.enviados);await rodar();rel.t+=290;await rodar();assert len(t.enviados)==n
            rel.t+=11;await rodar();assert t.enviados[-1].split('\n')[0]=='🔴 GATEWAY — WhatsApp desconectado' and f'canal {c[-6:]}' in t.enviados[-1]
            await conn.execute("UPDATE tenant_canais SET status_conexao='conectado' WHERE id=$1",c);rel.t+=5;await rodar();rel.t+=61;await rodar()
            assert titulos()[-1]=='🟢 GATEWAY — normalizado: WhatsApp desconectado'
            # Nenhuma mensagem traz telefone, nome ou texto de conversa; cada alerta e cada normalização ficam na auditoria da plataforma.
            assert all('5511' not in x and 'Cliente' not in x for x in t.enviados)
            assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id IS NULL AND acao='ops.alerta'")==5 and await conn.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id IS NULL AND acao='ops.normalizado'")==5

            # /health/ops: público só diz ok (200) ou não (503); os motivos só aparecem em chamada interna.
            app.state.redis=RedisFalso();lider=f'{app.state.pipeline.prefixo}:fila:lider'
            async def ops(interna=True):
                resp=await client.get('/health/ops',headers={} if interna else {'X-Forwarded-For':'203.0.113.9'})
                return resp.status_code,resp.json()
            gravar=lambda tipo,res,quando='now()': conn.execute(f"INSERT INTO ops_execucoes(tipo,resultado,inicio,fim) VALUES ($1,$2,{quando},{quando})",tipo,res)
            st,corpo=await ops();assert st==503 and corpo['ok'] is False and 'backup_nunca_executado' in corpo['motivos'] and 'agendador_sem_sinal' in corpo['motivos']
            st,corpo=await ops(interna=False);assert (st,corpo)==(503,{'ok':False})          # sem dados para fora
            await app.state.redis.set(lider,'x')
            # Backup só local (destino não configurado): ainda não está em ordem, e a chamada interna diz por quê.
            await gravar('backup','local');assert await ops()==(503,{'ok':False,'motivos':['destino_nao_configurado']})
            await gravar('backup','ok');assert await ops()==(200,{'ok':True,'motivos':[]}) and await ops(interna=False)==(200,{'ok':True})
            await gravar('backup','falha');assert (await ops())[1]['motivos']==['backup_falhou']
            await conn.execute("DELETE FROM ops_execucoes");await gravar('backup','ok',"now()-interval '27 hours'");assert (await ops())[1]['motivos']==['backup_atrasado']
            await gravar('backup','ok');await gravar('restauracao','falha');assert (await ops())[1]['motivos']==['teste_de_restauracao_falhou']
            await gravar('restauracao','ok');assert (await ops())[0]==200
            await app.state.redis.delete(lider);assert await ops()==(503,{'ok':False,'motivos':['agendador_sem_sinal']})
    banco(cenario)


def sh(tmp,*args,agora=None,env_extra=None):
    env={'PATH':os.environ['PATH'],'GATEWAY_RAIZ':str(RAIZ),'OPS_ENV':str(tmp/'env'),'OPS_ESTADO':str(tmp/'estado'),'ALERTA_ESTADO':str(tmp/'alertas'),'ALERTA_SIMULAR':str(tmp/'saida'),'TZ':'UTC'}
    if agora is not None: env['ALERTA_AGORA']=str(agora)
    env.update(env_extra or {})
    return subprocess.run(['sh',str(RAIZ/'scripts'/'alerta.sh'),*args],capture_output=True,text=True,env=env)


def test_script_de_alerta_da_vps_mesmas_regras(tmp_path):
    (tmp_path/'env').write_text('ALERTA_REPETIR_MIN=30\n')
    saida=lambda: [b.strip().split('\n') for b in (tmp_path/'saida').read_text().split('\n---\n') if b.strip()] if (tmp_path/'saida').exists() else []
    assert sh(tmp_path,'disparar','disco','aviso','disco acima de 80%','Uso: 83%',agora=1000).stdout.strip()=='novo'
    assert sh(tmp_path,'disparar','disco','aviso','disco acima de 80%','Uso: 84%',agora=2799).stdout.strip()=='suprimido'
    assert sh(tmp_path,'disparar','servico-worker','critico','serviço parado: worker',agora=2799).stdout.strip()=='novo'     # outro alvo não é bloqueado
    assert sh(tmp_path,'disparar','disco','aviso','disco acima de 80%','Uso: 85%',agora=2800).stdout.strip()=='lembrete'
    assert sh(tmp_path,'normalizar','disco',agora=6400).stdout.strip()=='normalizado' and sh(tmp_path,'normalizar','disco',agora=6401).stdout.strip()=='sem_alerta'
    s=saida();assert [x[0] for x in s]==['🟡 GATEWAY — disco acima de 80%','🔴 GATEWAY — serviço parado: worker','🟡 GATEWAY — disco acima de 80%','🟢 GATEWAY — normalizado: disco acima de 80%']
    assert s[0][1]=='Uso: 83%' and 'Continua há 30 min.' in s[2] and 'Durou 1 h 30 min.' in s[3]
    # Intervalo configurável; chave com caracteres estranhos não escapa da pasta de estado.
    (tmp_path/'env').write_text('ALERTA_REPETIR_MIN=5\n')
    assert sh(tmp_path,'disparar','../../x y','critico','t',agora=10).stdout.strip()=='novo' and sh(tmp_path,'disparar','../../x y','critico','t',agora=311).stdout.strip()=='lembrete'
    assert all(p.parent==tmp_path/'alertas' for p in (tmp_path/'alertas').iterdir())
    # Sem bot configurado: o teste avisa que não enviou, sai com erro e não tenta rede; o registro local explica.
    r=sh(tmp_path,'teste',env_extra={'ALERTA_SIMULAR':''});assert r.returncode==1 and 'NÃO ENVIADO' in r.stdout
    assert 'alertas desligados' in (tmp_path/'estado'/'ops.log').read_text()
    assert sh(tmp_path,'qualquer').returncode==2
