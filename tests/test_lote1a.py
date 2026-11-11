"""LOTE1-a (docs/ESPEC_NFSE1_LOTE1.md): notas em lote. Criar, mandar PDF/XML/ZIP (um por requisição), leitura em segundo plano uma por vez
cedendo a vez ao atendimento, junção de PDF e XML da mesma nota, duplicados, limites, fechar, concluir e apagar. Leitor simulado."""
import io
import zipfile
import pytest
from test_migrations import DSN
from test_arq1a import cenario,PDF,A,T
import pdf_sintetico as P

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
G={'X-Role':'gestor','X-Actor':'gestor@exemplo.test'}
SUP={'X-Role':'supervisor','X-Actor':'sup@exemplo.test'}
XML=b'<?xml version="1.0"?><nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe/></nfeProc>'
DANFE=P.pdf([(['DANFE Documento Auxiliar da Nota Fiscal Eletronica','Chave de acesso '+P.CHAVE,'Valor total da nota R$ 49,00'],None)])


def zipar(entradas):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        for nome,dados in entradas: z.writestr(nome,dados)
    return b.getvalue()


def test_lote_envio_leitura_juncao_e_fechamento(tmp_path,monkeypatch):
    async def corpo(pool,c,p,chamadas,pasta,**_):
        from nucleo import lotes as L
        base=f'/admin/v1/tenants/{T}'
        motivo=lambda r: r.json()['erro']['detalhes'].get('motivo')
        assert (await c.post(f'{base}/agentes/{A}/lotes',json={},headers=SUP)).status_code==403
        r=await c.post(f'{base}/agentes/{A}/lotes',json={'nome':'Notas de outubro'},headers=G);assert r.status_code==201,r.text
        lote=r.json();lid=lote['lote_id']
        assert (lote['estado'],lote['nome'],lote['arquivos']['total'],lote['limites']['notas'])==('aberto','Notas de outubro',0,200)
        q=f'{base}/lotes/{lid}'
        enviar=lambda nome,dados: c.post(q+'/arquivos',files={'arquivo':(nome,dados,'application/octet-stream')},headers=G)
        # Arquivo solto: XML da NF-e e o DANFE (PDF) da mesma nota; um PDF qualquer; o mesmo PDF de novo (duplicado).
        r=await enviar('nota.xml',XML);assert r.status_code==202,r.text
        assert r.json()['arquivos'][0]['estado']=='na_fila' and chamadas==[]   # não lê na hora: é a fila do lote
        assert (await enviar('danfe.pdf',DANFE)).json()['arquivos'][0]['estado']=='na_fila'
        await enviar('recibo.pdf',PDF)
        r=(await enviar('recibo-copia.pdf',PDF)).json()['arquivos'][0];assert (r['estado'],r['motivo'],r['nome'])==('duplicado','arquivo_repetido','recibo-copia.pdf')
        # Recusas de arquivo solto: nada gravado.
        r=await enviar('planilha.txt',b'nome;valor\n');assert (r.status_code,motivo(r))==(400,'tipo_nao_aceito')
        r=await enviar('outro.xml',b'<?xml version="1.0"?><qualquer/>');assert (r.status_code,motivo(r))==(400,'xml_nao_e_nfe')
        # ZIP: um XML repetido (duplicado), um PDF novo, um .txt, um caminho perigoso e uma entrada que é "bomba" de compressão.
        z=zipar([('pasta/repetido.xml',XML),('pasta/boleto2.pdf',P.pdf([(['Recibo do pagador','Valor R$ 10,00'],None)])),('leia.txt',b'x'),
                 ('../fora.pdf',DANFE),('bomba.pdf',b'%PDF-1.4\n'+b'0'*2_000_000)])
        r=await enviar('notas.zip',z);assert r.status_code==202,r.text
        itens={i['nome']:(i['estado'],i['motivo'],i['zip']) for i in r.json()['arquivos']}
        assert itens['repetido.xml']==('duplicado','arquivo_repetido','notas.zip') and itens['boleto2.pdf'][0]=='na_fila'
        assert itens['leia.txt'][:2]==('recusado','tipo_nao_aceito') and itens['fora.pdf'][:2]==('recusado','zip_caminho_invalido')
        assert itens['bomba.pdf'][:2]==('recusado','zip_taxa_de_compressao')
        r=await enviar('ruim.zip',b'PK\x03\x04lixo');assert (r.status_code,motivo(r))==(400,'zip_invalido')
        v=(await c.get(q,headers=G)).json()
        assert v['arquivos']['na_fila']==4 and v['arquivos']['duplicado']==2 and v['arquivos']['recusado']==3 and v['estimativa_s']==6
        # Cifrado no volume, como o ARQ1; a origem do anexo é o lote.
        a=await pool.fetchrow("SELECT * FROM anexos WHERE origem='lote' AND formato='pdf' ORDER BY criado_em LIMIT 1")
        assert a['referencia']==lid and a['modo']=='extrair' and b'%PDF' not in (pasta/a['caminho']).read_bytes()
        # Cede a vez: com documento de atendimento esperando, o lote não lê nada.
        await pool.execute("""INSERT INTO anexos(id,tenant_id,agente_id,origem,modo,nome,formato,tamanho_bytes,sha256,expira_em)
            VALUES ('anx_000000000000000000000001',$1,$2,'api','extrair','x.pdf','pdf',1,repeat('0',64),now()+interval '1 day')""",T,A)
        assert await L.ciclo(p,prazo_s=5)==0 and chamadas==[]
        await pool.execute("UPDATE anexos SET estado='falhou' WHERE id='anx_000000000000000000000001'")
        # Lê um por vez, na ordem de chegada.
        assert await L.ciclo(p,prazo_s=30)==4 and chamadas==['xml','pdf','pdf','pdf']
        v=(await c.get(q,headers=G)).json()
        assert (v['arquivos']['lido'],v['arquivos']['na_fila'],v['notas'],v['estado'])==(4,0,3,'aberto')
        # Notas: XML + DANFE juntos pela chave de acesso (o XML é a fonte); os outros dois PDFs sozinhos, sem chave de junção.
        n=(await c.get(q+'/notas',headers=G)).json()
        assert n['total']==3
        junta=next(x for x in n['itens'] if len(x['arquivos'])==2)
        assert junta['fonte']=='xml' and sorted(a['formato'] for a in junta['arquivos'])==['pdf','xml'] and junta['campos']['chave_acesso']['valor']
        assert junta['campos']['destinatario_documento']['valor']=='***.456.789-**'   # CPF mascarado
        assert all('sem_chave_de_juncao' in x['avisos'] for x in n['itens'] if x is not junta)
        lista=(await c.get(q+'/arquivos?estado=recusado',headers=G)).json()['itens'];assert len(lista)==3 and all(i['motivo'] for i in lista)
        assert len((await c.get(q+'/arquivos?limite=2',headers=G)).json()['itens'])==2
        # Fechar: não aceita mais arquivo; já lido tudo → concluido na hora.
        assert (await c.post(q+'/fechar',headers=SUP)).status_code==403
        r=await c.post(q+'/fechar',headers=G);assert r.status_code==202 and r.json()['estado']=='concluido'
        r=await enviar('tarde.pdf',DANFE);assert (r.status_code,motivo(r))==(409,'lote_fechado')
        assert (await c.post(q+'/fechar',headers=G)).status_code==409
        # Lote vazio não fecha.
        vazio=(await c.post(f'{base}/agentes/{A}/lotes',headers=G)).json()['lote_id']
        r=await c.post(f'{base}/lotes/{vazio}/fechar',headers=G);assert (r.status_code,motivo(r))==(409,'lote_vazio')
        assert [x['lote_id'] for x in (await c.get(f'{base}/lotes?agente_id={A}',headers=G)).json()['itens']]==[vazio,lid]
        # Apagar: arquivos e leituras saem; o lote some da lista.
        caminhos=[r['caminho'] for r in await pool.fetch("SELECT caminho FROM anexos WHERE referencia=$1",lid)]
        r=await c.delete(q,headers=G);assert r.status_code==200 and r.json()['estado']=='apagado'
        assert all(not (pasta/x).exists() for x in caminhos)
        assert await pool.fetchval("SELECT count(*) FROM anexos WHERE referencia IS NULL AND origem='lote' AND apagado_em IS NOT NULL")==4
        assert [x['lote_id'] for x in (await c.get(f'{base}/lotes',headers=G)).json()['itens']]==[vazio]
        acoes=[r['acao'] for r in await pool.fetch("SELECT acao FROM audit_log WHERE alvo=$1 ORDER BY id",lid)]
        assert acoes[0]=='lote.criado' and 'lote.fechado' in acoes and 'lote.concluido' in acoes and acoes[-1]=='lote.apagado'
    cenario(tmp_path,monkeypatch,corpo)


def test_lote_limites_e_capacidade(tmp_path,monkeypatch):
    async def corpo(pool,c,p,**_):
        from nucleo import lotes as L
        base=f'/admin/v1/tenants/{T}'
        motivo=lambda r: r.json()['erro']['detalhes'].get('motivo')
        lid=(await c.post(f'{base}/agentes/{A}/lotes',headers=G)).json()['lote_id']
        monkeypatch.setattr(L,'MAX_ARQUIVOS',2)
        zips=zipar([(f'n{i}.pdf',P.pdf([([f'Recibo {i}'],None)])) for i in range(3)])
        r=await c.post(f'{base}/lotes/{lid}/arquivos',files={'arquivo':('n.zip',zips,'application/zip')},headers=G)
        assert (r.status_code,motivo(r),r.json()['erro']['detalhes']['restam'])==(409,'limite_do_lote',2)
        assert await pool.fetchval('SELECT count(*) FROM lote_arquivos WHERE lote_id=$1',lid)==0   # tudo ou nada
        # Capacidade desligada no agente: não cria lote.
        await pool.execute("""UPDATE agentes SET capacidades='{"documentos":{"ativo":false}}' WHERE tenant_id=$1 AND id=$2""",T,A)
        r=await c.post(f'{base}/agentes/{A}/lotes',headers=G);assert (r.status_code,motivo(r))==(409,'capacidade_desligada')
        r=await c.post(f'{base}/agentes/nao-existe/lotes',headers=G);assert r.status_code==404
    cenario(tmp_path,monkeypatch,corpo)
