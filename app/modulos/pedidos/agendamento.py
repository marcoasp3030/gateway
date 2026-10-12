"""Horário agendado do pedido (PED2, docs/ESPEC_PED2.md).

O agente manda `agendado_para` (data e hora) ou só o texto `horario` ("hoje às 19h", "19:30", "amanhã ao meio-dia",
"o quanto antes"). Aqui o texto vira data e hora e o horário é conferido: não pode estar no passado, nem fora do
funcionamento da unidade naquele dia, nem a mais de 7 dias."""
import json,re,unicodedata
from datetime import datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
from nucleo import horarios as H
from nucleo import expediente

TOLERANCIA_MIN=5
MAX_DIAS=7
SEMANA={'segunda':0,'terca':1,'quarta':2,'quinta':3,'sexta':4,'sabado':5,'domingo':6}
# Pedido sem hora marcada: vale como "o quanto antes".
_SEM_HORA=re.compile(r'\b(quanto antes|agora|ja|imediat\w*|assim que (der|puder|possivel|estiver pronto|ficar pronto)|logo que (der|puder|possivel)|o mais rapido( possivel)?|quando (der|puder|ficar pronto|estiver pronto)|sem horario)\b')
_HORA=re.compile(r'(?<![\d/])([01]?\d|2[0-3])\s*(?:(?:h(?:oras?|rs?)?|:)\s*([0-5]\d)?)(?!\d)')
_HORA_SOLTA=re.compile(r'\b(?:as|a|pelas|la pelas|por volta d[ae]s?)\s+([01]?\d|2[0-3])\b(?!\s*[/:h\d])')
_PERIODO=re.compile(r'\b([01]?\d)\s*(?:h(?:oras?)?)?\s*(?:e\s*(meia|[0-5]\d)\s*)?d[ae]\s+(manha|tarde|noite|madrugada)\b')
_DATA=re.compile(r'\b(?:dia\s+)?([0-3]?\d)/([01]?\d)(?:/(\d{4}|\d{2}))?\b')
_DIA=re.compile(r'\bdia\s+([0-3]?\d)\b(?!\s*/)')
# "daqui a 40 minutos", "em 2 horas", "daqui 1h30".
_DAQUI=re.compile(r'\b(?:daqui(?: a)?|em|dentro de)\s+(?:(\d{1,2})\s*(?:h(?:oras?|rs?)?)\s*(?:e\s*)?(?:(\d{1,2})(?:\s*min\w*)?|(meia))?|(\d{1,3})\s*min\w*|(meia) hora|(uma) hora)(?![\d:])')


def agora_utc():
    """O instante de agora (um ponto só, para os testes fixarem o relógio)."""
    return datetime.now(timezone.utc)


def _normal(texto):
    sem=''.join(c for c in unicodedata.normalize('NFKD',str(texto or '')) if not unicodedata.combining(c))
    return ' '.join(sem.lower().replace('º','').split())


def _hora(t):
    """(hora, minuto) lidos do texto, ou None."""
    if re.search(r'\bmeio[ -]?dia\b',t): return 12,30 if re.search(r'meio[ -]?dia e meia',t) else 0
    if re.search(r'\bmeia[ -]?noite\b',t): return 0,0
    m=_PERIODO.search(t)
    if m:
        h=int(m.group(1));minuto=30 if m.group(2)=='meia' else int(m.group(2) or 0)
        if m.group(3) in ('tarde','noite') and h<12: h+=12
        return (h,minuto) if h<24 else None
    m=_HORA.search(t)
    if m:
        minuto=int(m.group(2) or 0)
        if re.search(r'\be meia\b',t[m.end():m.end()+8]) and not m.group(2): minuto=30
        return int(m.group(1)),minuto
    m=_HORA_SOLTA.search(t)
    if m: return int(m.group(1)),0
    return None


def _dia(t,hoje):
    """Data citada no texto; None = não citou (vale hoje); False = citou uma data que não existe."""
    if 'depois de amanha' in t: return hoje+timedelta(days=2)
    if re.search(r'\bamanha\b',t): return hoje+timedelta(days=1)
    if re.search(r'\bhoje\b',t): return hoje
    m=_DATA.search(t)
    if m:
        ano=int(m.group(3)) if m.group(3) else hoje.year
        if ano<100: ano+=2000
        try: d=hoje.replace(year=ano,month=int(m.group(2)),day=int(m.group(1)))
        except ValueError: return False
        # Sem ano e já passou há muito: é do ano que vem ("05/01" dito em dezembro).
        if not m.group(3) and (hoje-d).days>180: d=d.replace(year=ano+1)
        return d
    m=_DIA.search(t)
    if m:
        try: d=hoje.replace(day=int(m.group(1)))
        except ValueError: return False
        if d<hoje:
            mes=hoje.month%12+1
            try: d=d.replace(year=hoje.year+(1 if mes==1 else 0),month=mes)
            except ValueError: return False
        return d
    for nome,indice in SEMANA.items():
        if re.search(rf'\b{nome}(-feira| feira)?\b',t):
            # "sexta" dito numa sexta é hoje; nos outros dias, a próxima.
            return hoje+timedelta(days=(indice-hoje.weekday())%7)
    return None


def interpretar(texto,agora):
    """('agendado', datetime) | ('sem_agendamento', None) | ('invalido', None). `agora` no fuso da unidade."""
    t=_normal(texto)
    if not t: return 'sem_agendamento',None
    m=_DAQUI.search(t)
    if m:
        minutos=int(m.group(1) or 0)*60+int(m.group(2) or 0)+(30 if m.group(3) or m.group(5) else 0)+int(m.group(4) or 0)+(60 if m.group(6) else 0)
        if 0<minutos<=MAX_DIAS*1440: return 'agendado',(agora+timedelta(minutes=minutos)).replace(second=0,microsecond=0)
        return 'invalido',None
    hora=_hora(t);dia=_dia(t,agora.date())
    if dia is False: return 'invalido',None
    if hora is None:
        return ('sem_agendamento',None) if _SEM_HORA.search(t) and dia in (None,agora.date()) else ('invalido',None)
    quando=datetime.combine(dia or agora.date(),time(hora[0],hora[1]),agora.tzinfo)
    if quando<agora:
        # "meia-noite" sem dia é a próxima; dia da semana que já passou hoje é o da semana que vem.
        if dia is None and hora==(0,0): quando+=timedelta(days=1)
        elif dia==agora.date() and any(re.search(rf'\b{nome}(-feira| feira)?\b',t) for nome in SEMANA) and not re.search(r'\bhoje\b',t): quando+=timedelta(days=7)
    return 'agendado',quando


def de_iso(valor,fuso):
    """Data e hora de `agendado_para`. Sem fuso, vale o da unidade. None = não é data e hora."""
    try: d=datetime.fromisoformat(str(valor).strip().replace('Z','+00:00'))
    except ValueError: return None
    if not isinstance(d,datetime) or 'T' not in str(valor).upper().replace(' ','T'): return None
    return d.replace(tzinfo=ZoneInfo(fuso)) if d.tzinfo is None else d


def iso(quando,fuso):
    return quando.astimezone(ZoneInfo(fuso)).isoformat(timespec='seconds') if quando else None


def vencido(quando,agora=None):
    return bool(quando) and quando<(agora or agora_utc())-timedelta(minutes=TOLERANCIA_MIN)


def do_carrinho(c,fuso):
    """Horário agendado de um carrinho. Carrinho gravado antes do PED2 só tem o texto: ele é lido em relação
    ao dia em que foi escrito ("hoje às 19h" de ontem continua sendo ontem)."""
    if c.get('agendado_para'): return c['agendado_para']
    if not c['horario'] or not c.get('atualizado_em'): return None
    situacao,quando=interpretar(c['horario'],c['atualizado_em'].astimezone(ZoneInfo(fuso)))
    return quando if situacao=='agendado' else None


async def _abertura(conn,tenant,u,fuso,instante):
    """(situação da unidade em `instante`, só pela grade e pelas exceções; faixas daquele dia)."""
    horarios=json.loads(u['horarios']) if isinstance(u['horarios'],str) else u['horarios']
    dia=instante.astimezone(ZoneInfo(fuso)).date()
    exc=await H.excecoes_da_unidade(conn,tenant,u['id'],dia-timedelta(days=2),dia+timedelta(days=H.DIAS_A_FRENTE+1))
    # Pausa manual não entra: ela vale para agora, não para um horário marcado adiante.
    s=H.situacao(horarios,fuso,instante,exc)
    faixas,_=H.faixas_do_dia(horarios,H._excecoes_por_data(exc),dia)
    return s,[list(f) for f in faixas]


def _proxima(u,fuso,s):
    if not s.get('proxima_abertura'): return None
    return {'unidade':u['nome'],'codigo':u['codigo'],**expediente.momento(s['proxima_abertura'],fuso)}


async def conferir(conn,tenant,u,fuso,quando,agora=None):
    """Motivo e detalhes da recusa do horário agendado, ou (None, {}) se ele vale.
    `u` é a linha da unidade (None = carrinho ainda sem unidade: só dá para dizer se já passou)."""
    agora=agora or agora_utc()
    base={'agendado_para':iso(quando,fuso),'agora':iso(agora,fuso)}
    if vencido(quando,agora):
        proxima=aberta=None
        if u:
            s,_=await _abertura(conn,tenant,u,fuso,agora);aberta=s['aberta_agora'];proxima=_proxima(u,fuso,s)
        return 'horario_passado',{**base,'aberta_agora':aberta,'proxima_abertura':proxima}
    if quando>agora+timedelta(days=MAX_DIAS): return 'horario_muito_distante',{**base,'max_dias':MAX_DIAS}
    if u:
        s,faixas=await _abertura(conn,tenant,u,fuso,quando)
        # Sem horário cadastrado não dá para afirmar que está fechada: a unidade confirma.
        if s['aberta_agora'] is False:
            return 'horario_fora_funcionamento',{**base,'aberta_no_dia':faixas,'proxima_abertura':_proxima(u,fuso,s)}
    return None,{}
