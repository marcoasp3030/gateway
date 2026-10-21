"""Leitor de documentos do gateway (ARQ1, docs/ESPEC_ARQ1.md seções 13 e 14).

Recebe os bytes de UM documento e devolve JSON: texto por página e tabelas (PDF com texto), ou os dados do XML da NF-e/NFC-e.
Roda em contêiner próprio, numa rede interna sem saída para a internet e sem credencial nenhuma (nem banco, nem Redis, nem canal).
Um documento por vez; cada leitura num processo filho com teto de memória e de tempo (o pai mata o filho que passar).
Não executa nada do documento: PDF com JavaScript, ação de abertura, arquivo embutido ou formulário XFA é recusado; XML sem DTD
nem entidade externa (defusedxml). Foto e PDF escaneado (OCR) ficam para o ARQ1-a2."""
import hmac
import io
import json
import multiprocessing
import os
import re
import resource
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BYTES = 15 * 1024 * 1024
MAX_PAGINAS = 50
MAX_TEXTO_PAGINA = 20000
MAX_TABELAS_PAGINA = 10
MAX_LINHAS_TABELA = 500
MAX_COLUNAS_TABELA = 30
MAX_CELULA = 500
MAX_ITENS_NFE = 500
TEMPO_S = int(os.getenv('LEITOR_TEMPO_S', '60'))
MEMORIA_FILHO = int(os.getenv('LEITOR_MEMORIA_MB', '600')) * 1024 * 1024
# Conteúdo ativo de PDF: nomes procurados nos bytes e no catálogo (defesa dupla; o leitor também está isolado).
ATIVO = re.compile(rb'/(JavaScript|JS|Launch|EmbeddedFiles?|RichMedia|XFA)(?![A-Za-z])')
NFE = '{http://www.portalfiscal.inf.br/nfe}'


class Recusado(Exception):
    """Documento que não vai ser lido, com motivo curto e seguro (sem detalhe do conteúdo)."""
    def __init__(self, motivo): super().__init__(motivo); self.motivo = motivo


def _celula(v):
    t = '' if v is None else ' '.join(str(v).split())
    return t[:MAX_CELULA]


def ler_pdf(dados):
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect, PDFEncryptionError
    if ATIVO.search(dados): raise Recusado('pdf_conteudo_ativo')
    try:
        pdf = pdfplumber.open(io.BytesIO(dados))
    except (PDFPasswordIncorrect, PDFEncryptionError):
        raise Recusado('pdf_protegido') from None
    except Exception:
        raise Recusado('pdf_invalido') from None
    with pdf:
        catalogo = getattr(pdf.doc, 'catalog', {}) or {}
        nomes = catalogo.get('Names')
        try: nomes = nomes.resolve() if hasattr(nomes, 'resolve') else nomes
        except Exception: nomes = None
        if catalogo.get('OpenAction') is not None and 'JavaScript' in repr(catalogo.get('OpenAction')): raise Recusado('pdf_conteudo_ativo')
        if isinstance(nomes, dict) and any(k in nomes for k in ('JavaScript', 'EmbeddedFiles')): raise Recusado('pdf_conteudo_ativo')
        if len(pdf.pages) > MAX_PAGINAS: raise Recusado('paginas_demais')
        paginas = []
        for n, p in enumerate(pdf.pages, 1):
            try: texto = p.extract_text() or ''
            except Exception: texto = ''
            texto = texto.strip()
            tabelas = []
            try:
                for t in (p.extract_tables() or [])[:MAX_TABELAS_PAGINA]:
                    linhas = [[_celula(c) for c in (linha or [])[:MAX_COLUNAS_TABELA]] for linha in t[:MAX_LINHAS_TABELA]]
                    linhas = [l for l in linhas if any(l)]
                    if linhas: tabelas.append({'linhas': linhas})
            except Exception:
                pass
            paginas.append({'pagina': n, 'tem_texto': bool(texto), 'origem': 'texto' if texto else 'sem_texto',
                            'texto': texto[:MAX_TEXTO_PAGINA], 'texto_cortado': len(texto) > MAX_TEXTO_PAGINA, 'tabelas': tabelas})
    return {'formato': 'pdf', 'paginas': paginas}


def _txt(no, caminho):
    """Texto do elemento em `caminho` ("enderEmit/UF"), no espaço de nomes da NF-e; None se não houver."""
    if no is None: return None
    achado = no.find('/'.join(NFE + p for p in caminho.split('/')))
    return achado.text.strip() if achado is not None and achado.text else None


def ler_xml(dados):
    """XML da NF-e (modelo 55) ou NFC-e (65), com ou sem o protocolo (nfeProc). Outro XML é recusado."""
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException
    try:
        raiz = ElementTree.fromstring(dados, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ElementTree.ParseError, ValueError):
        raise Recusado('xml_invalido') from None
    inf = raiz.find(f'.//{NFE}infNFe')
    if inf is None: raise Recusado('xml_nao_e_nfe')
    ide, emit, dest = inf.find(f'{NFE}ide'), inf.find(f'{NFE}emit'), inf.find(f'{NFE}dest')
    tot = inf.find(f'{NFE}total/{NFE}ICMSTot')
    ident = inf.get('Id') or ''
    itens = []
    for det in inf.findall(f'{NFE}det')[:MAX_ITENS_NFE]:
        prod = det.find(f'{NFE}prod')
        itens.append({k: _txt(prod, c) for k, c in (('codigo', 'cProd'), ('descricao', 'xProd'), ('quantidade', 'qCom'), ('unidade', 'uCom'),
                                                    ('valor_unitario', 'vUnCom'), ('valor_total', 'vProd'))})
    prot = raiz.find(f'.//{NFE}protNFe/{NFE}infProt')
    nfe = {'chave': ident[3:] if ident.startswith('NFe') else None, 'modelo': _txt(ide, 'mod'), 'serie': _txt(ide, 'serie'), 'numero': _txt(ide, 'nNF'),
           'emissao': _txt(ide, 'dhEmi') or _txt(ide, 'dEmi'),
           'emitente': {'documento': _txt(emit, 'CNPJ') or _txt(emit, 'CPF'), 'nome': _txt(emit, 'xNome'), 'uf': _txt(emit, 'enderEmit/UF')},
           'destinatario': {'documento': _txt(dest, 'CNPJ') or _txt(dest, 'CPF'), 'nome': _txt(dest, 'xNome')},
           'valor_total': _txt(tot, 'vNF'), 'valor_produtos': _txt(tot, 'vProd'), 'itens': itens, 'itens_cortados': len(inf.findall(f'{NFE}det')) > MAX_ITENS_NFE,
           'protocolo': {'numero': _txt(prot, 'nProt'), 'status': _txt(prot, 'cStat'), 'motivo': _txt(prot, 'xMotivo')} if prot is not None else None}
    linhas = [f'Chave de acesso: {nfe["chave"] or "-"}', f'Modelo {nfe["modelo"] or "-"} · série {nfe["serie"] or "-"} · número {nfe["numero"] or "-"}',
              f'Emissão: {nfe["emissao"] or "-"}', f'Emitente: {nfe["emitente"]["nome"] or "-"} ({nfe["emitente"]["documento"] or "-"})',
              f'Destinatário: {nfe["destinatario"]["nome"] or "-"} ({nfe["destinatario"]["documento"] or "-"})', f'Valor total: {nfe["valor_total"] or "-"}']
    tabela = [['Código', 'Descrição', 'Quantidade', 'Unidade', 'Valor unitário', 'Valor total']] + \
             [[_celula(i[k]) for k in ('codigo', 'descricao', 'quantidade', 'unidade', 'valor_unitario', 'valor_total')] for i in itens]
    return {'formato': 'xml', 'nfe': nfe,
            'paginas': [{'pagina': 1, 'tem_texto': True, 'origem': 'xml', 'texto': '\n'.join(linhas), 'texto_cortado': False,
                         'tabelas': [{'linhas': tabela}] if itens else []}]}


def ler(formato, dados):
    """{'ok': True, ...resultado} ou {'ok': False, 'motivo': ...}. Nunca levanta exceção para quem chama."""
    if len(dados) > MAX_BYTES: return {'ok': False, 'motivo': 'tamanho'}
    try:
        if formato == 'pdf': return {'ok': True, **ler_pdf(dados)}
        if formato == 'xml': return {'ok': True, **ler_xml(dados)}
        return {'ok': False, 'motivo': 'tipo_nao_aceito'}
    except Recusado as r:
        return {'ok': False, 'motivo': r.motivo}
    except MemoryError:
        return {'ok': False, 'motivo': 'memoria'}
    except Exception:
        return {'ok': False, 'motivo': 'falha_na_leitura'}


def _filho(formato, dados, saida):
    try: resource.setrlimit(resource.RLIMIT_AS, (MEMORIA_FILHO, MEMORIA_FILHO))
    except (ValueError, OSError): pass
    saida.send(json.dumps(ler(formato, dados), ensure_ascii=False))
    saida.close()


def ler_isolado(formato, dados, tempo_s=TEMPO_S):
    """Lê num processo filho; passou do tempo → mata e devolve motivo 'tempo'."""
    contexto = multiprocessing.get_context('fork')
    pai, filho = contexto.Pipe(duplex=False)
    p = contexto.Process(target=_filho, args=(formato, dados, filho), daemon=True)
    p.start(); filho.close()
    try:
        if pai.poll(tempo_s): return json.loads(pai.recv())
        return {'ok': False, 'motivo': 'tempo'}
    except EOFError:
        return {'ok': False, 'motivo': 'memoria'}
    finally:
        if p.is_alive(): p.kill()
        p.join(5); pai.close()


UM_POR_VEZ = threading.Semaphore(1)


class Atendente(BaseHTTPRequestHandler):
    server_version = 'leitor'
    sys_version = ''

    def _responder(self, status, corpo):
        bruto = json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(bruto)))
        self.end_headers(); self.wfile.write(bruto)

    def log_message(self, *a):
        # Sem registro de requisição: nada do documento vai para log.
        pass

    def do_GET(self):
        if self.path == '/saude': return self._responder(200, {'ok': True})
        self._responder(404, {'ok': False})

    def do_POST(self):
        token = os.getenv('LEITOR_TOKEN', '')
        if self.path != '/ler' or not token or not hmac.compare_digest(self.headers.get('X-Leitor-Token', ''), token):
            return self._responder(404, {'ok': False})
        try: tamanho = int(self.headers.get('Content-Length', '0'))
        except ValueError: tamanho = -1
        if not 0 < tamanho <= MAX_BYTES: return self._responder(413, {'ok': False, 'motivo': 'tamanho'})
        dados = self.rfile.read(tamanho)
        formato = self.headers.get('X-Formato', '')
        if not UM_POR_VEZ.acquire(timeout=TEMPO_S): return self._responder(503, {'ok': False, 'motivo': 'ocupado'})
        try: self._responder(200, ler_isolado(formato, dados))
        finally: UM_POR_VEZ.release()


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('LEITOR_PORTA', '8090'))), Atendente).serve_forever()
