"""Disponibilidade do item por dia da semana, feriado e horário (DISP1, docs/ESPEC_DISP1.md).

Nulo = sem restrição. A conferência é feita contra o dia e a hora DO PEDIDO (o horário agendado do carrinho; sem
agendamento, agora), no fuso da unidade. Feriado é o do calendário do pacote (CAL1) para a unidade do item."""
import json
import logging
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
from nucleo import horarios as H
from nucleo import calendario

log=logging.getLogger('gateway.catalogo')
DIAS=H.DIAS
NOMES={'seg':'segunda','ter':'terça','qua':'quarta','qui':'quinta','sex':'sexta','sab':'sábado','dom':'domingo'}
CHAVES=('dias_semana','exceto_feriado','horario')
MOTIVOS=('dia_da_semana','feriado','horario')


def ler(valor):
    """Valor da coluna (texto JSON, dicionário ou nulo) → dicionário ou None."""
    if valor is None: return None
    d=json.loads(valor) if isinstance(valor,str) else dict(valor)
    return d or None


def validar(valor):
    """Normaliza o que veio do painel ou do CSV. None ou vazio = sem restrição. ValueError se o formato não serve."""
    if valor is None: return None
    if not isinstance(valor,dict) or set(valor)-set(CHAVES): raise ValueError('disponibilidade inválida')
    saida={}
    dias=valor.get('dias_semana')
    if dias:
        if not isinstance(dias,list) or any(d not in DIAS for d in dias): raise ValueError('dias_semana inválido')
        dias=[d for d in DIAS if d in dias]
        # Os sete dias = sem restrição de dia.
        if len(dias)<7: saida['dias_semana']=dias
    if valor.get('exceto_feriado') not in (None,True,False): raise ValueError('exceto_feriado inválido')
    if valor.get('exceto_feriado'): saida['exceto_feriado']=True
    faixas=valor.get('horario')
    if faixas:
        try: H.validar_faixas_do_dia(faixas)
        except ValueError: raise ValueError('horario inválido') from None
        saida['horario']=sorted([list(f) for f in faixas])
    return saida or None


def _hora(h):
    hora,minuto=int(h[:2]),int(h[3:5])
    return f'{hora}h' if minuto==0 else f'{hora}h{minuto:02d}'


def resumo(d):
    """Em palavras, para o agente dizer ao cliente: "segunda a sexta, 11h30–15h, exceto feriados"."""
    if not d: return None
    partes=[]
    dias=d.get('dias_semana') or []
    if dias:
        indices=[DIAS.index(x) for x in dias]
        seguidos=len(dias)>2 and indices==list(range(indices[0],indices[0]+len(dias)))
        if seguidos: partes.append(f'{NOMES[dias[0]]} a {NOMES[dias[-1]]}')
        elif len(dias)==1: partes.append(NOMES[dias[0]])
        else: partes.append(', '.join(NOMES[x] for x in dias[:-1])+' e '+NOMES[dias[-1]])
    if d.get('horario'): partes.append(' e '.join(f'{_hora(a)}–{_hora(b)}' for a,b in d['horario']))
    if d.get('exceto_feriado'): partes.append('exceto feriados')
    return ', '.join(partes) or None


def publica(d):
    """Como a disponibilidade vai para o agente e para o painel: as chaves cadastradas e o resumo em palavras."""
    if not d: return None
    return {'dias_semana':d.get('dias_semana') or [],'exceto_feriado':bool(d.get('exceto_feriado')),'horario':d.get('horario') or [],'resumo':resumo(d)}


def avaliar(d,quando,feriado=None):
    """(True, None) ou (False, 'dia_da_semana'|'feriado'|'horario'). `quando`: data e hora do pedido no fuso da unidade.
    `feriado(dia)` devolve o nome do feriado daquela data, ou None."""
    if not d: return True,None
    dias=d.get('dias_semana') or list(DIAS);faixas=d.get('horario') or []
    feriado=feriado or (lambda dia: None)
    def do_dia(dia):
        if DIAS[dia.weekday()] not in dias: return 'dia_da_semana'
        if d.get('exceto_feriado') and feriado(dia): return 'feriado'
        return None
    hoje=quando.date();agora=quando.time().replace(second=0,microsecond=0)
    if not faixas:
        motivo=do_dia(hoje);return motivo is None,motivo
    motivos=[]
    for ini,fim in faixas:
        a,b=time.fromisoformat(ini),time.fromisoformat(fim)
        if b>a:
            if a<=agora<b: motivos.append(do_dia(hoje))
        else:
            # Faixa que cruza a meia-noite pertence ao dia em que começa.
            if agora>=a: motivos.append(do_dia(hoje))
            elif agora<b: motivos.append(do_dia(hoje-timedelta(days=1)))
    if not motivos:
        # Fora de todas as faixas: se o dia também não serve, o motivo que importa é o do dia.
        return False,do_dia(hoje) or 'horario'
    if None in motivos: return True,None
    return False,motivos[0]


class Conferencia:
    """Confere itens de uma conversa contra o dia do pedido. Guarda o que já consultou (unidade, feriado do dia)."""
    def __init__(self,db,tenant,quando_utc,fuso_empresa):
        self.db,self.tenant,self.quando,self.fuso_empresa=db,tenant,quando_utc,fuso_empresa
        self._unidades={};self._feriados={}

    async def _unidade(self,codigo):
        if codigo not in self._unidades:
            self._unidades[codigo]=await self.db.fetchrow('SELECT id,nome,codigo,cidade,uf,fuso FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2',self.tenant,codigo)
        return self._unidades[codigo]

    async def _feriado(self,codigo,dia):
        chave=(codigo,dia)
        if chave not in self._feriados:
            try: self._feriados[chave]=await calendario.feriado_da_unidade(self.db,self.tenant,await self._unidade(codigo),dia)
            except Exception as erro:
                # Sem a fonte de feriados o item não é barrado por feriado (dia e horário continuam valendo).
                log.warning('Feriado não conferido para a disponibilidade (%s)',type(erro).__name__);self._feriados[chave]=None
        return self._feriados[chave]

    async def item(self,item):
        """{'disponivel','motivo','feriado','data_referencia','disponibilidade'} de uma linha de catalogo_itens."""
        d=ler(item['disponibilidade']);u=await self._unidade(item['unidade'])
        fuso=(u['fuso'] if u and u['fuso'] else None) or self.fuso_empresa
        local=self.quando.astimezone(ZoneInfo(fuso));referencia=local.isoformat(timespec='minutes')
        if not d: return {'disponivel':True,'motivo':None,'feriado':None,'data_referencia':referencia,'disponibilidade':None}
        nomes={}
        if d.get('exceto_feriado'):
            for dia in (local.date(),local.date()-timedelta(days=1)): nomes[dia]=await self._feriado(item['unidade'],dia)
        ok,motivo=avaliar(d,local,nomes.get)
        return {'disponivel':ok,'motivo':motivo,'feriado':nomes.get(local.date()) if motivo=='feriado' else None,
                'data_referencia':referencia,'disponibilidade':publica(d)}


async def da_conversa(db,tenant,conversa):
    """Conferência para o dia do pedido desta conversa: o horário agendado do carrinho aberto; sem ele, agora."""
    from modulos.pedidos import agendamento
    fuso=await db.fetchval('SELECT timezone FROM tenants WHERE id=$1',tenant)
    c=await db.fetchrow("SELECT * FROM carrinhos WHERE tenant_id=$1 AND conversation_id=$2 AND status='aberto'",tenant,conversa)
    quando=None
    if c:
        u=await db.fetchrow('SELECT fuso FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2',tenant,c['unidade']) if c['unidade'] else None
        quando=agendamento.do_carrinho(c,(u['fuso'] if u and u['fuso'] else None) or fuso)
        # Horário que já passou não é referência: o cliente vai escolher outro.
        if agendamento.vencido(quando): quando=None
    return Conferencia(db,tenant,quando or agendamento.agora_utc(),fuso)
