"""Postgres é a fonte; Redis é contador reconstruível, nunca um bloqueio."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import re,logging
from nucleo.banco import Repositorio
from nucleo.webhooks import emitir
from admin.servico import auditar
log=logging.getLogger('gateway.consumo')

def intervalo(fuso,mes=None,agora=None):
    z=ZoneInfo(fuso);atual=(agora or datetime.now(timezone.utc)).astimezone(z)
    if mes is None: mes=atual.strftime('%Y-%m')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',mes): raise ValueError('Mês inválido')
    ano,m=map(int,mes.split('-'));inicio=datetime(ano,m,1,tzinfo=z)
    fim=datetime(ano+1,1,1,tzinfo=z) if m==12 else datetime(ano,m+1,1,tzinfo=z)
    return mes,inicio,fim

async def consulta(db,t,mes=None):
    empresa=await Repositorio(db).tenant(t)
    if not empresa: raise LookupError('Tenant inexistente')
    mes,inicio,fim=intervalo(empresa['timezone'],mes)
    rows=await db.fetch('SELECT agente_id,count(*) AS turnos FROM turnos WHERE tenant_id=$1 AND agente_inicio >= $2 AND agente_inicio < $3 GROUP BY agente_id ORDER BY agente_id',t,inicio,fim)
    total=sum(r['turnos'] for r in rows)
    avisos=await db.fetch('SELECT percentual,turnos,limite,criado_em FROM consumo_avisos WHERE tenant_id=$1 AND mes=$2 ORDER BY percentual',t,mes)
    return {'mes':mes,'turnos':total,'limite':empresa['limite_turnos_mes'],'agentes':[dict(r) for r in rows],'avisos':[dict(r) for r in avisos]}

async def registrar(c,t):
    empresa=await Repositorio(c).tenant(t);mes,inicio,fim=intervalo(empresa['timezone'])
    await c.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','consumo:'+t+':'+mes)
    total=await c.fetchval('SELECT count(*) FROM turnos WHERE tenant_id=$1 AND agente_inicio >= $2 AND agente_inicio < $3',t,inicio,fim)
    limite=empresa['limite_turnos_mes']
    if limite is not None:
        for p in (80,100):
            if total*100>=limite*p:
                novo=await c.fetchval('INSERT INTO consumo_avisos(tenant_id,mes,percentual,turnos,limite) VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING RETURNING percentual',t,mes,p,total,limite)
                if novo:
                    await auditar(c,t,'sistema','consumo.aviso_'+str(p),mes)
                    await emitir(c,t,'consumo.limite',{'mes':mes,'percentual':p,'turnos':total,'limite':limite})
    return mes,total

async def atualizar_cache(redis,prefixo,t,mes,total):
    try:
        await redis.eval("local n=tonumber(redis.call('GET',KEYS[1])) or 0; local v=math.max(n,tonumber(ARGV[1])); redis.call('SET',KEYS[1],v,'EX',5356800); return v",1,prefixo+':consumo:'+t+':'+mes,total)
    except Exception:
        log.warning('Cache de consumo indisponível; Postgres preservado')
