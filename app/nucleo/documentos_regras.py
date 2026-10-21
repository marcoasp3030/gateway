"""ARQ1-a3: campos por tipo de documento, por regra e sem IA (docs/ESPEC_ARQ1.md, seções 13.3 e 15).

Entrada: o que o leitor devolveu (texto por página, tabelas, XML da NF-e). Saída: tipo do documento, campos com confiança e avisos.
  validado       — dígito verificador ou estrutura conferidos (linha digitável, código de barras, chave de acesso, CPF/CNPJ,
                   E2E do PIX, CRC do BR Code);
  lido_do_texto  — achado no texto digital ou no XML, sem como conferir;
  a_conferir     — divergência, heurística, NFS-e, item de tabela que não fecha.
Fontes conferidas em 21/10/2026: NT Conjunta 2025.001 (CNPJ alfanumérico: DV da chave com ASCII − 48 nos 43 caracteres e módulo 11;
chave [0-9]{6}[A-Z0-9]{12}[0-9]{26}); fator de vencimento do boleto reiniciado em 1000 em 22/02/2025 (Febraban);
E2E do PIX: E + ISPB [0-9A-Z]{8} + AAAAMMDDHHMM (UTC) + 11 alfanuméricos (identificadores do mesmo padrão no Manual de Padrões
para Iniciação do Pix 2.10.0; o formato do E2E em si é o divulgado pelos PSPs — conferido só o formato).
Comprovante lido NUNCA vira pagamento confirmado: `verificacao_pagamento` fica `nao_verificado`."""
import re
import unicodedata
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation

# ---------------- utilidades ----------------

def _norm(t):
    sem=''.join(c for c in unicodedata.normalize('NFKD',str(t or '')) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+',' ',sem.lower()).strip()


def _campo(valor,confianca,fonte,pagina=None,**extra):
    return {'valor':valor,'confianca':confianca,'fonte':fonte,**({'pagina':pagina} if pagina else {}),**extra}


def _dinheiro(texto):
    """'R$ 1.234,56' / '1234.56' → '1234.56' (texto com 2 casas), ou None."""
    t=str(texto or '').replace('R$','').replace(' ','').strip()
    if not t: return None
    if ',' in t: t=t.replace('.','').replace(',','.')
    try: d=Decimal(t)
    except InvalidOperation: return None
    return f'{d.quantize(Decimal("0.01"))}' if d.is_finite() and d>=0 else None


VALOR=r'(?:R\$\s*)?(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d+\.\d{2})'
DATA=r'(\d{2})/(\d{2})/(\d{4})'


def _data(d,m,a):
    try: return date(int(a),int(m),int(d)).isoformat()
    except ValueError: return None


# ---------------- módulo 10 e 11 ----------------

def mod10(digitos):
    soma=0
    for i,c in enumerate(reversed(digitos)):
        p=int(c)*(2 if i%2==0 else 1);soma+=p//10+p%10
    return (10-soma%10)%10


def mod11_boleto(digitos):
    """DV geral do código de barras do boleto bancário: pesos 2–9 da direita; 0, 10 ou 11 viram 1."""
    soma=sum(int(c)*(2+i%8) for i,c in enumerate(reversed(digitos)))
    dv=11-soma%11
    return 1 if dv in (0,10,11) else dv


def mod11_arrecadacao(digitos):
    """Arrecadação (concessionária) com módulo 11: resto 0 ou 1 → 0; senão 11 − resto."""
    soma=sum(int(c)*(2+i%8) for i,c in enumerate(reversed(digitos)))
    r=soma%11
    return 0 if r in (0,1) else 11-r


def _valor_ascii(c): return ord(c)-48


def mod11_ascii(caracteres):
    """DV da chave de acesso e do CNPJ (NT 2025.001): cada caractere vale ASCII − 48; pesos 2–9 da direita; resto 0 ou 1 → 0."""
    soma=sum(_valor_ascii(c)*(2+i%8) for i,c in enumerate(reversed(caracteres)))
    r=soma%11
    return 0 if r in (0,1) else 11-r


def cnpj_valido(cnpj):
    """CNPJ numérico ou alfanumérico (12 posições [0-9A-Z] + 2 DV numéricos; NT 2025.001)."""
    c=re.sub(r'[^0-9A-Z]','',str(cnpj or '').upper())
    if not re.fullmatch(r'[0-9A-Z]{12}[0-9]{2}',c) or len(set(c))==1: return False
    def dv(base):
        pesos=[(i%8)+2 for i in range(len(base))][::-1]
        r=sum(_valor_ascii(ch)*p for ch,p in zip(base,pesos))%11
        return 0 if r<2 else 11-r
    return dv(c[:12])==int(c[12]) and dv(c[:13])==int(c[13])


def cpf_valido(cpf):
    c=re.sub(r'\D','',str(cpf or ''))
    if len(c)!=11 or len(set(c))==1: return False
    for n in (9,10):
        s=sum(int(c[i])*(n+1-i) for i in range(n));d=(s*10)%11%10
        if d!=int(c[n]): return False
    return True


def _cnpj_fmt(c): return f'{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}'
def _cpf_fmt(c): return f'{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}'


# ---------------- tabelas ----------------

BANCOS={'001':'Banco do Brasil','003':'Banco da Amazônia','004':'Banco do Nordeste','021':'Banestes','033':'Santander','037':'Banpará','041':'Banrisul',
        '047':'Banese','070':'BRB','077':'Banco Inter','085':'Ailos','104':'Caixa Econômica Federal','136':'Unicred','197':'Stone','208':'BTG Pactual',
        '212':'Banco Original','237':'Bradesco','260':'Nu Pagamentos (Nubank)','290':'PagBank','323':'Mercado Pago','336':'C6 Bank','341':'Itaú Unibanco',
        '380':'PicPay','389':'Banco Mercantil','403':'Cora','422':'Safra','623':'Banco Pan','655':'Banco Votorantim','707':'Daycoval','745':'Citibank',
        '748':'Sicredi','756':'Sicoob'}
ISPBS={'00000000':'Banco do Brasil','00360305':'Caixa Econômica Federal','60701190':'Itaú Unibanco','60746948':'Bradesco','90400888':'Santander',
       '18236120':'Nu Pagamentos (Nubank)','00416968':'Banco Inter','31872495':'C6 Bank','08561701':'PagBank','10573521':'Mercado Pago',
       '22896431':'PicPay','30306294':'BTG Pactual','92702067':'Banrisul','01181521':'Sicredi','02038232':'Sicoob','58160789':'Safra'}
UFS={'11':'RO','12':'AC','13':'AM','14':'RR','15':'PA','16':'AP','17':'TO','21':'MA','22':'PI','23':'CE','24':'RN','25':'PB','26':'PE','27':'AL',
     '28':'SE','29':'BA','31':'MG','32':'ES','33':'RJ','35':'SP','41':'PR','42':'SC','43':'RS','50':'MS','51':'MT','52':'GO','53':'DF'}
MODELOS={'55':'NF-e','65':'NFC-e','57':'CT-e','58':'MDF-e','59':'CF-e SAT','67':'CT-e OS'}
SEGMENTOS={'1':'prefeitura','2':'saneamento','3':'energia elétrica e gás','4':'telecomunicações','5':'órgão governamental','6':'carnê ou outros',
           '7':'multa de trânsito','9':'uso exclusivo do banco'}


# ---------------- boleto bancário ----------------

BASE_ANTIGA=date(1997,10,7)
REINICIO=date(2025,2,22)


def vencimento_do_fator(fator,referencia):
    """Fator de 4 dígitos → data. Desde 22/02/2025 o fator voltou a 1000: um fator vale duas datas; vale a mais próxima da referência."""
    if fator==0: return None
    if fator<1000: return None
    antiga=BASE_ANTIGA+timedelta(days=fator)
    nova=REINICIO+timedelta(days=fator-1000)
    return min((antiga,nova),key=lambda d: abs((d-referencia).days))


def barras_da_linha(d):
    """Linha digitável de 47 dígitos → código de barras de 44."""
    return d[0:4]+d[32]+d[33:47]+d[4:9]+d[10:20]+d[21:31]


def conferir_boleto(d,referencia):
    """d: 47 dígitos (linha) ou 44 (código de barras). Dict com os campos, ou None se não for boleto bancário válido."""
    if len(d)==47:
        if d[0]=='8': return None
        if mod10(d[0:9])!=int(d[9]) or mod10(d[10:20])!=int(d[20]) or mod10(d[21:31])!=int(d[31]): return None
        barras=barras_da_linha(d);linha=d
    elif len(d)==44:
        if d[0]=='8': return None
        barras=d;linha=None
    else: return None
    if mod11_boleto(barras[:4]+barras[5:])!=int(barras[4]): return None
    fator=int(barras[5:9]);valor=int(barras[9:19])
    if linha is None:
        c=barras[19:44];l=barras[0:4]+c[0:5];l2=c[5:15];l3=c[15:25]
        linha=l+str(mod10(l))+l2+str(mod10(l2))+l3+str(mod10(l3))+barras[4]+barras[5:19]
    venc=vencimento_do_fator(fator,referencia)
    return {'linha':linha,'barras':barras,'banco':barras[0:3],'moeda':barras[3],'fator':fator,'vencimento':venc.isoformat() if venc else None,
            'valor':f'{Decimal(valor)/100:.2f}' if valor else None}


def linha_formatada(l):
    return f'{l[0:5]}.{l[5:10]} {l[10:15]}.{l[15:21]} {l[21:26]}.{l[26:32]} {l[32]} {l[33:47]}'


# ---------------- arrecadação (concessionária) ----------------

def conferir_arrecadacao(d):
    """48 dígitos (4 blocos de 11 + DV) ou 44 (código de barras), começando com 8."""
    if len(d)==48:
        if d[0]!='8': return None
        blocos=[d[i*12:i*12+11] for i in range(4)];dvs=[d[i*12+11] for i in range(4)]
        barras=''.join(blocos)
    elif len(d)==44:
        if d[0]!='8': return None
        barras=d;blocos=[barras[i*11:i*11+11] for i in range(4)];dvs=None
    else: return None
    ident=barras[2]
    if ident in '67': f=mod10
    elif ident in '89': f=mod11_arrecadacao
    else: return None
    if f(barras[:3]+barras[4:])!=int(barras[3]): return None
    if dvs is not None and any(f(b)!=int(v) for b,v in zip(blocos,dvs)): return None
    linha=''.join(b+str(f(b)) for b in blocos)
    efetivo=ident in '68'
    return {'linha':linha,'barras':barras,'segmento':barras[1],'valor_efetivo':efetivo,'valor':f'{Decimal(int(barras[4:15]))/100:.2f}' if int(barras[4:15]) else None}


def arrecadacao_formatada(l): return ' '.join(f'{l[i*12:i*12+11]}-{l[i*12+11]}' for i in range(4))


# ---------------- NF-e: chave de acesso ----------------

CHAVE=re.compile(r'[0-9]{6}[A-Z0-9]{12}[0-9]{26}')


def conferir_chave(c):
    c=re.sub(r'[\s.]','',str(c or '').upper())
    if not CHAVE.fullmatch(c) or mod11_ascii(c[:43])!=int(c[43]): return None
    return {'chave':c,'uf':UFS.get(c[0:2]),'cuf':c[0:2],'ano_mes':f'20{c[2:4]}-{c[4:6]}','cnpj_emitente':c[6:20],'cnpj_valido':cnpj_valido(c[6:20]),
            'modelo':c[20:22],'modelo_nome':MODELOS.get(c[20:22]),'serie':str(int(c[22:25])),'numero':str(int(c[25:34])),'tipo_emissao':c[34],'codigo':c[35:43]}


def chave_formatada(c): return ' '.join(c[i:i+4] for i in range(0,44,4))


# ---------------- PIX ----------------

E2E=re.compile(r'\bE[0-9A-Z]{8}\d{12}[A-Za-z0-9]{11}\b')


def conferir_e2e(e,referencia=None):
    """Formato do E2E (32): E + ISPB + AAAAMMDDHHMM (UTC) + 11. Confere só o formato e a data; NUNCA confirma pagamento."""
    if not E2E.fullmatch(e or ''): return None
    try: quando=datetime.strptime(e[9:21],'%Y%m%d%H%M').replace(tzinfo=timezone.utc)
    except ValueError: return None
    if quando<datetime(2020,10,1,tzinfo=timezone.utc): return None
    if referencia and quando>referencia+timedelta(days=1): return None
    return {'e2e':e,'ispb':e[1:9],'instituicao':ISPBS.get(e[1:9]),'data_hora_utc':quando.isoformat()}


def crc16(dados):
    crc=0xFFFF
    for b in dados.encode('utf-8'):
        crc^=b<<8
        for _ in range(8): crc=((crc<<1)^0x1021) if crc&0x8000 else crc<<1
        crc&=0xFFFF
    return f'{crc:04X}'


def _tlv(s):
    saida={};i=0
    while i+4<=len(s):
        tag,n=s[i:i+2],s[i+2:i+4]
        if not n.isdigit(): return None
        n=int(n);saida[tag]=s[i+4:i+4+n];i+=4+n
    return saida if i==len(s) else None


BRCODE=re.compile(r'000201.{20,500}?6304[0-9A-Fa-f]{4}')


def conferir_brcode(s):
    """PIX copia e cola (EMV/BR Code): CRC16 conferido; chave, valor, nome, cidade e txid."""
    s=(s or '').strip()
    if len(s)<30 or s[-8:-4]!='6304' or crc16(s[:-4])!=s[-4:].upper(): return None
    t=_tlv(s)
    if t is None: return None
    conta=_tlv(t.get('26','')) or {}
    extra=_tlv(t.get('62','')) or {}
    return {'chave':conta.get('01'),'url':conta.get('25'),'valor':_dinheiro(t.get('54')),'nome':t.get('59'),'cidade':t.get('60'),'txid':extra.get('05')}


def tipo_de_chave(c):
    c=c.strip()
    if re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',c.lower()): return 'aleatoria'
    if '@' in c and re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',c): return 'email'
    d=re.sub(r'\D','',c)
    if len(d)==11 and cpf_valido(d) and not c.startswith('+'): return 'cpf'
    if len(d)==14 and cnpj_valido(d): return 'cnpj'
    if c.startswith('+') or len(d) in (10,11,12,13): return 'celular'
    return None


# ---------------- leitura do texto ----------------

def _paginas(lido):
    return [(p['pagina'],p.get('texto') or '') for p in (lido or {}).get('paginas',[])]


def _sem_acento(t):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(t or '')) if not unicodedata.combining(c))


def _fim_do_rotulo(linha,rotulo,no_inicio=False):
    """Posição logo depois do rótulo na linha (sem acento, sem diferença de maiúsculas), ou None."""
    padrao=r'\b'+r'\W+'.join(re.escape(p) for p in rotulo.split())+r'\b'
    m=re.match(r'\W*'+padrao,_sem_acento(linha),re.I) if no_inicio else re.search(padrao,_sem_acento(linha),re.I)
    return m.end() if m else None


def _rotulo(texto,rotulos,padrao,linhas_depois=2):
    """Valor depois de um rótulo ("Vencimento", "Valor do documento") na mesma linha ou nas próximas. Rótulos em ordem de preferência."""
    linhas=texto.split('\n')
    for r in rotulos:
        for i,l in enumerate(linhas):
            pos=_fim_do_rotulo(l,r)
            if pos is None: continue
            for trecho in [_sem_acento(l)[pos:]]+[_sem_acento(x) for x in linhas[i+1:i+1+linhas_depois]]:
                m=re.search(padrao,trecho)
                if m: return m
    return None


def _nome_apos(texto,rotulos):
    """Nome depois de um rótulo no começo da linha ("Beneficiário: Empresa X", "Para\nFulano"), sem CPF/CNPJ."""
    linhas=texto.split('\n')
    for r in rotulos:
        for i,l in enumerate(linhas):
            pos=_fim_do_rotulo(l,r,no_inicio=True)
            if pos is None: continue
            resto=l[pos:].strip(' :-–\t')
            if not resto and i+1<len(linhas): resto=linhas[i+1].strip()
            nome=re.sub(r'\s+',' ',re.sub(r'(?i)\b(CNPJ|CPF|CPF/CNPJ)\b.*$','',resto)).strip(' :-–')
            nome=re.sub(r'\s*\d{2,3}[.\d/-]{8,}.*$','',nome).strip(' :-–')
            if 2<=len(nome)<=120 and re.search(r'[A-Za-zÀ-ÿ]{2}',nome): return nome
    return None


def _documentos(texto):
    """CPF e CNPJ formatados no texto, com o DV conferido: [(tipo, número só com dígitos/letras, posição)]."""
    achados=[]
    for m in re.finditer(r'\b[0-9A-Z]{2}\.[0-9A-Z]{3}\.[0-9A-Z]{3}/[0-9A-Z]{4}-\d{2}\b',texto):
        c=re.sub(r'[^0-9A-Z]','',m.group(0))
        if cnpj_valido(c): achados.append(('cnpj',c,m.start()))
    for m in re.finditer(r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b',texto):
        c=re.sub(r'\D','',m.group(0))
        if cpf_valido(c): achados.append(('cpf',c,m.start()))
    return achados


def _doc_perto(texto,rotulos):
    """CPF/CNPJ do rótulo no começo da linha ("Pagador: … CPF …"): primeiro o da própria linha, senão o das 2 seguintes."""
    linhas=texto.split('\n')
    for r in rotulos:
        for i,l in enumerate(linhas):
            if _fim_do_rotulo(l,r,no_inicio=True) is None: continue
            for janela in (l,'\n'.join(linhas[i+1:i+3])):
                achados=sorted(_documentos(janela),key=lambda x: x[2])
                if achados: return achados[0][0],achados[0][1]
    return None


def _digitos_longos(texto):
    """Sequências de 44 a 48 dígitos no texto (linha digitável com pontos e espaços, código de barras, chave em grupos de 4)."""
    for m in re.finditer(r'\d[\d .\-]{42,75}\d',texto):
        d=re.sub(r'\D','',m.group(0))
        if 44<=len(d)<=48: yield d,m.start()
        # Linha seguida de outros números na mesma linha: tenta os tamanhos certos a partir do começo.
        elif len(d)>48:
            for n in (48,47,44): yield d[:n],m.start()


# ---------------- análise ----------------

def analisar(lido,recebido_em=None):
    """{'tipo_documento', 'campos', 'avisos', 'verificacao_pagamento'} a partir do resultado do leitor."""
    ref_dt=recebido_em or datetime.now(timezone.utc)
    ref=ref_dt.date()
    campos={};avisos=[];tipo=None
    paginas=_paginas(lido)
    texto='\n'.join(t for _,t in paginas)
    ntexto=_norm(texto)

    # NF-e/NFC-e pelo XML (preferido quando vier).
    nfe=(lido or {}).get('nfe')
    if nfe:
        info=conferir_chave(nfe.get('chave'))
        tipo={'55':'nfe','65':'nfce'}.get(nfe.get('modelo'),'nfe')
        if info:
            campos['chave_acesso']=_campo(info['chave'],'validado','xml')
            campos.update(_campos_da_chave(info,'xml'))
        elif nfe.get('chave'):
            campos['chave_acesso']=_campo(nfe['chave'],'a_conferir','xml');avisos.append('chave_de_acesso_com_dv_invalido')
        for k,v in (('emissao',nfe.get('emissao')),('emitente_nome',(nfe.get('emitente') or {}).get('nome')),
                    ('destinatario_nome',(nfe.get('destinatario') or {}).get('nome')),('valor_total',nfe.get('valor_total')),
                    ('protocolo',(nfe.get('protocolo') or {}).get('numero')),('situacao',(nfe.get('protocolo') or {}).get('motivo'))):
            if v: campos[k]=_campo(v,'lido_do_texto','xml')
        for papel in ('emitente','destinatario'):
            doc=((nfe.get(papel) or {}).get('documento') or '')
            if doc:
                ok=cnpj_valido(doc) if len(doc)==14 else cpf_valido(doc)
                campos[f'{papel}_documento']=_campo(_cnpj_fmt(doc) if len(doc)==14 else _cpf_fmt(doc),'validado' if ok else 'a_conferir','xml')
        return _fim(tipo,campos,avisos)

    # Códigos no texto: boleto bancário, arrecadação, chave de acesso.
    boleto=arrec=chave=None
    for d,pos in _digitos_longos(texto):
        pagina=_pagina_de(paginas,pos)
        if not boleto and len(d) in (47,44):
            b=conferir_boleto(d,ref)
            if b: boleto=(b,pagina);continue
        if not arrec and len(d) in (48,44):
            a=conferir_arrecadacao(d)
            if a: arrec=(a,pagina);continue
        if not chave and len(d)==44:
            c=conferir_chave(d)
            if c: chave=(c,pagina)
    if not chave:
        # Chave com letras (CNPJ alfanumérico) em grupos de 4.
        for m in re.finditer(r'(?:[0-9A-Z]{4}[ .]?){10}[0-9A-Z]{4}',texto):
            c=conferir_chave(m.group(0))
            if c: chave=(c,_pagina_de(paginas,m.start()));break
    brcode=next((b for b in (conferir_brcode(m.group(0)) for m in BRCODE.finditer(texto.replace('\n',''))) if b),None)
    e2e=next((e for e in (conferir_e2e(m.group(0),ref_dt) for m in E2E.finditer(texto)) if e),None)

    if chave and ('danfe' in ntexto or 'nota fiscal' in ntexto or 'nfc e' in ntexto or 'chave de acesso' in ntexto):
        info,pagina=chave
        tipo={'55':'nfe','65':'nfce'}.get(info['modelo'],'nfe')
        campos['chave_acesso']=_campo(chave_formatada(info['chave']),'validado','texto',pagina)
        campos.update(_campos_da_chave(info,'texto',pagina))
        m=_rotulo(texto,['valor total da nota','valor total da nfe','valor a pagar','valor total'],VALOR)
        if m: campos['valor_total']=_campo(_dinheiro(m.group(1)),'lido_do_texto','texto')
        itens,aviso_itens=_itens_danfe(lido)
        if itens: campos['itens']=_campo(itens,'a_conferir' if aviso_itens else 'lido_do_texto','tabela')
        if aviso_itens: avisos.append('itens_nao_fecham')
        return _fim(tipo,campos,avisos)

    if boleto:
        b,pagina=boleto;tipo='boleto'
        campos['linha_digitavel']=_campo(linha_formatada(b['linha']),'validado','texto',pagina)
        campos['codigo_barras']=_campo(b['barras'],'validado','texto',pagina)
        campos['banco']=_campo(f"{b['banco']} — {BANCOS[b['banco']]}" if b['banco'] in BANCOS else b['banco'],'validado','texto',pagina)
        if b['valor']: campos['valor']=_campo(b['valor'],'validado','texto',pagina)
        if b['vencimento']: campos['vencimento']=_campo(b['vencimento'],'validado','texto',pagina,regra='fator_de_vencimento')
        _comparar_impresso(texto,b,campos,avisos)
        _partes_do_boleto(texto,campos)
        if brcode: _campos_brcode(brcode,campos,'pix_')
        return _fim(tipo,campos,avisos)

    if arrec:
        a,pagina=arrec;tipo='boleto_concessionaria'
        campos['linha_digitavel']=_campo(arrecadacao_formatada(a['linha']),'validado','texto',pagina)
        campos['codigo_barras']=_campo(a['barras'],'validado','texto',pagina)
        campos['segmento']=_campo(SEGMENTOS.get(a['segmento'],a['segmento']),'validado','texto',pagina)
        if a['valor']:
            campos['valor' if a['valor_efetivo'] else 'valor_referencia']=_campo(a['valor'],'validado','texto',pagina)
        m=_rotulo(texto,['vencimento','data de vencimento','venc'],DATA)
        if m: campos['vencimento']=_campo(_data(*m.groups()),'lido_do_texto','texto')
        _partes_do_boleto(texto,campos)
        return _fim(tipo,campos,avisos)

    if 'nfs e' in ntexto or 'nota fiscal de servico' in ntexto or 'nota fiscal eletronica de servico' in ntexto:
        tipo='nfse'
        m=_rotulo(texto,['numero da nota','numero da nfs e','nfs e n','numero'],r'(\d{1,15})')
        if m: campos['numero']=_campo(m.group(1),'a_conferir','texto')
        m=_rotulo(texto,['valor liquido','valor total da nota','valor dos servicos','valor total'],VALOR)
        if m: campos['valor']=_campo(_dinheiro(m.group(1)),'a_conferir','texto')
        m=_rotulo(texto,['data de emissao','emissao','data e hora de emissao'],DATA)
        if m: campos['emissao']=_campo(_data(*m.groups()),'a_conferir','texto')
        for papel,rotulos in (('prestador',['prestador']),('tomador',['tomador'])):
            d=_doc_perto(texto,rotulos)
            if d: campos[f'{papel}_documento']=_campo(_cnpj_fmt(d[1]) if d[0]=='cnpj' else _cpf_fmt(d[1]),'a_conferir','texto')
        avisos.append('nfse_sem_padrao_nacional_conferido')
        return _fim(tipo,campos,avisos)

    if e2e or ('pix' in ntexto and any(p in ntexto for p in ('comprovante','transferencia','pagamento efetuado','pix enviado','pix realizado'))):
        tipo='comprovante_pix'
        if e2e:
            campos['id_transacao']=_campo(e2e['e2e'],'validado','texto',regra='formato_e2e')
            if e2e['instituicao']: campos['instituicao_pagador']=_campo(e2e['instituicao'],'validado','texto',regra='ispb_do_e2e')
        m=_rotulo(texto,['valor','valor do pix','valor pago','valor da transferencia'],VALOR)
        if m: campos['valor']=_campo(_dinheiro(m.group(1)),'lido_do_texto','texto')
        m=re.search(DATA+r'\D{1,6}(\d{2}):(\d{2})',texto)
        if m:
            d=_data(*m.groups()[:3])
            if d:
                campos['data_hora']=_campo(f'{d}T{m.group(4)}:{m.group(5)}','lido_do_texto','texto')
                if e2e and abs((date.fromisoformat(d)-datetime.fromisoformat(e2e['data_hora_utc']).date()).days)>1: avisos.append('data_difere_do_id_da_transacao')
        for papel,rotulos in (('pagador',['de','pagador','origem','quem pagou','dados de quem pagou']),('recebedor',['para','recebedor','destino','favorecido','quem recebeu','dados de quem recebeu'])):
            n=_nome_apos(texto,rotulos)
            if n: campos[f'{papel}_nome']=_campo(n,'lido_do_texto','texto')
        m=_rotulo(texto,['chave pix','chave'],r'([\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d ().-]{9,17}\d|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})')
        if m and tipo_de_chave(m.group(1)): campos['chave_pix']=_campo(m.group(1).strip(),'lido_do_texto','texto',tipo=tipo_de_chave(m.group(1)))
        if brcode: _campos_brcode(brcode,campos,'')
        return _fim(tipo,campos,avisos,{'estado':'nao_verificado'})

    if brcode:
        tipo='pix_cobranca';_campos_brcode(brcode,campos,'')
        return _fim(tipo,campos,avisos)
    return _fim('outro' if texto.strip() else None,campos,avisos)


def _fim(tipo,campos,avisos,verificacao=None):
    return {'tipo_documento':tipo,'campos':{k:v for k,v in campos.items() if v and v.get('valor') not in (None,'',[])},'avisos':avisos,
            'verificacao_pagamento':verificacao}


def _pagina_de(paginas,pos):
    total=0
    for n,t in paginas:
        total+=len(t)+1
        if pos<total: return n
    return None


def _campos_da_chave(info,fonte,pagina=None):
    c={'modelo':_campo(info['modelo_nome'] or info['modelo'],'validado',fonte,pagina),'serie':_campo(info['serie'],'validado',fonte,pagina),
       'numero':_campo(info['numero'],'validado',fonte,pagina),'ano_mes_emissao':_campo(info['ano_mes'],'validado',fonte,pagina)}
    if info['uf']: c['uf']=_campo(info['uf'],'validado',fonte,pagina)
    c['emitente_cnpj']=_campo(_cnpj_fmt(info['cnpj_emitente']),'validado' if info['cnpj_valido'] else 'a_conferir',fonte,pagina)
    return c


def _comparar_impresso(texto,b,campos,avisos):
    """Valor e vencimento impressos × os do código: vale o código; diferença vira aviso (decisão 28)."""
    m=_rotulo(texto,['valor do documento','valor cobrado','valor a pagar','valor'],VALOR)
    if m and b['valor'] and _dinheiro(m.group(1))!=b['valor']: avisos.append('valor_impresso_difere_do_codigo')
    m=_rotulo(texto,['vencimento','data de vencimento'],DATA)
    if m and b['vencimento'] and _data(*m.groups())!=b['vencimento']: avisos.append('vencimento_impresso_difere_do_codigo')


def _partes_do_boleto(texto,campos):
    for papel,rotulos in (('beneficiario',['beneficiario','cedente']),('pagador',['pagador','sacado'])):
        n=_nome_apos(texto,rotulos)
        if n: campos[f'{papel}_nome']=_campo(n,'lido_do_texto','texto')
        d=_doc_perto(texto,rotulos)
        # O número confere pelo DV; a quem pertence vem do texto.
        if d: campos[f'{papel}_documento']=_campo(_cnpj_fmt(d[1]) if d[0]=='cnpj' else _cpf_fmt(d[1]),'validado','texto',regra=f'dv_{d[0]}',pertence='lido_do_texto')


def _campos_brcode(b,campos,prefixo):
    for k in ('chave','valor','nome','cidade','txid'):
        if b.get(k): campos[f'{prefixo}brcode_{k}']=_campo(b[k],'validado','texto',regra='crc16_brcode')


def _itens_danfe(lido):
    """Itens da tabela do DANFE (descrição, quantidade, valor unitário, total). Aviso quando quantidade × unitário ≠ total."""
    for p in (lido or {}).get('paginas',[]):
        for t in p.get('tabelas',[]):
            linhas=t['linhas']
            for i,cab in enumerate(linhas[:3]):
                nc=[_norm(c) for c in cab]
                def col(*nomes):
                    return next((j for j,c in enumerate(nc) if any(n in c for n in nomes)),None)
                cd,cq,cu,ct=col('descri'),col('quant','qtd','qtde'),col('unit'),col('total','v total','vl total')
                if cd is None or cq is None or ct is None: continue
                itens=[];problema=False
                for l in linhas[i+1:]:
                    if len(l)<=max(x for x in (cd,cq,cu,ct) if x is not None): continue
                    q,u,tot=_dinheiro_ou_qtd(l[cq]),_dinheiro_ou_qtd(l[cu]) if cu is not None else None,_dinheiro_ou_qtd(l[ct])
                    if not l[cd] or tot is None: continue
                    if q is not None and u is not None and abs(q*u-tot)>Decimal('0.02'): problema=True
                    itens.append({'descricao':l[cd],'quantidade':str(q) if q is not None else None,'valor_unitario':str(u) if u is not None else None,'valor_total':f'{tot:.2f}'})
                if itens: return itens,problema
    return [],False


def _dinheiro_ou_qtd(t):
    s=str(t or '').replace('R$','').strip()
    if not s: return None
    if ',' in s: s=s.replace('.','').replace(',','.')
    try: return Decimal(s)
    except InvalidOperation: return None


# ---------------- guarda: "pagamento confirmado" ----------------

AFIRMA=re.compile(r"(?i)\b(pagamento|pix|transfer[eê]ncia|dep[oó]sito)\s+(j[aá]\s+)?(foi\s+|est[aá]\s+|t[aá]\s+)?(confirmad[oa]|recebid[oa]|aprovad[oa]|identificad[oa]|compensad[oa]|creditad[oa])"
                  r"|\b(recebemos|confirmamos|identificamos)\s+(o\s+|seu\s+|sua\s+|a\s+)?(pagamento|pix|transfer[eê]ncia)"
                  r"|\bj[aá]\s+(est[aá]|t[aá])\s+pag[oa]\b|\bpag[oa]\s+com\s+sucesso\b|\bpagamento\s+ok\b")
NEGA=re.compile(r"(?i)\b(n[aã]o|ainda\s+n[aã]o|vamos|vou|iremos|assim\s+que|quando|ap[oó]s|depois\s+que|aguard|conferir|confer[eê]ncia|verificar)\b")
AVISO_AO_AGENTE=('Sua resposta afirmou que o pagamento foi confirmado ou recebido. O gateway NUNCA confirma pagamento por comprovante '
                 '(verificacao_pagamento: nao_verificado). Diga ao cliente que recebeu o comprovante e que a equipe vai conferir.')
TEXTO_AO_CLIENTE='Recebi o seu comprovante. A equipe vai conferir o pagamento e te confirma por aqui.'


def afirma_pagamento(resultado):
    """A resposta do agente afirma pagamento confirmado/recebido (frase a frase; frase com negação ou futuro não conta)?"""
    for balao in (resultado or {}).get('resposta') or []:
        for frase in re.split(r'(?<=[.!?\n])\s+',str(balao)):
            if AFIRMA.search(frase) and not NEGA.search(frase): return True
    return False
