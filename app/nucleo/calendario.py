"""Calendário do pacote (CAL1, docs/ESPEC_CAL1.md): hoje + 7 dias, com feriado e observação de cada dia.

O agente nunca calcula datas: lê daqui o dia da semana, o que é feriado e o que muda nas unidades.
Fontes: biblioteca `holidays` (nacionais e estaduais), lista de municipais por cidade (abaixo), pontos facultativos
pela Páscoa e as exceções por data cadastradas nas unidades. Tudo calculado no fuso da empresa."""
import json
import logging
import unicodedata
from datetime import date,datetime,timedelta,timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

log=logging.getLogger('gateway.calendario')
DIAS=('segunda-feira','terça-feira','quarta-feira','quinta-feira','sexta-feira','sábado','domingo')
DIAS_A_FRENTE=7
MAX_OBSERVACAO=160
# Feriados municipais por (cidade sem acento e minúscula, UF): datas fixas (mês, dia) e, se a cidade guarda, Corpus Christi.
MUNICIPAIS={('sao paulo','SP'):{'fixos':{(1,25):'Aniversário de São Paulo'},'corpus_christi':True}}


def _chave(texto):
    sem=''.join(c for c in unicodedata.normalize('NFKD',str(texto or '')) if not unicodedata.combining(c))
    return ' '.join(sem.lower().split())


def pascoa(ano):
    """Domingo de Páscoa (calendário gregoriano, algoritmo de Meeus/Butcher)."""
    a=ano%19;b,c=divmod(ano,100);d,e=divmod(b,4);f=(b+8)//25;g=(b-f+1)//3
    h=(19*a+b-d-g+15)%30;i,k=divmod(c,4);l=(32+2*e+2*i-h-k)%7;m=(a+11*h+22*l)//451
    mes,dia=divmod(h+l-7*m+114,31)
    return date(ano,mes,dia+1)


@lru_cache(maxsize=64)
def oficiais(ano,uf=None):
    """{data: (nome, 'nacional'|'estadual')} da biblioteca holidays (só feriado oficial; ponto facultativo fica de fora)."""
    import holidays
    from holidays.constants import PUBLIC
    nacionais=holidays.country_holidays('BR',years=ano,language='pt_BR',categories=(PUBLIC,))
    saida={d:(nome,'nacional') for d,nome in nacionais.items()}
    if uf:
        try: estado=holidays.country_holidays('BR',subdiv=uf,years=ano,language='pt_BR',categories=(PUBLIC,))
        except NotImplementedError: estado={}
        for d,nome in estado.items(): saida.setdefault(d,(nome,'estadual'))
    return saida


def municipais(ano,cidade,uf):
    regra=MUNICIPAIS.get((_chave(cidade),str(uf or '').upper()))
    if not regra: return {}
    saida={date(ano,mes,dia):nome for (mes,dia),nome in regra.get('fixos',{}).items()}
    if regra.get('corpus_christi'): saida[pascoa(ano)+timedelta(days=60)]='Corpus Christi'
    return saida


def facultativos(ano):
    """Pontos facultativos que mexem com restaurante: Carnaval (segunda e terça) e Quarta-feira de Cinzas."""
    p=pascoa(ano)
    return {p-timedelta(days=48):'Carnaval (ponto facultativo)',p-timedelta(days=47):'Carnaval (ponto facultativo)',
            p-timedelta(days=46):'Quarta-feira de Cinzas (ponto facultativo)'}


def _hora(h):
    hora,minuto=int(h[:2]),int(h[3:5])
    return f'{hora}h' if minuto==0 else f'{hora}h{minuto:02d}'


def _faixas(faixas):
    return ' e '.join(f'{_hora(a)}–{_hora(b)}' for a,b in faixas)


def _feriado(dia,unidades):
    """(nome, tipo, lugares onde vale quando não vale para todas as unidades)."""
    ufs={u['uf'] for u in unidades if u.get('uf')}
    nacional=oficiais(dia.year).get(dia)
    if nacional: return nacional[0],'nacional',None
    for uf in sorted(ufs):
        achado=oficiais(dia.year,uf).get(dia)
        if achado:
            vale=[u for u in unidades if u.get('uf')==uf]
            return achado[0],'estadual',None if len(vale)==len(unidades) else uf
    for cidade,uf in sorted({(u['cidade'],u['uf']) for u in unidades if u.get('cidade') and u.get('uf')}):
        nome=municipais(dia.year,cidade,uf).get(dia)
        if nome:
            vale=[u for u in unidades if _chave(u.get('cidade'))==_chave(cidade) and u.get('uf')==uf]
            return nome,'municipal',None if len(vale)==len(unidades) else cidade
    return None,None,None


def _dia(dia,rotulo,unidades,excecoes):
    """Um item de `dias`. `excecoes`: {id da unidade: {'fechado','faixas','motivo'}} só deste dia."""
    feriado,tipo,so_em=_feriado(dia,unidades)
    partes=[]
    facultativo=facultativos(dia.year).get(dia)
    if facultativo and not feriado: partes.append(facultativo)
    if so_em: partes.append(f'feriado só em {so_em}')
    do_dia=[(u,excecoes[u['id']]) for u in unidades if u['id'] in excecoes]
    def repete(e):
        # Unidade fechada no feriado, sem motivo próprio: a exceção só repete o que o feriado já diz.
        motivo=_chave(e['motivo'])
        return bool(feriado) and e['fechado'] and (not motivo or motivo==_chave(feriado) or motivo in ('feriado','feriado '+_chave(feriado)))
    do_dia=[(u,e) for u,e in do_dia if not repete(e)]
    def frase(nome,e,plural=False):
        if e['fechado']:
            motivo=(e['motivo'] or '').strip()
            return (f'{nome} fechadas' if plural else f'{nome} fechado')+(f' ({motivo})' if motivo else '')
        return f'{nome} {"abrem" if plural else "abre"} {_faixas(e["faixas"])}'
    iguais=len(do_dia)==len(unidades)>0 and len({(e['fechado'],json.dumps(e['faixas']),_chave(e['motivo'])) for _,e in do_dia})==1
    if iguais and do_dia[0][1]['fechado'] and not feriado and (do_dia[0][1]['motivo'] or '').strip():
        # Todas as unidades fechadas pelo mesmo motivo, sem feriado oficial: é o "feriado da empresa".
        feriado,tipo=do_dia[0][1]['motivo'].strip()[:80],'empresa'
    elif iguais and len(unidades)>1: partes.append(frase('Todas as unidades',do_dia[0][1],plural=True))
    else: partes+=[frase(u['nome'],e) for u,e in do_dia]
    observacao='; '.join(partes) or None
    if observacao and len(observacao)>MAX_OBSERVACAO: observacao=observacao[:MAX_OBSERVACAO-1].rstrip()+'…'
    return {'data':dia.isoformat(),'dia_semana':DIAS[dia.weekday()],'rotulo':rotulo,'feriado':feriado,'tipo_feriado':tipo,'observacao':observacao}


def montar(hoje,fuso,unidades=(),excecoes=()):
    """Calendário a partir de `hoje` (data no fuso da empresa). `unidades`: [{'id','nome','cidade','uf'}] ativas;
    `excecoes`: [{'unidade_id','data','fechado','faixas','motivo'}]."""
    unidades=[dict(u) for u in unidades];por_dia={}
    for e in excecoes:
        faixas=e['faixas'];faixas=json.loads(faixas) if isinstance(faixas,str) else (faixas or [])
        d=e['data'] if isinstance(e['data'],date) else date.fromisoformat(str(e['data']))
        # Exceção sem faixas é dia fechado, marcado ou não.
        por_dia.setdefault(d,{})[e['unidade_id']]={'fechado':bool(e['fechado']) or not faixas,'faixas':[list(f) for f in faixas],'motivo':e['motivo']}
    dias=[]
    for n in range(DIAS_A_FRENTE+1):
        dia=hoje+timedelta(days=n)
        dias.append(_dia(dia,'hoje' if n==0 else 'amanhã' if n==1 else None,unidades,por_dia.get(dia,{})))
    return {'fuso':fuso,'dias':dias}


def simples(hoje,fuso):
    """Só as datas e os dias da semana: o que sai quando alguma fonte falha. O turno nunca para por causa do calendário."""
    return {'fuso':fuso,'dias':[{'data':(hoje+timedelta(days=n)).isoformat(),'dia_semana':DIAS[(hoje+timedelta(days=n)).weekday()],
                                 'rotulo':'hoje' if n==0 else 'amanhã' if n==1 else None,'feriado':None,'tipo_feriado':None,'observacao':None}
                                for n in range(DIAS_A_FRENTE+1)]}


async def do_pacote(db,tenant,fuso,agora=None):
    """Campo `calendario` do pacote ao agente, no fuso da empresa (às 00:05 "hoje" já é o dia novo)."""
    hoje=(agora or datetime.now(timezone.utc)).astimezone(ZoneInfo(fuso)).date()
    try:
        unidades=await db.fetch('SELECT id,nome,cidade,uf FROM tenant_unidades WHERE tenant_id=$1 AND ativo ORDER BY nome,id',tenant)
        excecoes=await db.fetch('''SELECT e.unidade_id,e.data,e.fechado,e.faixas,e.motivo FROM unidade_excecoes e
            JOIN tenant_unidades u ON u.tenant_id=e.tenant_id AND u.id=e.unidade_id AND u.ativo
            WHERE e.tenant_id=$1 AND e.data>=$2 AND e.data<=$3''',tenant,hoje,hoje+timedelta(days=DIAS_A_FRENTE))
        return montar(hoje,fuso,unidades,excecoes)
    except Exception as erro:
        log.warning('Calendário montado só com as datas (%s)',type(erro).__name__)
        return simples(hoje,fuso)
