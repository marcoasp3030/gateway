"""ARQ1-a3 (docs/ESPEC_ARQ1.md, seção 15): campos por regra, sem IA — boleto bancário (DVs, fator de vencimento nas duas bases),
concessionária (módulo 10 e 11), NF-e/NFC-e (chave de acesso com CNPJ alfanumérico, DANFE, XML), NFS-e (só a_conferir), comprovante
de PIX (E2E só pelo formato, nunca pagamento confirmado), BR Code; confiança de cada campo; guarda de pagamento afirmado."""
from datetime import date,datetime,timezone
import pytest
from nucleo import documentos_regras as R
import documentos_sinteticos as S

HOJE=date(2026,10,21)
AGORA=datetime(2026,10,21,15,0,tzinfo=timezone.utc)


def lido(*paginas,tabelas=None,nfe=None):
    return {'formato':'pdf','paginas':[{'pagina':n,'tem_texto':bool(t),'origem':'texto','texto':t,'tabelas':(tabelas or {}).get(n,[])} for n,t in enumerate(paginas,1)],
            **({'nfe':nfe} if nfe else {})}


def test_digitos_verificadores_e_cnpj_alfanumerico_oficial():
    assert R.cnpj_valido('12ABC34501DE35') and R.cnpj_valido('12.ABC.345/01DE-35')       # exemplo da Receita (CNPJ alfanumérico)
    assert not R.cnpj_valido('12ABC34501DE36') and R.cnpj_valido('11222333000181') and not R.cnpj_valido('11111111111111')
    assert R.cpf_valido('123.456.789-09') and not R.cpf_valido('123.456.789-00') and not R.cpf_valido('00000000000')
    c=S.chave();assert R.conferir_chave(c)['numero']=='1234' and R.conferir_chave(c[:-1]+str((int(c[-1])+1)%10)) is None
    alfa=S.chave(cnpj_emitente=S.cnpj('12ABC34501DE'))
    info=R.conferir_chave(alfa);assert info and info['cnpj_emitente']=='12ABC34501DE35' and info['cnpj_valido']
    assert R.conferir_chave(alfa.lower()) is not None and R.conferir_chave('A'+alfa[1:]) is None    # letra só nas posições do CNPJ


def test_boleto_bancario_dvs_fator_e_codigo_de_barras():
    l,b=S.boleto(vencimento=date(2026,10,25),valor_centavos=15230)
    r=R.conferir_boleto(l,HOJE);assert (r['vencimento'],r['valor'],r['banco'],r['barras'])==('2026-10-25','152.30','341',b)
    assert R.conferir_boleto(b,HOJE)['linha']==l
    for i in (5,15,25,40): assert R.conferir_boleto(l[:i]+str((int(l[i])+1)%10)+l[i+1:],HOJE) is None
    # Mesmo fator, duas datas (base antiga e a de 22/02/2025): vale a mais próxima da data do documento.
    antigo,_=S.boleto(vencimento=date(2023,5,10));assert R.conferir_boleto(antigo,date(2023,5,1))['vencimento']=='2023-05-10'
    novo,_=S.boleto(vencimento=date(2025,2,22));assert R.conferir_boleto(novo,HOJE)['fator']==1000 and R.conferir_boleto(novo,HOJE)['vencimento']=='2025-02-22'
    assert R.vencimento_do_fator(1000,date(2000,7,1))==date(2000,7,3)
    livre,_=S.boleto(valor_centavos=0);assert R.conferir_boleto(livre,HOJE)['valor'] is None


def test_concessionaria_modulo_10_e_11_e_valor_de_referencia():
    for ident in '6789':
        l,b=S.arrecadacao(ident=ident,valor_centavos=8945)
        r=R.conferir_arrecadacao(l);assert r and r['barras']==b and R.conferir_arrecadacao(b)['linha']==l
        assert (r['valor'],r['valor_efetivo'])==('89.45',ident in '68')
        assert R.conferir_arrecadacao(l[:3]+str((int(l[3])+1)%10)+l[4:]) is None
    assert R.conferir_boleto(S.arrecadacao()[0][:47],HOJE) is None


def test_e2e_brcode_e_chave_pix():
    assert R.conferir_e2e(S.e2e(),AGORA)['instituicao']=='Itaú Unibanco'
    assert R.conferir_e2e(S.e2e(ispb='ABCD1234'),AGORA)['ispb']=='ABCD1234'
    assert R.conferir_e2e(S.e2e(quando='202613011330')) is None and R.conferir_e2e(S.e2e(quando='202710211330'),AGORA) is None
    assert R.conferir_e2e(S.e2e()[:-1]) is None and R.conferir_e2e('X'+S.e2e()[1:]) is None
    b=R.conferir_brcode(S.brcode());assert (b['chave'],b['valor'],b['txid'])==('cobranca@exemplo.com.br','152.30','PEDIDO77')
    assert R.conferir_brcode(S.brcode()[:-2]+'00') is None
    assert [R.tipo_de_chave(x) for x in ('fulano@exemplo.com','+55 11 91234-5678','123.456.789-09','11.222.333/0001-81','5c1c3f0e-1a2b-4c3d-8e9f-0a1b2c3d4e5f')]==\
           ['email','celular','cpf','cnpj','aleatoria']


def test_analise_boleto_com_partes_divergencia_e_brcode():
    l,_=S.boleto(vencimento=date(2026,10,25),valor_centavos=15230)
    texto='\n'.join(['Recibo do Pagador','Beneficiário: Empresa Exemplo Ltda CNPJ 11.222.333/0001-81','Pagador: Pessoa Exemplo CPF 123.456.789-09',
                     'Vencimento 26/10/2026','Valor do Documento R$ 152,30',R.linha_formatada(l),'Pix copia e cola: '+S.brcode()])
    a=R.analisar(lido(texto),AGORA)
    c=a['campos'];assert a['tipo_documento']=='boleto'
    assert {k:c[k]['confianca'] for k in ('linha_digitavel','codigo_barras','banco','valor','vencimento')}=={k:'validado' for k in ('linha_digitavel','codigo_barras','banco','valor','vencimento')}
    assert (c['valor']['valor'],c['vencimento']['valor'],c['banco']['valor'])==('152.30','2026-10-25','341 — Itaú Unibanco')
    assert (c['beneficiario_nome']['valor'],c['beneficiario_nome']['confianca'])==('Empresa Exemplo Ltda','lido_do_texto')
    assert (c['beneficiario_documento']['valor'],c['beneficiario_documento']['confianca'],c['beneficiario_documento']['pertence'])==('11.222.333/0001-81','validado','lido_do_texto')
    assert c['pagador_documento']['valor']=='123.456.789-09' and c['pix_brcode_txid']['valor']=='PEDIDO77'
    # Vencimento impresso (26/10) difere do código (25/10): vale o código, com aviso.
    assert a['avisos']==['vencimento_impresso_difere_do_codigo'] and a['verificacao_pagamento'] is None
    # Linha com DV errado: não vira boleto validado.
    ruim=R.linha_formatada(l)[:-1]+str((int(l[-1])+1)%10)
    assert R.analisar(lido('Recibo do pagador\n'+ruim),AGORA)['tipo_documento']=='outro'


def test_analise_concessionaria_nfe_danfe_nfse():
    l,_=S.arrecadacao(segmento='3',ident='8',valor_centavos=8945)
    a=R.analisar(lido('Conta de energia\nVencimento: 30/10/2026\n'+R.arrecadacao_formatada(l)),AGORA)
    assert a['tipo_documento']=='boleto_concessionaria' and a['campos']['valor']['valor']=='89.45' and a['campos']['segmento']['valor']=='energia elétrica e gás'
    assert (a['campos']['vencimento']['valor'],a['campos']['vencimento']['confianca'])==('2026-10-30','lido_do_texto')
    ch=S.chave(cnpj_emitente=S.cnpj('12ABC34501DE'),numero=777)
    danfe=lido('DANFE\nDocumento Auxiliar da Nota Fiscal Eletrônica\nCHAVE DE ACESSO\n'+R.chave_formatada(ch)+'\nVALOR TOTAL DA NOTA 49,00',
               tabelas={1:[{'linhas':[['CÓDIGO','DESCRIÇÃO','QUANT.','V. UNIT.','V. TOTAL'],['A1','Caneta azul','10','2,50','25,00'],['B2','Caderno','2','12,00','24,00']]}]})
    a=R.analisar(danfe,AGORA);c=a['campos']
    assert a['tipo_documento']=='nfe' and c['chave_acesso']['confianca']=='validado' and c['numero']['valor']=='777' and c['emitente_cnpj']['valor']=='12.ABC.345/01DE-35'
    assert c['uf']['valor']=='SP' and c['valor_total']['valor']=='49.00' and c['itens']['confianca']=='lido_do_texto' and len(c['itens']['valor'])==2
    danfe['paginas'][0]['tabelas'][0]['linhas'][1][4]='26,00'
    a=R.analisar(danfe,AGORA);assert a['campos']['itens']['confianca']=='a_conferir' and 'itens_nao_fecham' in a['avisos']
    nfse=R.analisar(lido('NFS-e Nota Fiscal de Serviço Eletrônica\nNúmero da Nota: 4521\nData de emissão 15/10/2026\nPrestador: Empresa Exemplo CNPJ 11.222.333/0001-81\nValor Líquido R$ 1.500,00'),AGORA)
    assert nfse['tipo_documento']=='nfse' and {v['confianca'] for v in nfse['campos'].values()}=={'a_conferir'} and nfse['campos']['valor']['valor']=='1500.00'


def test_analise_xml_da_nfe_e_comprovante_pix():
    ch=S.chave()
    a=R.analisar({'formato':'xml','paginas':[{'pagina':1,'tem_texto':True,'origem':'xml','texto':'x','tabelas':[]}],
                  'nfe':{'chave':ch,'modelo':'55','emitente':{'documento':'11222333000181','nome':'Fornecedor'},'destinatario':{'documento':'12345678909','nome':'Pessoa'},'valor_total':'49.00'}},AGORA)
    c=a['campos'];assert (c['chave_acesso']['confianca'],c['emitente_documento']['confianca'],c['destinatario_documento']['valor'],c['valor_total']['confianca'])==\
                         ('validado','validado','123.456.789-09','lido_do_texto')
    a=R.analisar({'formato':'xml','paginas':[],'nfe':{'chave':ch[:-1]+'0' if ch[-1]!='0' else ch[:-1]+'1','modelo':'55'}},AGORA)
    assert a['campos']['chave_acesso']['confianca']=='a_conferir' and 'chave_de_acesso_com_dv_invalido' in a['avisos']
    pix=lido('Comprovante de transferência Pix\nValor R$ 152,30\n21/10/2026 - 10:31\nDe\nPessoa Exemplo\nPara\nEmpresa Exemplo Ltda\nChave Pix: cobranca@exemplo.com.br\nID da transação: '+S.e2e())
    a=R.analisar(pix,AGORA);c=a['campos']
    assert a['tipo_documento']=='comprovante_pix' and a['verificacao_pagamento']=={'estado':'nao_verificado'}
    assert (c['id_transacao']['confianca'],c['instituicao_pagador']['valor'],c['valor']['valor'],c['data_hora']['valor'])==('validado','Itaú Unibanco','152.30','2026-10-21T10:31')
    assert (c['pagador_nome']['valor'],c['recebedor_nome']['valor'],c['chave_pix']['tipo'])==('Pessoa Exemplo','Empresa Exemplo Ltda','email')
    assert all(v['confianca']=='lido_do_texto' for k,v in c.items() if k not in ('id_transacao','instituicao_pagador'))
    assert R.analisar(lido('Contrato de prestação de serviços\nCláusula 1'),AGORA)['tipo_documento']=='outro'


@pytest.mark.parametrize('frase,afirma',[('Pagamento confirmado! Obrigado.',True),('Recebemos seu Pix 🎉',True),('Seu pagamento foi aprovado.',True),
    ('Já está pago, tudo certo.',True),('Recebi o comprovante, a equipe vai conferir o pagamento.',False),('Assim que o pagamento for confirmado te aviso.',False),
    ('O pagamento ainda não foi confirmado.',False),('Pode me mandar o comprovante?',False)])
def test_frase_que_afirma_pagamento(frase,afirma):
    assert R.afirma_pagamento({'resposta':[frase]})==afirma


def test_ponta_a_ponta_boleto_no_pacote_e_guarda_de_pagamento(tmp_path,monkeypatch):
    from test_arq1a import cenario,T
    import pdf_sintetico as P
    l,_=S.boleto(vencimento=date(2026,10,25),valor_centavos=15230)
    pdf=P.pdf([(['Recibo do Pagador','Beneficiario: Empresa Exemplo Ltda CNPJ 11.222.333/0001-81','Pagador: Pessoa Exemplo CPF 123.456.789-09',
                 'Valor do Documento R$ 152,30',R.linha_formatada(l)],None)])
    comprovante=P.pdf([(['Comprovante de transferencia Pix','Valor R$ 152,30','21/10/2026 - 10:31','ID da transacao: '+S.e2e()],None)])
    async def corpo(pool,c,p,enviar,pacotes,roteiro,**_):
        d=(await enviar(arquivo=pdf,nome='boleto.pdf')).json()
        cps=d['conteudo']['campos'];assert d['tipo_documento']=='boleto' and cps['linha_digitavel']['confianca']=='validado' and cps['valor']['valor']=='152.30'
        r=(await enviar(arquivo=comprovante,nome='pix.pdf',modo='agente',contato='5511900000401')).json()
        assert r['tipo_documento']=='comprovante_pix' and r['verificacao_pagamento']=={'estado':'nao_verificado'}
        # O agente afirma pagamento confirmado duas vezes: refeito com o recado e, repetindo, a resposta é segurada.
        afirma=lambda payload: {'resposta':['Pagamento confirmado, obrigado!'],'acao':'nenhuma'}
        async def a1(payload): return afirma(payload)
        async def a2(payload):
            assert 'NUNCA confirma pagamento' in (payload['aviso_do_gateway'] or '');return afirma(payload)
        roteiro[:]=[a1,a2]
        assert await p.processar_duravel(T,r['conversation_id'])=='concluido'
        pk=pacotes[0];[anexo]=pk['anexos']
        assert anexo['tipo_documento']=='comprovante_pix' and anexo['verificacao_pagamento']=={'estado':'nao_verificado'} and anexo['campos']['id_transacao']['confianca']=='validado'
        turnos=await pool.fetch("SELECT status,pagamento_afirmado,resposta FROM turnos WHERE conversation_id=$1 ORDER BY id",r['conversation_id'])
        assert [t['pagamento_afirmado'] for t in turnos]==['refeito','segurado']
        enviado=await pool.fetchval("SELECT conteudo FROM mensagens WHERE conversation_id=$1 AND origem='bot' ORDER BY id DESC LIMIT 1",r['conversation_id'])
        assert enviado==R.TEXTO_AO_CLIENTE
    cenario(tmp_path,monkeypatch,corpo)
