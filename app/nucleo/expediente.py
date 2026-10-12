"""Fora do horário (H1-a) e horário da equipe (T1) — docs/ESPEC_CFG1C.md.

A empresa está fora do horário quando TODAS as unidades ativas estão fechadas agora (grade, exceção ou pausa).
Unidade sem horário cadastrado não dá para afirmar: a empresa não é tratada como fechada."""
import re
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from nucleo import horarios as H

DIAS=('segunda-feira','terça-feira','quarta-feira','quinta-feira','sexta-feira','sábado','domingo')
VARIAVEIS={'fora_do_horario_mensagem':('proxima_abertura','nome_empresa','abre'),'transbordo_fora_do_horario':('retorno_equipe','horario')}
SEM_PERIODO_H=12


def momento(iso,fuso):
    """{"dia_semana","data","hora"} de um instante ISO, no fuso dado."""
    d=datetime.fromisoformat(iso).astimezone(ZoneInfo(fuso))
    return {'dia_semana':DIAS[d.weekday()],'data':d.date().isoformat(),'hora':d.strftime('%H:%M')}


def em_palavras(iso,fuso,agora=None):
    """"hoje às 18h", "amanhã às 12h", "terça-feira às 12h", "dia 20/10 às 12h"."""
    if not iso: return ''
    z=ZoneInfo(fuso);d=datetime.fromisoformat(iso).astimezone(z);hoje=(agora or datetime.now(timezone.utc)).astimezone(z).date()
    hora=f'{d.hour}h' if d.minute==0 else f'{d.hour}h{d.minute:02d}'
    dias=(d.date()-hoje).days
    quando='hoje' if dias==0 else 'amanhã' if dias==1 else DIAS[d.weekday()] if 1<dias<7 else d.strftime('dia %d/%m')
    return f'{quando} às {hora}'


def renderizar(modelo,valores):
    """Troca {variavel}; trecho [[ … ]] com variável vazia some inteiro. Variável desconhecida fica como está."""
    def opcional(m):
        trecho=m.group(1)
        return trecho if all(str(valores.get(v) or '').strip() for v in re.findall(r'\{(\w+)\}',trecho)) else ''
    texto=re.sub(r'\[\[(.*?)\]\]',opcional,modelo or '',flags=re.S)
    return re.sub(r'\{(\w+)\}',lambda m: str(valores.get(m.group(1)) or '') if m.group(1) in valores else m.group(0),texto).strip()


def variaveis_desconhecidas(campo,texto):
    return sorted({v for v in re.findall(r'\{(\w*)\}',texto or '') if v not in VARIAVEIS.get(campo,())})


async def da_empresa(db,tenant,fuso,agora=None):
    """{"fora": bool, "proxima": ISO|None, "unidade": nome|None, "codigo": código|None, "fuso": fuso da unidade}."""
    agora=agora or datetime.now(timezone.utc)
    unidades=await db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND ativo ORDER BY id',tenant)
    proxima=None;fechadas=0
    for u in unidades:
        s=await H.situacao_da_unidade(db,tenant,u,fuso,agora)
        if s['aberta_agora'] is False:
            fechadas+=1
            quando=s.get('proxima_abertura') or s.get('pausada_ate')
            if quando and (proxima is None or datetime.fromisoformat(quando)<datetime.fromisoformat(proxima['proxima'])):
                proxima={'proxima':quando,'unidade':u['nome'],'codigo':u['codigo'],'fuso':u['fuso'] or fuso}
    fora=bool(unidades) and fechadas==len(unidades)
    if not fora: return {'fora':False,'proxima':None,'unidade':None,'codigo':None,'fuso':fuso}
    return {'fora':True,**(proxima or {'proxima':None,'unidade':None,'codigo':None,'fuso':fuso})}


def proxima_abertura(situacao):
    """Formato do pacote: {"unidade","codigo","dia_semana","data","hora"} ou None."""
    if not situacao.get('proxima'): return None
    return {'unidade':situacao['unidade'],'codigo':situacao['codigo'],**momento(situacao['proxima'],situacao['fuso'])}


async def da_equipe(db,tenant,cfg,fuso,agora=None,empresa=None):
    """Equipe humana disponível agora? {"disponivel": bool, "retorno": ISO|None, "fuso": fuso}.
    Grade própria (transbordo_horario_equipe) se houver; vazia = segue o horário das unidades."""
    agora=agora or datetime.now(timezone.utc);grade=cfg.get('transbordo_horario_equipe')
    if isinstance(grade,dict) and any(grade.values()):
        s=H.situacao(grade,fuso,agora)
        return {'disponivel':s['aberta_agora'] is not False,'retorno':s.get('proxima_abertura') if s['aberta_agora'] is False else None,'fuso':fuso}
    empresa=empresa or await da_empresa(db,tenant,fuso,agora)
    return {'disponivel':not empresa['fora'],'retorno':empresa['proxima'] if empresa['fora'] else None,'fuso':empresa['fuso']}


def retorno_equipe(equipe):
    return momento(equipe['retorno'],equipe['fuso']) if equipe.get('retorno') else None
