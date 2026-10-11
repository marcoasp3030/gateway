"""Alertas operacionais pelo Telegram (OPS1, docs/ESPEC_OPS1.md), do lado do gateway: worker, fila, Skyone e WhatsApp.
Os de backup, disco, memória e contêiner parado são do script da VPS (scripts/vigia.sh), que funciona com o gateway fora do ar.

Regras: o mesmo alerta sai no máximo uma vez a cada `repetir_s` (lembrete enquanto durar); quando o problema acaba sai
um "normalizado" com a duração. Mensagem nunca leva telefone, nome nem texto de conversa. Sem bot configurado, nada é
enviado: o estado e a auditoria continuam, e o log avisa uma vez."""
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

log=logging.getLogger('gateway.alertas')
ICONES={'aviso':'🟡','critico':'🔴'}
FUSO=ZoneInfo('America/Sao_Paulo')


def _numero(nome,padrao,minimo,maximo):
    try: valor=float(os.getenv(nome,'') or padrao)
    except ValueError: valor=padrao
    return min(max(valor,minimo),maximo)


def limites():
    """Limites dos alertas, todos configuráveis por variável de ambiente."""
    return {'repetir_s':_numero('ALERTA_REPETIR_MIN',30,1,1440)*60,
            'fila_espera_s':_numero('ALERTA_FILA_ESPERA_S',60,5,3600),'fila_conversas':_numero('ALERTA_FILA_CONVERSAS',50,1,100000),
            'skyone_erro_pct':_numero('ALERTA_SKYONE_ERRO_PCT',20,1,100),'skyone_min_chamadas':_numero('ALERTA_SKYONE_MIN_CHAMADAS',5,1,100000),
            'skyone_janela_min':_numero('ALERTA_SKYONE_JANELA_MIN',10,1,120),
            'worker_s':_numero('ALERTA_WORKER_S',90,10,3600),'whatsapp_s':_numero('ALERTA_WHATSAPP_MIN',5,1,1440)*60,
            'workers_esperados':int(_numero('WORKERS',2,0,64))}


def duracao(segundos):
    minutos=int(segundos//60)
    if minutos<1: return 'menos de 1 min'
    if minutos<60: return f'{minutos} min'
    return f'{minutos//60} h {minutos%60:02d} min'


class Alertas:
    def __init__(self,redis,http,db,prefixo,token=None,chat=None,repetir_s=1800,relogio=time.time):
        self.redis,self.http,self.db,self.prefixo=redis,http,db,prefixo
        self.token=(token if token is not None else os.getenv('TELEGRAM_BOT_TOKEN','')).strip()
        self.chat=(chat if chat is not None else os.getenv('TELEGRAM_CHAT_ID','')).strip()
        self.repetir_s,self.relogio=repetir_s,relogio
        self._avisou_desligado=False

    @property
    def configurado(self): return bool(self.token and self.chat)

    def _k(self,chave): return f'{self.prefixo}:ops:alerta:{chave}'

    async def _enviar(self,texto):
        """True se saiu. Erro de envio não derruba quem chamou e não aparece com o token no log."""
        if not self.configurado:
            if not self._avisou_desligado:
                log.warning('Alertas desligados: TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID não configurados');self._avisou_desligado=True
            return False
        try:
            r=await self.http.post(f'https://api.telegram.org/bot{self.token}/sendMessage',json={'chat_id':self.chat,'text':texto,'disable_web_page_preview':True},timeout=10)
            if r.status_code==200: return True
            log.warning('Alerta não enviado: o Telegram respondeu %s',r.status_code)
        except Exception as erro:
            log.warning('Alerta não enviado (%s)',type(erro).__name__)
        return False

    def _texto(self,icone,titulo,detalhe):
        agora=datetime.fromtimestamp(self.relogio(),FUSO).strftime('%d/%m %H:%M')
        return f'{icone} GATEWAY — {titulo}'+(f'\n{detalhe}' if detalhe else '')+f'\n{agora}'

    async def _auditar(self,acao,chave):
        try:
            from admin.servico import auditar
            async with self.db.acquire() as conn,conn.transaction(): await auditar(conn,None,'sistema',acao,chave[:200])
        except Exception: log.warning('Alerta não registrado na auditoria')

    async def disparar(self,chave,nivel,titulo,detalhe=''):
        """'novo' (primeira vez), 'lembrete' (passou o intervalo) ou 'suprimido'."""
        agora=self.relogio();k=self._k(chave);estado=await self.redis.hgetall(k)
        if not estado:
            await self.redis.hset(k,mapping={'inicio':agora,'ultimo':0,'titulo':titulo});await self.redis.expire(k,7*86400)
            await self._auditar('ops.alerta',chave)
            if await self._enviar(self._texto(ICONES.get(nivel,'🔴'),titulo,detalhe)): await self.redis.hset(k,'ultimo',agora)
            return 'novo'
        if agora-float(estado.get('ultimo') or 0)<self.repetir_s: return 'suprimido'
        continua=f'Continua há {duracao(agora-float(estado["inicio"]))}.'
        if await self._enviar(self._texto(ICONES.get(nivel,'🔴'),titulo,(detalhe+'\n' if detalhe else '')+continua)): await self.redis.hset(k,'ultimo',agora)
        await self.redis.expire(k,7*86400)
        return 'lembrete'

    async def normalizar(self,chave,titulo=None,detalhe=''):
        """Uma mensagem de "normalizado", só se havia alerta aberto. Devolve True se havia."""
        k=self._k(chave);estado=await self.redis.hgetall(k)
        if not estado: return False
        await self.redis.delete(k);await self._auditar('ops.normalizado',chave)
        durou=f'Durou {duracao(self.relogio()-float(estado["inicio"]))}.'
        await self._enviar(self._texto('🟢',f'normalizado: {titulo or estado.get("titulo") or chave}',(detalhe+' · ' if detalhe else '')+durou))
        return True

    async def avaliar(self,chave,ativa,nivel,titulo,detalhe='',apos_s=0,calma_s=0):
        """Condição observada a cada conferência: dispara depois de ativa por `apos_s` seguidos e
        normaliza depois de inativa por `calma_s` seguidos. Um pico isolado não alerta nem normaliza."""
        agora=self.relogio();k=self._k('obs:'+chave)
        if ativa:
            await self.redis.hdel(k,'inativa_desde')
            # Alerta já aberto e o problema voltou antes de normalizar: segue o mesmo alerta (só o lembrete, no intervalo).
            if await self.redis.exists(self._k(chave)): return await self.disparar(chave,nivel,titulo,detalhe)
            if await self.redis.hsetnx(k,'ativa_desde',agora): await self.redis.expire(k,86400)
            if agora-float(await self.redis.hget(k,'ativa_desde') or agora)>=apos_s: return await self.disparar(chave,nivel,titulo,detalhe)
            return 'aguardando'
        await self.redis.hdel(k,'ativa_desde')
        if not await self.redis.exists(self._k(chave)):
            await self.redis.delete(k);return 'normal'
        if await self.redis.hsetnx(k,'inativa_desde',agora): await self.redis.expire(k,86400)
        if agora-float(await self.redis.hget(k,'inativa_desde') or agora)>=calma_s:
            await self.redis.delete(k);await self.normalizar(chave,titulo);return 'normalizado'
        return 'acalmando'


async def conferir(alertas,db,indicadores,lim=None):
    """Uma rodada das conferências do gateway. `indicadores` = FilaDuravel.indicadores()."""
    lim=lim or limites()
    # Worker parado: menos workers vivos que o configurado.
    faltam=lim['workers_esperados']-int(indicadores.get('workers_vivos') or 0)
    if indicadores.get('ligada'):
        await alertas.avaliar('worker',faltam>0,'critico','worker parado',f'{indicadores.get("workers_vivos") or 0} de {lim["workers_esperados"]} workers ativos',lim['worker_s'],120)
    # Fila acima do limite, por empresa (identificador da empresa; nada de cliente).
    for tenant,e in (indicadores.get('empresas') or {}).items():
        cheia=e['mais_antiga_s']>lim['fila_espera_s'] or e['aguardando']>lim['fila_conversas']
        await alertas.avaliar(f'fila:{tenant}',cheia,'critico','fila acima do limite',
                              f'Empresa: {tenant} · {e["aguardando"]} conversa(s) esperando · a mais antiga há {int(e["mais_antiga_s"])} s',30,120)
    # Erro do Skyone: proporção de turnos com erro do agente na janela, com um mínimo de chamadas.
    r=await db.fetchrow("""SELECT count(*) AS total,count(*) FILTER (WHERE status='erro') AS erros FROM turnos
        WHERE modo_agente='live' AND agente_fim IS NOT NULL AND agente_inicio>now()-make_interval(mins=>$1)""",int(lim['skyone_janela_min']))
    total,erros=int(r['total']),int(r['erros']);pct=100*erros/total if total else 0
    alto=total>=lim['skyone_min_chamadas'] and pct>lim['skyone_erro_pct']
    detalhe=f'{erros} erro(s) em {total} chamada(s) nos últimos {int(lim["skyone_janela_min"])} min ({pct:.0f}%)'
    if alto: await alertas.avaliar('skyone',True,'critico','erro do Skyone acima do limite',detalhe)
    # Só normaliza com folga: abaixo da metade do limite (ou sem movimento) por 10 min. Entre a metade e o limite, fica como está.
    elif total<lim['skyone_min_chamadas'] or pct<lim['skyone_erro_pct']/2: await alertas.avaliar('skyone',False,'critico','erro do Skyone acima do limite',detalhe,0,600)
    # WhatsApp desconectado: canal uazapi ativo cujo último estado conhecido não é "conectado".
    for c in await db.fetch("SELECT tenant_id,id,status_conexao FROM tenant_canais WHERE tipo='uazapi' AND ativo AND status_conexao IS NOT NULL"):
        await alertas.avaliar(f'whatsapp:{c["id"]}',c['status_conexao']!='conectado','critico','WhatsApp desconectado',
                              f'Empresa: {c["tenant_id"]} · canal {c["id"][-6:]} · estado: {str(c["status_conexao"])[:30]}',lim['whatsapp_s'],60)


async def situacao_ops(db,redis,prefixo,agora=None):
    """Motivos pelos quais a operação NÃO está em ordem (lista vazia = tudo certo). Usado pelo /health/ops."""
    motivos=[]
    ultimo=await db.fetchval("SELECT resultado FROM ops_execucoes WHERE tipo='backup' ORDER BY id DESC LIMIT 1")
    if ultimo is None: motivos.append('backup_nunca_executado')
    else:
        # Backup feito (enviado ou só local) há mais de 26 h = atrasado.
        if await db.fetchval("SELECT coalesce(now()-max(fim)>interval '26 hours',true) FROM ops_execucoes WHERE tipo='backup' AND resultado IN ('ok','local')"): motivos.append('backup_atrasado')
        if ultimo=='falha': motivos.append('backup_falhou')
        # Backup só na VPS não protege contra a perda da máquina: enquanto o destino externo não estiver configurado, não está em ordem.
        if ultimo=='local': motivos.append('destino_nao_configurado')
    t=await db.fetchrow("SELECT resultado FROM ops_execucoes WHERE tipo='restauracao' ORDER BY id DESC LIMIT 1")
    if t is not None and t['resultado']=='falha': motivos.append('teste_de_restauracao_falhou')
    if not await redis.exists(f'{prefixo}:fila:lider'): motivos.append('agendador_sem_sinal')
    return motivos
