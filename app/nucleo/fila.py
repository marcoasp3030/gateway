"""Fila durável (Fase 0.6): filas por empresa em Redis Streams, workers e agendador.

O Postgres continua sendo a fonte da verdade: a mensagem é gravada antes de entrar
na fila e só é marcada como processada junto com o turno. O Redis guarda apenas
"o que fazer e quando" e é reconstruído a partir do Postgres pela reconciliação.

Com FILA_DURAVEL=off, workers e agendador sobem, mantêm sinais de vida e
indicadores, mas não agendam nem consomem nada: quem processa é o agendador em
processo da recepção (caminho atual). Com on, o caminho atual não processa.
"""
import asyncio
import json
import logging
import os
import socket
import time
import uuid
from pathlib import Path

from admin.servico import auditar
from nucleo.pipeline import insistir

log = logging.getLogger('gateway.fila')

GRUPO = 'workers'

# Move um item vencido do agendamento para a fila da empresa, de forma atômica:
# só quem consegue remover do "due" adiciona na fila (sem duplicar na troca de líder).
MOVER = '''
if redis.call('ZREM',KEYS[1],ARGV[1])==1 then
  redis.call('XADD',KEYS[2],'*','chave',ARGV[1])
  redis.call('SADD',KEYS[3],ARGV[2])
  redis.call('SADD',KEYS[4],ARGV[1])
  return 1
end
return 0
'''

# Vagas compartilhadas entre todos os workers (INV07/INV15): a empresa tem no máximo
# max_lotes_simultaneos e o agente max_concorrencia conversas em andamento.
# Retorna 0 (vaga obtida), 1 (empresa no limite) ou 2 (agente no limite).
# Só é chamado por quem tem a trava da conversa: se a conversa já consta, é sobra de uma
# execução que caiu, e quem tem a trava assume a mesma vaga (por isso devolve 0).
ADQUIRIR = '''
local agora=tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',agora)
redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',agora)
if redis.call('ZSCORE',KEYS[1],ARGV[5]) then return 0 end
if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[3]) then return 1 end
if redis.call('ZCARD',KEYS[2])>=tonumber(ARGV[4]) then return 2 end
redis.call('ZADD',KEYS[1],ARGV[2],ARGV[5])
redis.call('ZADD',KEYS[2],ARGV[2],ARGV[5])
redis.call('PEXPIRE',KEYS[1],ARGV[6])
redis.call('PEXPIRE',KEYS[2],ARGV[6])
return 0
'''

RENOVAR = '''
if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('PEXPIRE',KEYS[1],ARGV[2]) end
return 0
'''


def ligada_no_ambiente():
    return os.getenv('FILA_DURAVEL', 'off').strip().lower() in ('on', '1', 'true', 'sim')


class Vaga:
    """Vaga de uma conversa nos limites da empresa (max_lotes_simultaneos) e do agente (max_concorrencia).

    Usada pelo processamento da conversa: reservar() depois de obter a trava da conversa e
    devolver() antes de soltá-la. Assim um item repetido da mesma conversa (mensagem nova durante
    o processamento, item retomado por outro worker) nunca devolve a vaga de quem ainda processa."""
    def __init__(self, fila, tenant, chave):
        self.fila, self.tenant, self.chave = fila, tenant, chave
        self.lotes = self.ativo = None
        self.motivo = 0

    async def reservar(self, cfg):
        """0 = reservada; 1 = empresa no limite; 2 = agente no limite."""
        f = self.fila
        self.lotes, self.ativo = f.k_lotes(self.tenant), f.k_ativos(self.tenant, cfg['id'])
        agora = time.time() * 1000; validade = (cfg['timeout_s'] + 90) * 1000
        self.motivo = int(await f.redis.eval(ADQUIRIR, 2, self.lotes, self.ativo, agora, agora + validade,
                                             cfg['max_lotes_simultaneos'], cfg['max_concorrencia'], self.chave, int(validade)))
        return self.motivo

    async def renovar(self, cfg):
        """Estende a validade da vaga já reservada (turno refeito: mais uma consulta ao agente)."""
        validade = (cfg['timeout_s'] + 90) * 1000; ate = time.time() * 1000 + validade
        for chave in (self.lotes, self.ativo):
            await self.fila.redis.zadd(chave, {self.chave: ate}, xx=True)
            await self.fila.redis.pexpire(chave, int(validade))

    async def devolver(self):
        await self.fila.redis.zrem(self.lotes, self.chave)
        await self.fila.redis.zrem(self.ativo, self.chave)


class FilaDuravel:
    def __init__(self, pipeline, ligada=False, concorrencia=20, prefixo=None, lider_ttl_ms=10000,
                 ocioso_ms=90000, alerta_fila_s=30, alerta_cota=0.8, alerta_cota_s=300,
                 arquivo_vivo=None):
        self.p = pipeline
        self.redis = pipeline.redis
        self.db = pipeline.db
        self.prefixo = prefixo or pipeline.prefixo
        self.ligada = ligada
        self.concorrencia = concorrencia
        self.id = f'{socket.gethostname()}-{uuid.uuid4().hex[:6]}'
        self.lider_ttl_ms = lider_ttl_ms
        # Item pendente com um worker por mais que isso é retomado por outro
        # (timeout_s padrão 60 + 30 s de folga).
        self.ocioso_ms = ocioso_ms
        self.alerta_fila_s = alerta_fila_s
        self.alerta_cota = alerta_cota
        self.alerta_cota_s = alerta_cota_s
        self.arquivo_vivo = Path(arquivo_vivo or os.getenv('VIVO_ARQUIVO', '/tmp/gateway-vivo'))
        self.grupos = set()
        self.rodada = 0
        self.tarefas = set()
        self.lider = False
        # Empresas no limite: o worker deixa de ler a fila delas por um instante.
        self.cheias = {}
        # Reconciliação com o provedor do canal (uazapi): a cada varredura_s e na subida.
        from canais.varredura import Varredura
        self.varredura = Varredura(pipeline)
        self.varredura_s = float(os.getenv('VARREDURA_UAZAPI_S', '90') or 90)
        self.varredura_task = None
        # OPS1: alertas pelo Telegram (worker, fila, Skyone, WhatsApp), conferidos pelo agendador.
        from nucleo.alertas import Alertas, limites
        self.limites_ops = limites()
        self.ops = Alertas(self.redis, pipeline.http, self.db, self.prefixo, repetir_s=self.limites_ops['repetir_s'])

    # ---------------- chaves ----------------
    def stream(self, tenant): return f'{self.prefixo}:fila:{tenant}'
    @property
    def k_tenants(self): return f'{self.prefixo}:fila:tenants'
    @property
    def k_na_fila(self): return f'{self.prefixo}:fila:na_fila'
    @property
    def k_lider(self): return f'{self.prefixo}:fila:lider'
    @property
    def k_due(self): return f'{self.prefixo}:due'
    def k_worker(self, wid): return f'{self.prefixo}:fila:worker:{wid}'
    def k_ativos(self, tenant, agente): return f'{self.prefixo}:fila:ativos:{tenant}:{agente}'
    def k_lotes(self, tenant): return f'{self.prefixo}:fila:lotes:{tenant}'

    # ---------------- sinais de vida ----------------
    def tocar_arquivo(self):
        try:
            self.arquivo_vivo.touch()
        except OSError:
            log.warning('Não foi possível registrar o sinal de vida local')

    async def sinal_worker(self):
        await self.redis.set(self.k_worker(self.id), str(int(time.time())), ex=15)
        self.tocar_arquivo()

    # ---------------- liderança do agendador ----------------
    async def manter_lideranca(self):
        """Renova se já é líder; senão tenta assumir. Validade curta (lider_ttl_ms)."""
        if await self.redis.eval(RENOVAR, 1, self.k_lider, self.id, self.lider_ttl_ms):
            self.lider = True
        else:
            self.lider = bool(await self.redis.set(self.k_lider, self.id, nx=True, px=self.lider_ttl_ms))
        return self.lider

    async def soltar_lideranca(self):
        await self.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0",
                              1, self.k_lider, self.id)
        self.lider = False

    # ---------------- agendamento ----------------
    async def garantir_grupo(self, tenant):
        s = self.stream(tenant)
        if s in self.grupos: return
        try:
            await self.redis.xgroup_create(s, GRUPO, id='0', mkstream=True)
        except Exception as erro:  # BUSYGROUP: já existe
            if 'BUSYGROUP' not in str(erro): raise
        self.grupos.add(s)

    async def mover_vencidos(self):
        movidos = 0
        for chave in await self.redis.zrangebyscore(self.k_due, 0, time.time(), start=0, num=100):
            tenant, _ = json.loads(chave)
            await self.garantir_grupo(tenant)
            movidos += await self.redis.eval(MOVER, 4, self.k_due, self.stream(tenant), self.k_tenants,
                                             self.k_na_fila, chave, tenant)
        return movidos

    async def reconciliar(self):
        """Conversa com entrada pendente no Postgres e sem agendamento, fila ou trava → volta a ser agendada."""
        rows = await self.db.fetch("""SELECT DISTINCT tenant_id,conversation_id FROM mensagens
            WHERE processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema')""")
        repostos = 0
        for row in rows:
            chave = self.p.chave(row['tenant_id'], row['conversation_id'])
            if await self.redis.zscore(self.k_due, chave) is not None: continue
            if await self.redis.sismember(self.k_na_fila, chave): continue
            if await self.redis.exists(self.p._trava(row['tenant_id'], row['conversation_id'])): continue
            await self.redis.zadd(self.k_due, {chave: time.time()})
            repostos += 1
        if repostos: log.warning('Reconciliação recolocou %s conversa(s) na fila', repostos)
        return repostos

    async def agendador(self, intervalo=0.25, parar=None):
        proxima_reconciliacao = 0
        proximos_indicadores = 0
        proxima_limpeza = 0
        proxima_varredura = 0
        proximas_vazias = 0
        proximos_sem_retorno = 0
        proximos_documentos = 0
        proximos_avisos = 0
        proximos_ops = 0
        while not (parar and parar.is_set()):
            try:
                if await self.manter_lideranca():
                    agora = time.monotonic()
                    if agora >= proximos_indicadores:
                        ind = await self.indicadores()
                        await self.alertas(ind)
                        proximos_indicadores = agora + 15
                        if agora >= proximos_ops:
                            # Falha aqui nunca pode parar o agendamento das conversas.
                            proximos_ops = agora + 30
                            try:
                                from nucleo.alertas import conferir
                                await conferir(self.ops, self.db, ind, self.limites_ops)
                            except Exception as erro:
                                log.warning('Conferência de alertas falhou (%s)', type(erro).__name__)
                    if self.varredura_s > 0 and agora >= proxima_varredura and (
                            self.varredura_task is None or self.varredura_task.done()):
                        # Em tarefa própria: chamada lenta ao provedor não atrasa o agendamento.
                        self.varredura_task = asyncio.create_task(self.varrer()); proxima_varredura = agora + self.varredura_s
                    if agora >= proximos_avisos:
                        # Avisos internos (transbordo, solicitações e os dos módulos: pedido para a unidade, aviso de pedido ao cliente).
                        proximos_avisos = agora + 2
                        from nucleo.avisos import despachar as despachar_avisos
                        await despachar_avisos(self.p)
                    if self.ligada:
                        await self.mover_vencidos()
                        if agora >= proxima_reconciliacao:
                            await self.reconciliar(); proxima_reconciliacao = agora + 60
                        if agora >= proxima_limpeza:
                            await self.p.limpeza(); proxima_limpeza = agora + 21600
                        if agora >= proximas_vazias:
                            # Conversa vazia (Fase 3): a cada 5 min.
                            proximas_vazias = agora + 300; await self.p.encerrar_vazias()
                        if agora >= proximos_sem_retorno:
                            # ENC1-a: atendimento com pedido fechado e sem retorno do cliente em 30 min, conferido a cada minuto.
                            proximos_sem_retorno = agora + 60; await self.p.encerrar_sem_retorno()
                        if agora >= proximos_documentos:
                            # ARQ1-a1: documento cuja leitura parou (queda) volta a ser lido; vencido pela retenção é apagado.
                            proximos_documentos = agora + 10
                            from nucleo import documentos
                            await documentos.varrer(self.p)
                        await self.despachar_saidas()
                self.tocar_arquivo()
            except Exception:
                log.warning('Agendador da fila temporariamente indisponível')
            await asyncio.sleep(intervalo)
        if self.varredura_task and not self.varredura_task.done():
            self.varredura_task.cancel(); await asyncio.gather(self.varredura_task, return_exceptions=True)
        await self.soltar_lideranca()

    async def varrer(self):
        try: await self.varredura.rodar()
        except Exception as erro:
            log.warning('Varredura do provedor indisponível (%s)', type(erro).__name__)

    async def despachar_saidas(self):
        from nucleo.webhooks import despachar
        t = self.p.saida_task
        if t is None or t.done():
            self.p.saida_task = asyncio.create_task(despachar(self.db, self.p.http, self.p.cofre))

    # ---------------- worker ----------------
    async def worker(self, parar=None, bloqueio_ms=500):
        ultimo_sinal = 0; ultima_retomada = 0
        while not (parar and parar.is_set()):
            try:
                if time.monotonic() - ultimo_sinal >= 5:
                    await self.sinal_worker(); ultimo_sinal = time.monotonic()
                if not self.ligada:
                    await asyncio.sleep(1); continue
                self.tarefas = {t for t in self.tarefas if not t.done()}
                livres = self.concorrencia - len(self.tarefas)
                if livres <= 0:
                    await asyncio.wait(self.tarefas, timeout=0.5, return_when=asyncio.FIRST_COMPLETED); continue
                if time.monotonic() - ultima_retomada >= 5:
                    await self.retomar(livres); ultima_retomada = time.monotonic()
                tenants = sorted(await self.redis.smembers(self.k_tenants))
                if not tenants:
                    await asyncio.sleep(bloqueio_ms / 1000); continue
                # Rodízio entre empresas: um item de cada por leitura (INV07).
                agora = time.monotonic()
                tenants = [t for t in tenants if self.cheias.get(t, 0) <= agora]
                if not tenants:
                    await asyncio.sleep(0.1); continue
                self.rodada = (self.rodada + 1) % len(tenants)
                ordem = tenants[self.rodada:] + tenants[:self.rodada]
                for t in ordem: await self.garantir_grupo(t)
                lidos = await self.redis.xreadgroup(GRUPO, self.id, {self.stream(t): '>' for t in ordem[:livres]},
                                                    count=1, block=bloqueio_ms)
                for stream, itens in lidos or []:
                    for item_id, campos in itens:
                        self.tarefas.add(asyncio.create_task(self.tratar(stream, item_id, campos['chave'])))
            except Exception:
                log.warning('Worker temporariamente indisponível')
                await asyncio.sleep(1)
        if self.tarefas:
            # Parada graciosa: termina o que está em andamento antes de sair.
            await asyncio.wait(self.tarefas, timeout=float(os.getenv('PARADA_GRACIOSA_S', '75') or 0) or None)

    async def retomar(self, livres):
        """Itens parados com um worker morto por mais que ocioso_ms são assumidos (XAUTOCLAIM)."""
        for t in sorted(await self.redis.smembers(self.k_tenants)):
            if livres <= 0: return
            await self.garantir_grupo(t)
            resposta = await self.redis.xautoclaim(self.stream(t), GRUPO, self.id, self.ocioso_ms, '0-0', count=livres)
            for item_id, campos in (resposta[1] if resposta else []):
                if not campos: continue  # item já apagado
                self.tarefas.add(asyncio.create_task(self.tratar(self.stream(t), item_id, campos['chave'])))
                livres -= 1

    async def tratar(self, stream, item_id, chave):
        tenant, conversa = json.loads(chave)
        vaga = Vaga(self, tenant, chave)
        try:
            # A vaga é reservada dentro do processamento, por quem tem a trava da conversa (INV07).
            resultado = await self.p.processar_duravel(tenant, conversa, vaga)
            if resultado == 'sem_vaga':
                # O item volta para o agendamento e o worker segue para as outras empresas.
                if vaga.motivo == 1: self.cheias[tenant] = time.monotonic() + 0.3
                await self.redis.zadd(self.k_due, {chave: time.time() + 0.3})
                await self.confirmar(stream, item_id, chave)
                return 'sem_vaga'
            # 'ocupado': quem tem a trava reagenda o que sobrar ao terminar; este item sai.
            await insistir(lambda: self.confirmar(stream, item_id, chave), self.p.insistir_s)
            return resultado
        except Exception as erro:
            # Fica pendente: outro worker retoma depois de ocioso_ms. Só o tipo do erro: a mensagem pode ter conteúdo.
            log.warning('Falha ao processar item da fila (%s); será retomado', type(erro).__name__)

    async def confirmar(self, stream, item_id, chave):
        await self.redis.xack(stream, GRUPO, item_id)
        await self.redis.xdel(stream, item_id)
        await self.redis.srem(self.k_na_fila, chave)

    # ---------------- indicadores e alertas ----------------
    async def _chaves(self, padrao):
        return [k async for k in self.redis.scan_iter(match=padrao, count=200)]

    async def indicadores(self):
        agora_ms = time.time() * 1000
        empresas = {}
        for t in sorted(await self.redis.smembers(self.k_tenants)):
            s = self.stream(t); aguardando = 0; pendentes = 0; mais_antiga = 0.0
            try:
                for g in await self.redis.xinfo_groups(s):
                    if g['name'] != GRUPO: continue
                    pendentes = int(g.get('pending') or 0)
                    aguardando = int(g.get('lag') or 0)
                    ultimo = g.get('last-delivered-id') or '0-0'
                    primeiro = await self.redis.xrange(s, min='(' + ultimo, max='+', count=1)
                    if primeiro:
                        mais_antiga = max(0.0, (agora_ms - int(primeiro[0][0].split('-')[0])) / 1000)
            except Exception:
                pass  # fila ainda não criada
            empresas[t] = {'aguardando': aguardando, 'em_processamento': pendentes,
                           'mais_antiga_s': round(mais_antiga, 1)}
        agentes = {}
        base = f'{self.prefixo}:fila:ativos:'
        for k in await self._chaves(base + '*'):
            tenant, agente = k[len(base):].split(':', 1)
            n = await self.redis.zcount(k, agora_ms, '+inf')
            if n: agentes.setdefault(tenant, {})[agente] = n
        cota = []
        controle = self.p.controle
        for k in await self._chaves(f'{self.prefixo}:skyone:url:*'):
            uso = await self.redis.zcount(k, agora_ms - controle.janela_ms, '+inf')
            cota.append({'url': k.rsplit(':', 1)[1][:12], 'uso': uso, 'limite': controle.limite,
                         'percentual': round(uso / controle.limite, 3) if controle.limite else 0})
        workers = len(await self._chaves(f'{self.prefixo}:fila:worker:*'))
        return {'ligada': self.ligada, 'empresas': empresas, 'agentes_em_andamento': agentes,
                'cota_skyone': sorted(cota, key=lambda c: c['url']), 'workers_vivos': workers,
                'agendador_ativo': bool(await self.redis.exists(self.k_lider)),
                'varredura': await self.varredura.indicador(),
                'aguardando': sum(e['aguardando'] for e in empresas.values()),
                'mais_antiga_s': max([e['mais_antiga_s'] for e in empresas.values()] or [0.0])}

    async def alertas(self, ind):
        """Fila parada há mais de alerta_fila_s e cota de URL acima de alerta_cota por alerta_cota_s.
        Agrupados (uma vez a cada 10/30 min) e registrados na auditoria e no log, sem URL."""
        for tenant, e in ind['empresas'].items():
            if e['mais_antiga_s'] > self.alerta_fila_s and await self.redis.set(
                    f'{self.prefixo}:alerta:fila:{tenant}', '1', nx=True, ex=600):
                log.warning('Fila atrasada: mensagem esperando há mais de %s s', self.alerta_fila_s)
                async with self.db.acquire() as conn, conn.transaction():
                    await auditar(conn, tenant, 'sistema', 'fila.atrasada', tenant)
        agora = time.time()
        for c in ind['cota_skyone']:
            desde_k = f'{self.prefixo}:alerta:cota:desde:{c["url"]}'
            if c['percentual'] < self.alerta_cota:
                await self.redis.delete(desde_k); continue
            await self.redis.set(desde_k, str(agora), nx=True, ex=max(60, int(self.alerta_cota_s * 3)))
            desde = float(await self.redis.get(desde_k) or agora)
            if agora - desde >= self.alerta_cota_s and await self.redis.set(
                    f'{self.prefixo}:alerta:cota:{c["url"]}', '1', nx=True, ex=1800):
                log.warning('Cota do Skyone acima de %s%% por %s s', int(self.alerta_cota * 100), self.alerta_cota_s)
                async with self.db.acquire() as conn, conn.transaction():
                    await auditar(conn, None, 'sistema', 'skyone.cota_alta', 'url:' + c['url'])

    def resumo(self, ind):
        """Versão sem empresas nem URLs, para o /health interno."""
        return {k: ind[k] for k in ('ligada', 'aguardando', 'mais_antiga_s', 'workers_vivos', 'agendador_ativo')}
