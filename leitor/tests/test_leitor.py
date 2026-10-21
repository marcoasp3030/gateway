"""Leitor de documentos (ARQ1-a1): roda na imagem do leitor (scripts/test_suite.sh), com as bibliotecas dele.
PDF com texto e tabelas, página sem texto, PDF com conteúdo ativo, PDF inválido, XML da NF-e, XML perigoso e XML que não é NF-e."""
import leitor
import pdf_sintetico as P


def test_pdf_texto_tabela_e_pagina_sem_texto():
    r = leitor.ler_isolado('pdf', P.pdf([(['Recibo do pagador', 'Beneficiario Empresa Exemplo Ltda', 'Valor R$ 152,30'],
                                          [['Item', 'Qtd', 'Valor'], ['Caneta', '10', '25,00']]), ([], None)]))
    assert r['ok'] and r['formato'] == 'pdf' and len(r['paginas']) == 2
    p1, p2 = r['paginas']
    assert p1['tem_texto'] and 'Beneficiario Empresa Exemplo Ltda' in p1['texto'] and p1['origem'] == 'texto'
    assert p1['tabelas'] == [{'linhas': [['Item', 'Qtd', 'Valor'], ['Caneta', '10', '25,00']]}]
    assert (p2['tem_texto'], p2['origem'], p2['texto']) == (False, 'sem_texto', '')


def test_pdf_recusado_sem_executar_nada():
    assert leitor.ler_isolado('pdf', P.pdf([(['oi'], None)], '/OpenAction << /S /JavaScript /JS (app.alert(1)) >>')) == {'ok': False, 'motivo': 'pdf_conteudo_ativo'}
    assert leitor.ler_isolado('pdf', b'%PDF-1.4 lixo sem estrutura') == {'ok': False, 'motivo': 'pdf_invalido'}
    muitas = P.pdf([(['p'], None)] * (leitor.MAX_PAGINAS + 1))
    assert leitor.ler_isolado('pdf', muitas) == {'ok': False, 'motivo': 'paginas_demais'}
    assert leitor.ler('exe', b'MZ') == {'ok': False, 'motivo': 'tipo_nao_aceito'}


def test_xml_da_nfe():
    r = leitor.ler_isolado('xml', P.NFE_XML.format(chave=P.CHAVE).encode())
    assert r['ok'] and r['formato'] == 'xml'
    n = r['nfe']
    assert (n['chave'], n['modelo'], n['serie'], n['numero'], n['valor_total']) == (P.CHAVE, '55', '1', '1234', '49.00')
    assert n['emitente'] == {'documento': '11222333000181', 'nome': 'Fornecedor Exemplo Ltda', 'uf': 'SP'}
    assert [i['descricao'] for i in n['itens']] == ['Caneta azul', 'Caderno'] and n['protocolo']['status'] == '100'
    assert r['paginas'][0]['tabelas'][0]['linhas'][1] == ['A1', 'Caneta azul', '10.0000', 'UN', '2.50', '25.00']


def test_xml_perigoso_ou_estranho():
    entidade = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY e SYSTEM "file:///etc/passwd">]><a>&e;</a>'
    assert leitor.ler_isolado('xml', entidade) == {'ok': False, 'motivo': 'xml_invalido'}
    bomba = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><a>&b;</a>'
    assert leitor.ler_isolado('xml', bomba) == {'ok': False, 'motivo': 'xml_invalido'}
    assert leitor.ler_isolado('xml', b'<a>x</a>') == {'ok': False, 'motivo': 'xml_nao_e_nfe'}


def test_tempo_esgotado_mata_o_filho(monkeypatch):
    import time
    monkeypatch.setattr(leitor, 'ler', lambda f, d: time.sleep(5))
    assert leitor.ler_isolado('pdf', b'%PDF', tempo_s=1) == {'ok': False, 'motivo': 'tempo'}
