"""Horário de funcionamento: uma função só para "aberta agora? próxima abertura?".

Usada pelo pacote ao agente, pelo painel e (nos próximos blocos) pelo carrinho, pelo pipeline
e pela Skill de unidades. Grade semanal com vários turnos por dia, turno que cruza a meia-noite,
exceções por data, pausa manual e fuso da unidade."""
from datetime import date,datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
import json
import re
DIAS=('seg','ter','qua','qui','sex','sab','dom')
MAX_TURNOS=12
# Até onde procurar a próxima abertura (feriados emendados, unidade que abre uma vez por semana).
DIAS_A_FRENTE=35


def _faixas(faixas):
    if not isinstance(faixas,list) or len(faixas)>MAX_TURNOS: raise ValueError('Faixas inválidas')
    for faixa in faixas:
        if not isinstance(faixa,list) or len(faixa)!=2: raise ValueError('Faixa inválida')
        if any(not isinstance(x,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',x) for x in faixa) or faixa[0]==faixa[1]: raise ValueError('Hora inválida')
    return faixas


def validar(horarios):
    if not isinstance(horarios,dict) or set(horarios)-set(DIAS): raise ValueError('Horários inválidos')
    for faixas in horarios.values(): _faixas(faixas)
    return horarios


def _minutos(h): return int(h[:2])*60+int(h[3:])


def validar_sem_sobreposicao(horarios):
    """Além do formato: dois turnos não podem se sobrepor, nem um turno que cruza a meia-noite
    invadir o primeiro turno do dia seguinte."""
    validar(horarios)
    semana=7*1440;intervalos=[]
    for i,dia in enumerate(DIAS):
        for ini,fim in horarios.get(dia,[]):
            a=i*1440+_minutos(ini);b=i*1440+_minutos(fim)+(1440 if fim<ini else 0)
            intervalos.append((a,b))
    intervalos.sort()
    for (a1,b1),(a2,b2) in zip(intervalos,intervalos[1:]):
        if a2<b1: raise ValueError('Turnos sobrepostos')
    # O último turno do domingo pode cruzar para a segunda.
    if intervalos and intervalos[-1][1]>semana and intervalos[0][0]<intervalos[-1][1]-semana: raise ValueError('Turnos sobrepostos')
    return horarios


def validar_faixas_do_dia(faixas):
    _faixas(faixas)
    validar_sem_sobreposicao({'seg':faixas})
    return faixas


def _excecoes_por_data(excecoes):
    """Aceita linhas do banco ou dicionários: {data: None (fechado) | [faixas]}."""
    por={}
    for e in excecoes or []:
        d=e['data'] if isinstance(e['data'],date) else date.fromisoformat(str(e['data']))
        faixas=e['faixas'];faixas=json.loads(faixas) if isinstance(faixas,str) else faixas
        por[d]=None if e['fechado'] else list(faixas)
    return por


def faixas_do_dia(horarios,por_data,dia):
    """Faixas que valem numa data: a exceção substitui a grade daquele dia. Devolve (faixas, origem)."""
    if dia in por_data: return (por_data[dia] or []),'excecao'
    return horarios.get(DIAS[dia.weekday()],[]),'grade'


def _intervalos(horarios,por_data,z,de,ate):
    saida=[]
    d=de
    while d<=ate:
        for ini,fim in faixas_do_dia(horarios,por_data,d)[0]:
            a=datetime.combine(d,time.fromisoformat(ini),z)
            # Turno com fim menor que o início cruza a meia-noite e pertence ao dia em que começa.
            b=datetime.combine(d+(timedelta(days=1) if fim<ini else timedelta()),time.fromisoformat(fim),z)
            saida.append((a,b))
        d+=timedelta(days=1)
    return sorted(saida)


def situacao(horarios,fuso,agora=None,excecoes=None,pausa_ate=None,pausa_indefinida=False,ativo=True):
    """Situação da unidade agora.

    aberta_agora: True, False ou None (sem horário cadastrado = não informado).
    motivo: None (aberta) | 'inativa' | 'pausa' | 'excecao' | 'horario' | 'sem_horario'.
    fecha_as / proxima_abertura / pausada_ate: ISO no fuso da unidade, ou None."""
    validar(horarios);z=ZoneInfo(fuso)
    agora=(agora or datetime.now(timezone.utc)).astimezone(z)
    iso=lambda d: d.astimezone(z).isoformat(timespec='minutes') if d else None
    base={'aberta_agora':False,'motivo':None,'fecha_as':None,'proxima_abertura':None,'pausada_ate':None}
    if not ativo: return {**base,'motivo':'inativa'}
    por_data=_excecoes_por_data(excecoes)
    hoje=agora.date()
    intervalos=_intervalos(horarios,por_data,z,hoje-timedelta(days=1),hoje+timedelta(days=DIAS_A_FRENTE))
    def primeira_a_partir(instante):
        """Primeiro instante aberto em ou depois de `instante`."""
        for a,b in intervalos:
            if a<=instante<b: return instante
            if a>instante: return a
        return None
    if pausa_indefinida: return {**base,'motivo':'pausa'}
    if pausa_ate and pausa_ate.astimezone(z)>agora:
        return {**base,'motivo':'pausa','pausada_ate':iso(pausa_ate),'proxima_abertura':iso(primeira_a_partir(pausa_ate.astimezone(z)))}
    sem_grade=not any(horarios.get(d) for d in DIAS) and not any(por_data.values())
    if sem_grade and hoje not in por_data:
        # Nada cadastrado: não informado. Quem consome trata como "a unidade confirma".
        return {**base,'aberta_agora':None,'motivo':'sem_horario'}
    for a,b in intervalos:
        if a<=agora<b:
            # Turnos emendados (um termina quando o outro começa) contam como um período só.
            fim=b
            for a2,b2 in intervalos:
                if a2==fim: fim=b2
            return {**base,'aberta_agora':True,'fecha_as':iso(fim)}
    motivo='excecao' if hoje in por_data else 'horario'
    return {**base,'motivo':motivo,'proxima_abertura':iso(primeira_a_partir(agora))}


def estado(horarios,fuso,agora=None):
    """Forma antiga (só grade semanal): mantida para quem já usa."""
    s=situacao(horarios,fuso,agora)
    return {'aberta_agora':bool(s['aberta_agora']),'proxima_abertura':s['proxima_abertura'] and datetime.fromisoformat(s['proxima_abertura']).isoformat()}


def agenda(horarios,fuso,excecoes=None,dias=7,agora=None):
    """Prévia dia a dia (grade + exceções), para o painel conferir."""
    validar(horarios);z=ZoneInfo(fuso);hoje=(agora or datetime.now(timezone.utc)).astimezone(z).date()
    por_data=_excecoes_por_data(excecoes);motivos={(e['data'] if isinstance(e['data'],date) else date.fromisoformat(str(e['data']))):e.get('motivo') for e in (dict(x) for x in (excecoes or []))}
    saida=[]
    for i in range(dias):
        d=hoje+timedelta(days=i);faixas,origem=faixas_do_dia(horarios,por_data,d)
        saida.append({'data':d.isoformat(),'dia':DIAS[d.weekday()],'origem':origem,'fechado':not faixas,'faixas':[list(f) for f in faixas],'motivo':motivos.get(d)})
    return saida


def resumo(s,fuso,agora=None):
    """Situação da unidade em poucas palavras, para a descrição de uma opção de lista (B3):
    "Aberta até 22h30", "Fechada · abre amanhã 12h", "Pedidos pausados"."""
    z=ZoneInfo(fuso);agora=(agora or datetime.now(z)).astimezone(z)
    hora=lambda d: f'{d.hour}h' if d.minute==0 else f'{d.hour}h{d.minute:02d}'
    def quando(iso):
        d=datetime.fromisoformat(iso).astimezone(z);dias=(d.date()-agora.date()).days
        return ('' if dias==0 else 'amanhã ' if dias==1 else d.strftime('%d/%m '))+hora(d)
    if s['motivo']=='inativa': return 'Indisponível'
    if s['motivo']=='pausa': return 'Pedidos pausados'+(f' · volta {quando(s["proxima_abertura"])}' if s.get('proxima_abertura') else '')
    if s['aberta_agora'] is True: return 'Aberta até '+quando(s['fecha_as']) if s.get('fecha_as') else 'Aberta'
    if s['aberta_agora'] is None: return 'Horário a confirmar'
    return 'Fechada'+(f' · abre {quando(s["proxima_abertura"])}' if s.get('proxima_abertura') else '')


async def resumos_das_unidades(db,tenant,codigos):
    """{codigo: resumo} das unidades ativas da empresa entre os códigos pedidos."""
    if not codigos: return {}
    fuso=await db.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant)
    saida={}
    for u in await db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND ativo AND codigo=ANY($2::text[])',tenant,list(codigos)):
        saida[u['codigo']]=resumo(await situacao_da_unidade(db,tenant,u,fuso),u['fuso'] or fuso)
    return saida


async def excecoes_da_unidade(db,tenant,unidade_id,de=None,ate=None):
    return await db.fetch('''SELECT id,data,fechado,faixas,motivo FROM unidade_excecoes WHERE tenant_id=$1 AND unidade_id=$2
        AND ($3::date IS NULL OR data>=$3) AND ($4::date IS NULL OR data<=$4) ORDER BY data''',tenant,unidade_id,de,ate)


async def situacao_da_unidade(db,tenant,unidade,fuso_empresa,agora=None):
    """Situação de uma linha de tenant_unidades (com exceções e pausa), no fuso da unidade."""
    horarios=json.loads(unidade['horarios']) if isinstance(unidade['horarios'],str) else unidade['horarios']
    hoje=(agora or datetime.now(timezone.utc)).date()
    exc=await excecoes_da_unidade(db,tenant,unidade['id'],hoje-timedelta(days=2),hoje+timedelta(days=DIAS_A_FRENTE+1))
    return situacao(horarios,unidade['fuso'] or fuso_empresa,agora,exc,unidade['pausa_ate'],unidade['pausa_indefinida'],unidade['ativo'])


# Dados da unidade que vão ao agente. Nulo = não informado: o agente não afirma nada sobre o campo
# e diz que a unidade confirma no retorno do pré-pedido. whatsapp_pedidos é interno e não vai.
PARA_O_AGENTE=('codigo','endereco','telefone','formas_pagamento','taxa_entrega','pedido_minimo','area_entrega','aceita_retirada','regras')


async def itens_no_cardapio(db,tenant):
    """{código da unidade: quantos itens podem ser pedidos} — ativo, com preço e que não seja só do salão (a conta do modo `categorias` da busca)."""
    rows=await db.fetch("""SELECT unidade,count(*) n FROM catalogo_itens WHERE tenant_id=$1 AND ativo AND preco IS NOT NULL AND delivery<>'nao'
        AND tipo IN ('item','adicional') GROUP BY 1""",tenant)
    return {r['unidade']:r['n'] for r in rows}


async def pacote(db,tenant,fuso,agora=None):
    rows=await db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND ativo ORDER BY id',tenant)
    saida=[];cardapio=await itens_no_cardapio(db,tenant)
    for r in rows:
        horarios=json.loads(r['horarios']) if isinstance(r['horarios'],str) else r['horarios']
        dados={k:(float(r[k]) if k in ('taxa_entrega','pedido_minimo') and r[k] is not None else r[k]) for k in PARA_O_AGENTE}
        s=await situacao_da_unidade(db,tenant,r,fuso,agora)
        saida.append({'id':r['id'],'nome':r['nome'],'aberta_agora':s['aberta_agora'],'proxima_abertura':s['proxima_abertura'],
                      'motivo_fechada':s['motivo'],'fecha_as':s['fecha_as'],'pausada_ate':s['pausada_ate'],'pausa_motivo':r['pausa_motivo'] if s['motivo']=='pausa' else None,
                      **dados,'horarios':horarios,
                      # DISP1: false = nenhum item desta unidade pode ser pedido; o agente não oferece cardápio dela.
                      'tem_cardapio':cardapio.get(r['codigo'],0)>0,'itens_no_cardapio':cardapio.get(r['codigo'],0)})
    return saida
