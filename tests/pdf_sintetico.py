"""PDFs sintéticos para os testes do ARQ1 (sem biblioteca: objetos escritos à mão, fonte Helvetica padrão).
Cada página: linhas de texto e, opcionalmente, uma tabela desenhada com linhas (o pdfplumber reconhece pelas bordas)."""


def _esc(t):
    return t.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _pagina(linhas, tabela=None):
    ops = ['BT /F1 11 Tf 50 780 Td 14 TL']
    for l in linhas: ops.append(f'({_esc(l)}) Tj T*')
    ops.append('ET')
    if tabela:
        # Grade de 120 pt por coluna e 20 pt por linha, começando em (50, 600).
        n, m = len(tabela), len(tabela[0]); x0, y0, w, h = 50, 600, 120, 20
        for i in range(n + 1): ops.append(f'{x0} {y0 - i*h} m {x0 + m*w} {y0 - i*h} l S')
        for j in range(m + 1): ops.append(f'{x0 + j*w} {y0} m {x0 + j*w} {y0 - n*h} l S')
        for i, linha in enumerate(tabela):
            for j, cel in enumerate(linha):
                ops.append(f'BT /F1 9 Tf {x0 + j*w + 4} {y0 - (i+1)*h + 6} Td ({_esc(cel)}) Tj ET')
    return '\n'.join(ops).encode('latin-1')


def pdf(paginas, extra_catalogo=''):
    """paginas: [(linhas, tabela|None)]; linhas vazias = página sem texto (como escaneada). extra_catalogo: ex. '/OpenAction ...'."""
    objs = []
    def novo(corpo): objs.append(corpo); return len(objs)
    fonte = novo(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    paginas_ids = []
    raiz_paginas = len(objs) + 2 * len(paginas) + 1
    for linhas, tabela in paginas:
        conteudo = _pagina(linhas, tabela)
        c = novo(b'<< /Length %d >>\nstream\n' % len(conteudo) + conteudo + b'\nendstream')
        p = novo(f'<< /Type /Page /Parent {raiz_paginas} 0 R /MediaBox [0 0 595 842] /Contents {c} 0 R /Resources << /Font << /F1 {fonte} 0 R >> >> >>'.encode())
        paginas_ids.append(p)
    kids = ' '.join(f'{i} 0 R' for i in paginas_ids)
    assert novo(f'<< /Type /Pages /Kids [{kids}] /Count {len(paginas_ids)} >>'.encode()) == raiz_paginas
    catalogo = novo(f'<< /Type /Catalog /Pages {raiz_paginas} 0 R {extra_catalogo} >>'.encode())
    saida = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'); offsets = []
    for n, corpo in enumerate(objs, 1):
        offsets.append(len(saida)); saida += f'{n} 0 obj\n'.encode() + corpo + b'\nendobj\n'
    xref = len(saida)
    saida += f'xref\n0 {len(objs)+1}\n0000000000 65535 f \n'.encode()
    for o in offsets: saida += f'{o:010d} 00000 n \n'.encode()
    saida += f'trailer\n<< /Size {len(objs)+1} /Root {catalogo} 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return bytes(saida)


NFE_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe><infNFe Id="NFe{chave}" versao="4.00">
<ide><cUF>35</cUF><mod>55</mod><serie>1</serie><nNF>1234</nNF><dhEmi>2026-10-20T10:00:00-03:00</dhEmi></ide>
<emit><CNPJ>11222333000181</CNPJ><xNome>Fornecedor Exemplo Ltda</xNome><enderEmit><UF>SP</UF></enderEmit></emit>
<dest><CNPJ>11444777000161</CNPJ><xNome>Cliente Exemplo SA</xNome></dest>
<det nItem="1"><prod><cProd>A1</cProd><xProd>Caneta azul</xProd><qCom>10.0000</qCom><uCom>UN</uCom><vUnCom>2.50</vUnCom><vProd>25.00</vProd></prod></det>
<det nItem="2"><prod><cProd>B2</cProd><xProd>Caderno</xProd><qCom>2.0000</qCom><uCom>UN</uCom><vUnCom>12.00</vUnCom><vProd>24.00</vProd></prod></det>
<total><ICMSTot><vProd>49.00</vProd><vNF>49.00</vNF></ICMSTot></total></infNFe></NFe>
<protNFe><infProt><nProt>135260000000001</nProt><cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe></nfeProc>'''
CHAVE='35261011222333000181550010000012341000123453'
