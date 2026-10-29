"""TEL1: telefone da lista do piloto e dos contatos de alerta no formato do WhatsApp (55 + DDD + número, só dígitos).

O piloto compara o número exatamente como chega da uazapi; "11912345678" gravado sem o 55 nunca casaria."""
import re

MENSAGEM='Use DDD + número, ex.: 11912345678'
SEPARADORES=re.compile(r'[\s\-()+]')
# Canal api (ARQ1): identificador com letras ("teste-marco-01") vale na lista do piloto como está.
IDENTIFICADOR=re.compile(r'[A-Za-z0-9_@+.-]{3,100}')


def telefone_br(valor):
    """Só dígitos com 55 na frente, ou None fora da regra: 10 ou 11 dígitos ganham o 55; 12 ou 13 começando com 55 ficam."""
    if not isinstance(valor,str): return None
    d=SEPARADORES.sub('',valor)
    if not d.isascii() or not d.isdigit(): return None
    if len(d) in (10,11): return '55'+d
    if len(d) in (12,13) and d.startswith('55'): return d
    return None


def contato_do_piloto(valor):
    """Telefone normalizado; identificador com letras como está; None fora da regra."""
    if isinstance(valor,str) and any(c.isalpha() for c in valor):
        return valor if IDENTIFICADOR.fullmatch(valor) else None
    return telefone_br(valor)


def lista_do_piloto(valores):
    """Lista normalizada, sem repetir (mantém a ordem), ou None se algum item estiver fora da regra."""
    saida=[]
    for v in valores:
        n=contato_do_piloto(v)
        if n is None: return None
        if n not in saida: saida.append(n)
    return saida
