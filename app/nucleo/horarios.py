from datetime import datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
import re
DIAS=('seg','ter','qua','qui','sex','sab','dom')

def validar(horarios):
    if not isinstance(horarios,dict) or set(horarios)-set(DIAS): raise ValueError('Horários inválidos')
    for faixas in horarios.values():
        if not isinstance(faixas,list) or len(faixas)>12: raise ValueError('Faixas inválidas')
        for faixa in faixas:
            if not isinstance(faixa,list) or len(faixa)!=2: raise ValueError('Faixa inválida')
            if any(not isinstance(x,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',x) for x in faixa) or faixa[0]==faixa[1]: raise ValueError('Hora inválida')
    return horarios

def estado(horarios,fuso,agora=None):
    validar(horarios);z=ZoneInfo(fuso);agora=(agora or datetime.now(timezone.utc)).astimezone(z)
    intervalos=[]
    for d in range(-1,8):
        data=agora.date()+timedelta(days=d)
        for ini,fim in horarios.get(DIAS[data.weekday()],[]):
            a=datetime.combine(data,time.fromisoformat(ini),z)
            b=datetime.combine(data+(timedelta(days=1) if fim<ini else timedelta()),time.fromisoformat(fim),z)
            intervalos.append((a,b))
    aberta=any(a<=agora<b for a,b in intervalos)
    futuras=[a for a,b in intervalos if a>agora]
    return {'aberta_agora':aberta,'proxima_abertura':min(futuras).isoformat() if futuras and not aberta else None}

async def pacote(db,tenant,fuso,agora=None):
    rows=await db.fetch('SELECT id,nome,horarios FROM tenant_unidades WHERE tenant_id=$1 AND ativo ORDER BY id',tenant)
    import json
    return [{'id':r['id'],'nome':r['nome'],**estado(json.loads(r['horarios']) if isinstance(r['horarios'],str) else r['horarios'],fuso,agora)} for r in rows]
