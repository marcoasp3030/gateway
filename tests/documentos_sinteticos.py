"""Documentos sintéticos válidos (e inválidos) para os testes do ARQ1-a3: boleto bancário, arrecadação, chave de acesso da NF-e
(inclusive com CNPJ alfanumérico), CPF, CNPJ, E2E do PIX e BR Code. Nenhum dado de cliente real."""
from datetime import date
from nucleo import documentos_regras as R


def cnpj(base12):
    def dv(b):
        pesos=[(i%8)+2 for i in range(len(b))][::-1]
        r=sum((ord(c)-48)*p for c,p in zip(b,pesos))%11
        return '0' if r<2 else str(11-r)
    d1=dv(base12);return base12+d1+dv(base12+d1)


def cpf(base9):
    c=base9
    for n in (9,10):
        s=sum(int(c[i])*(n+1-i) for i in range(n));c+=str((s*10)%11%10)
    return c


def boleto(banco='341',vencimento=date(2026,10,25),valor_centavos=15230,campo_livre='1790010104351004791020150008'[:25]):
    """(linha digitável de 47, código de barras de 44). Fator pela base nova (22/02/2025 = 1000)."""
    fator=1000+(vencimento-R.REINICIO).days if vencimento>=R.REINICIO else (vencimento-R.BASE_ANTIGA).days
    sem_dv=banco+'9'+f'{fator:04d}'+f'{valor_centavos:010d}'+campo_livre
    dv=R.mod11_boleto(sem_dv)
    barras=sem_dv[:4]+str(dv)+sem_dv[4:]
    c=barras[19:44];l1=barras[0:4]+c[0:5];l2=c[5:15];l3=c[15:25]
    linha=l1+str(R.mod10(l1))+l2+str(R.mod10(l2))+l3+str(R.mod10(l3))+barras[4]+barras[5:19]
    return linha,barras


def arrecadacao(segmento='3',ident='6',valor_centavos=8945,resto='00012345678901234567890123456'):
    """(linha de 48, código de barras de 44) de concessionária."""
    f=R.mod10 if ident in '67' else R.mod11_arrecadacao
    sem_dv='8'+segmento+ident+f'{valor_centavos:011d}'+resto
    barras=sem_dv[:3]+str(f(sem_dv))+sem_dv[3:]
    blocos=[barras[i*11:i*11+11] for i in range(4)]
    return ''.join(b+str(f(b)) for b in blocos),barras


def chave(cuf='35',aamm='2610',cnpj_emitente=None,modelo='55',serie=1,numero=1234,tp='1',codigo='00012345'):
    c=cuf+aamm+(cnpj_emitente or cnpj('112223330001'))+modelo+f'{serie:03d}'+f'{numero:09d}'+tp+codigo
    return c+str(R.mod11_ascii(c))


def e2e(ispb='60701190',quando='202610211330',seq='abcDEF12345'):
    return 'E'+ispb+quando+seq


def brcode(chave_pix='cobranca@exemplo.com.br',valor='152.30',nome='EMPRESA EXEMPLO',cidade='SAO PAULO',txid='PEDIDO77'):
    def campo(tag,v): return f'{tag}{len(v):02d}{v}'
    conta=campo('00','br.gov.bcb.pix')+campo('01',chave_pix)
    s=campo('00','01')+campo('26',conta)+campo('52','0000')+campo('53','986')+campo('54',valor)+campo('58','BR')+campo('59',nome)+campo('60',cidade)+campo('62',campo('05',txid))+'6304'
    return s+R.crc16(s)
