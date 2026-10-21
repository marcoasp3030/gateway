"""ARQ1-a1 (docs/ESPEC_ARQ1.md, seção 14): envio de documento por API (chave da empresa e chave de formulário), modos extrair e agente,
armazenamento cifrado, leitura atrasada e queda, retenção e LGPD, painel, pacote do agente e a Skill ler_documento.
O leitor de verdade roda em contêiner próprio e tem os testes dele (leitor/tests); aqui ele é simulado com o mesmo formato de resposta."""
import asyncio
import base64
import json
import os
import re
import pytest
from test_migrations import DSN
from test_fase4_pedidos import montar
from test_fila import banco_real,montar as montar_fila,limpar
import pdf_sintetico as P

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
T,A='bykoji','bykoji_atendimento'
ADMIN={'X-Role':'admin','X-Actor':'ana'}
PDF=P.pdf([(['Recibo do pagador','Beneficiario Empresa Exemplo Ltda','Vencimento 25/10/2026','Valor R$ 152,30'],[['Item','Valor'],['Mensalidade','152,30']]),
           ([],None)])


def leitor_simulado(chamadas,atraso=0,falhar=0):
    """Mesmo formato do leitor de verdade: texto entre parênteses dos operadores Tj do PDF sintético; XML → nfe."""
    estado={'falhas':falhar}
    async def ler(http,formato,dados):
        chamadas.append(formato)
        if atraso: await asyncio.sleep(atraso)
        if estado['falhas']>0:
            estado['falhas']-=1;raise RuntimeError('leitor fora do ar')
        if formato=='xml':
            return {'ok':True,'formato':'xml','nfe':{'chave':P.CHAVE,'modelo':'55','serie':'1','numero':'1234','emissao':'2026-10-20T10:00:00-03:00',
                    'emitente':{'documento':'11222333000181','nome':'Fornecedor Exemplo Ltda','uf':'SP'},'destinatario':{'documento':'12345678909','nome':'Pessoa Exemplo'},
                    'valor_total':'49.00','itens':[],'protocolo':{'numero':'135260000000001','status':'100','motivo':'Autorizado o uso da NF-e'}},
                    'paginas':[{'pagina':1,'tem_texto':True,'origem':'xml','texto':'Chave de acesso: '+P.CHAVE,'texto_cortado':False,'tabelas':[]}]}
        if b'/JavaScript' in dados: return {'ok':False,'motivo':'pdf_conteudo_ativo'}
        paginas=[]
        for n,bloco in enumerate(dados.split(b'/Type /Page ')[1:],1):
            pass
        conteudos=re.findall(rb'stream\n(.*?)\nendstream',dados,re.S)
        for n,c in enumerate(conteudos,1):
            textos=[t.decode('latin-1') for t in re.findall(rb'\((.*?)\) Tj T\*',c)]
            celulas=[t.decode('latin-1') for t in re.findall(rb'Td \((.*?)\) Tj ET',c)]
            tabelas=[{'linhas':[celulas[i:i+2] for i in range(0,len(celulas),2)]}] if celulas else []
            texto='\n'.join(textos)
            paginas.append({'pagina':n,'tem_texto':bool(texto),'origem':'texto' if texto else 'sem_texto','texto':texto,'texto_cortado':False,'tabelas':tabelas})
        return {'ok':True,'formato':'pdf','paginas':paginas}
    return ler


def cenario(tmp_path,monkeypatch,corpo,ligar=True,**leitor):
    from nucleo import documentos as D
    pasta=tmp_path/('arquivos_'+os.urandom(4).hex());pasta.mkdir()
    monkeypatch.setenv('ARQUIVOS_DIR',str(pasta))
    monkeypatch.setenv('ARQUIVOS_CHAVE_MESTRA',base64.urlsafe_b64encode(os.urandom(32)).decode())
    chamadas=[];monkeypatch.setattr(D,'ler_no_leitor',leitor_simulado(chamadas,**leitor))
    async def rodar(pool):
        app,c,r,p,pref=await montar_fila(pool,True,tmp_path)
        pacotes=[];roteiro=[]
        async def agente(http,repo,cofre,tenant,cfg,payload):
            pacotes.append(payload);return await roteiro.pop(0)(payload) if roteiro else {'resposta':['Recebi seu documento.'],'acao':'nenhuma'}
        p.chamar=agente
        try:
            async with c:
                conversa,ids,api,_=await montar(pool,c,app)
                if ligar:
                    await pool.execute("""UPDATE modelos_agente m SET capacidades='{"documentos":{"ativo":true}}' FROM agentes a
                        WHERE a.tenant_id=$1 AND a.id=$2 AND m.id=a.modelo_id AND m.versao=a.modelo_versao""",T,A)
                r1=await c.post(f'/admin/v1/tenants/{T}/api-keys',json={'nome':'n8n','escopos':['documentos:enviar']})
                chave_empresa=next(v for k,v in r1.json().items() if k=='chave')
                r2=await c.post(f'/admin/v1/tenants/{T}/api-keys',json={'nome':'skyone-docs','escopos':['ferramentas:documentos'],'agente_id':A})
                chave_skill=next(v for k,v in r2.json().items() if k=='chave')
                from nucleo.credenciais import Cofre
                await pool.execute("INSERT INTO tenant_webhooks_saida(id,tenant_id,url,segredo_enc,eventos,ativo) VALUES ('wh_teste',$1,'https://exemplo.invalid/retorno',$2,$3,true)",
                                   T,app.state.cofre.cifrar(T,'segredo-de-teste'),['documento.pronto','documento.falhou','conversa.resposta','documento.apagado'])
                async def enviar(arquivo=PDF,nome='boleto.pdf',chave=None,form=None,headers=None,**campos):
                    h={'Authorization':''};h.update({'X-Form-Key':form} if form else {'X-API-Key':chave or chave_empresa});h.update(headers or {})
                    if not form: campos.setdefault('agente_id',A)
                    return await c.post('/api/v1/documentos',files={'arquivo':(nome,arquivo,'application/octet-stream')},data={k:str(v) for k,v in campos.items()},headers=h)
                async def skill(anexo,conversa_id,**params):
                    return await c.get(f'/api/v1/documentos/{anexo}/conteudo',params={'conversation_id':conversa_id,**params},headers={'X-API-Key':chave_skill,'Authorization':''})
                async def eventos(nome):
                    return [json.loads(x['corpo'])['dados'] for x in await pool.fetch('SELECT corpo FROM webhook_entregas WHERE tenant_id=$1 AND evento=$2 ORDER BY id',T,nome)]
                await corpo(pool=pool,c=c,p=p,app=app,api=api,enviar=enviar,skill=skill,eventos=eventos,chamadas=chamadas,pacotes=pacotes,roteiro=roteiro,
                            pasta=pasta,chave_empresa=chave_empresa,D=D)
                for t in list(getattr(app.state,'documentos_tarefas',())): await t
        finally:
            await limpar(r,pref)
    banco_real(rodar)


def test_modo_extrair_sincrono_cifrado_consulta_e_webhook(tmp_path,monkeypatch):
    async def corpo(pool,c,enviar,eventos,pasta,D,**_):
        r=await enviar(referencia='pedido-77');assert r.status_code==200,r.text
        d=r.json()
        assert (d['estado'],d['modo'],d['formato'],d['paginas'],d['referencia'],d['duplicado'],d['nome'])==('pronto','extrair','pdf',2,'pedido-77',False,'boleto.pdf')
        p1,p2=d['conteudo']['paginas']
        assert 'Beneficiario Empresa Exemplo Ltda' in p1['texto'] and p1['tabelas'][0]['id']=='tab_1_1' and p1['tabelas'][0]['linhas'][1]==['Mensalidade','152,30']
        assert (p2['origem'],p2['texto'])==('sem_texto','') and d['verificacao_pagamento'] is None
        # Cifrado: nem o arquivo no volume nem o resultado no banco têm o conteúdo em claro; o caminho não tem nome nem empresa.
        a=await pool.fetchrow('SELECT * FROM anexos WHERE id=$1',d['anexo_id'])
        bruto=(pasta/a['caminho']).read_bytes()
        assert b'%PDF' not in bruto and b'Beneficiario' not in bruto and b'Beneficiario' not in bytes(a['resultado']) and T not in a['caminho'] and 'boleto' not in a['caminho']
        assert a['sha256']==__import__('hashlib').sha256(PDF).hexdigest() and a['expira_em']>a['criado_em']
        # Consulta: o mesmo documento; uma página por vez.
        h={'X-API-Key':_['chave_empresa'],'Authorization':''}
        g=(await c.get(f'/api/v1/documentos/{d["anexo_id"]}',headers=h)).json();assert g['conteudo']['paginas'][0]['texto']==p1['texto']
        assert (await c.get(f'/api/v1/documentos/{d["anexo_id"]}',params={'pagina':1},headers=h)).json()['texto']==p1['texto']
        assert (await c.get(f'/api/v1/documentos/{d["anexo_id"]}',params={'pagina':9},headers=h)).status_code==404
        # Webhook de retorno: só ids e estado.
        [e]=await eventos('documento.pronto');assert e=={'anexo_id':d['anexo_id'],'estado':'pronto','motivo':None,'modo':'extrair','referencia':'pedido-77',
                                                         'agente_id':A,'conversation_id':None,'formato':'pdf','paginas':2,'tipo_documento':None}
        # Idempotência: o mesmo envio repetido devolve o mesmo documento, sem ler de novo.
        r1=await enviar(idempotencia='envio-1');r2=await enviar(idempotencia='envio-1')
        assert r1.json()['anexo_id']==r2.json()['anexo_id'] and r2.json()['duplicado'] is True and len(_['chamadas'])==2
    cenario(tmp_path,monkeypatch,corpo)


def test_recusas_capacidade_tipo_tamanho_navegador_e_escopo(tmp_path,monkeypatch):
    async def corpo(pool,c,enviar,api,**_):
        motivo=lambda r: r.json()['erro']['detalhes'].get('motivo')
        r=await enviar(arquivo=b'MZ\x90\x00executavel',nome='boleto.pdf');assert (r.status_code,motivo(r))==(400,'tipo_nao_aceito')
        r=await enviar(arquivo=b'');assert (r.status_code,motivo(r))==(400,'vazio')
        r=await enviar(arquivo=P.pdf([(['x'],None)],'/OpenAction << /S /JavaScript /JS (x) >>'));assert (r.json()['estado'],r.json()['motivo'])==('rejeitado','pdf_conteudo_ativo')
        # Agente restringe: tamanho menor e só XML.
        r=await c.put(f'/admin/v1/tenants/{T}/agentes/{A}/capacidades/documentos',json={'max_bytes':1024},headers=ADMIN);assert r.status_code==200,r.text
        assert r.json()['documentos']['max_bytes']==1024 and len(PDF)>1024
        r=await enviar();assert (r.status_code,motivo(r),r.json()['erro']['detalhes']['max_bytes'])==(413,'tamanho',1024)
        assert (await c.put(f'/admin/v1/tenants/{T}/agentes/{A}/capacidades/documentos',json={'max_bytes':99999999},headers=ADMIN)).status_code==400
        assert (await c.put(f'/admin/v1/tenants/{T}/agentes/{A}/capacidades/documentos',json={'ativo':False},headers={'X-Role':'membro'})).status_code==403
        await c.put(f'/admin/v1/tenants/{T}/agentes/{A}/capacidades/documentos',json={'ativo':False},headers=ADMIN)
        r=await enviar();assert (r.status_code,motivo(r))==(409,'capacidade_desligada')
        await c.put(f'/admin/v1/tenants/{T}/agentes/{A}/capacidades/documentos',json={},headers=ADMIN)
        assert (await enviar()).status_code==200
        # Chave da empresa vinda de navegador: recusada.
        r=await enviar(headers={'Origin':'https://site.exemplo'});assert (r.status_code,motivo(r))==(403,'chave_da_empresa_no_navegador')
        r=await enviar(headers={'Sec-Fetch-Mode':'cors'});assert r.status_code==403
        # Chave sem o escopo, chave inválida, duas credenciais.
        sem=next(v for k,v in (await c.post(f'/admin/v1/tenants/{T}/api-keys',json={'nome':'x','escopos':['conversar']})).json().items() if k=='chave')
        assert (await enviar(chave=sem)).status_code==403 and (await enviar(chave='nada')).status_code==401
        assert (await enviar(form='algo')).status_code==401
        # Modo agente exige contato; agente de outra empresa / inexistente.
        r=await enviar(modo='agente');assert (r.status_code,r.json()['erro']['detalhes']['campo'])==(400,'contato')
        r=await enviar(agente_id='nao_existe');assert r.status_code==404
        r=await enviar(modo='outro');assert r.status_code==400
    cenario(tmp_path,monkeypatch,corpo)


def test_chave_de_formulario_origem_limite_modos_e_revogacao(tmp_path,monkeypatch):
    async def corpo(pool,c,enviar,skill,**_):
        assert (await c.post(f'/admin/v1/tenants/{T}/chaves-formulario',json={'nome':'site','agente_id':A},headers={'X-Role':'membro'})).status_code==403
        for ruim,campo in (({'nome':'site','agente_id':A,'origens':['site.exemplo']},'origens'),({'nome':'site','agente_id':A,'modos':['tudo']},'modos'),
                           ({'nome':'site','agente_id':A,'limite_por_minuto':0},'limite_por_minuto'),({'nome':'site'},'agente_id')):
            r=await c.post(f'/admin/v1/tenants/{T}/chaves-formulario',json=ruim,headers=ADMIN);assert (r.status_code,r.json()['erro']['detalhes']['campo'])==(400,campo),ruim
        r=await c.post(f'/admin/v1/tenants/{T}/chaves-formulario',json={'nome':'Site de cobrança','agente_id':A,'limite_por_minuto':3,'tipos':['pdf'],
                       'modos':['agente','extrair'],'origens':['https://site.exemplo']},headers=ADMIN)
        assert r.status_code==201,r.text
        k=r.json();form=k['chave'];assert form.startswith('gwf_') and k['modos']==['agente','extrair'] and 'hash' not in k
        assert [x['id'] for x in (await c.get(f'/admin/v1/tenants/{T}/chaves-formulario')).json()['itens']]==[k['id']]
        # Sem modo: o primeiro da chave (agente), que exige o contato.
        r=await enviar(form=form);assert r.json()['erro']['detalhes']['campo']=='contato'
        r=await enviar(form=form,modo='extrair',headers={'Origin':'https://site.exemplo'});assert r.status_code==200,r.text
        assert r.headers['access-control-allow-origin']=='https://site.exemplo' and r.json()['conteudo']['paginas']
        anexo=r.json()['anexo_id']
        r=await enviar(form=form,modo='extrair',headers={'Origin':'https://outro.exemplo'});assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(403,'origem_nao_permitida')
        # Preflight do navegador.
        r=await c.options('/api/v1/documentos',headers={'Origin':'https://site.exemplo','Access-Control-Request-Method':'POST','Authorization':''})
        assert r.status_code==204 and 'X-Form-Key' in r.headers['access-control-allow-headers']
        # Tipo fora da lista da chave; outro agente; consulta só com o estado.
        r=await enviar(form=form,modo='extrair',arquivo=P.NFE_XML.format(chave=P.CHAVE).encode(),nome='nota.xml');assert r.json()['erro']['detalhes']['motivo']=='tipo_nao_aceito'
        r=await enviar(form=form,modo='extrair',agente_id='outro');assert r.status_code==403
        g=await c.get(f'/api/v1/documentos/{anexo}',headers={'X-Form-Key':form,'Authorization':''});assert g.status_code==200 and 'conteudo' not in g.json() and g.json()['estado']=='pronto'
        # Limite por minuto (3): os envios aceitos contam.
        assert (await enviar(form=form,modo='extrair')).status_code==200
        assert (await enviar(form=form,modo='extrair')).status_code==200
        r=await enviar(form=form,modo='extrair');assert (r.status_code,r.headers.get('retry-after'))==(429,'60')
        # Chave de formulário não serve para Skill nem para outra rota da API.
        assert (await c.get(f'/api/v1/documentos/{anexo}/conteudo',params={'conversation_id':'x'},headers={'X-API-Key':form,'Authorization':''})).status_code==403   # sem o escopo
        assert (await c.post('/api/v1/conversas/mensagens',json={'canal_id':'x','identificador':'1','texto':'oi'},headers={'X-API-Key':form,'Authorization':''})).status_code in (401,403)
        # Revogada no painel: deixa de funcionar na hora.
        r=await c.delete(f'/admin/v1/tenants/{T}/chaves-formulario/{k["id"]}',headers=ADMIN);assert r.json()['revogada_por']=='ana'
        assert (await enviar(form=form,modo='extrair')).status_code==401
    cenario(tmp_path,monkeypatch,corpo)


def test_modo_agente_pacote_resposta_no_webhook_e_skill(tmp_path,monkeypatch):
    async def corpo(pool,c,p,enviar,skill,eventos,pacotes,roteiro,**_):
        r=await enviar(modo='agente',contato='5511900000301',nome_contato='Cliente Teste',mensagem='Segue o boleto de outubro',referencia='form-9')
        assert r.status_code==200,r.text
        d=r.json();conv=d['conversation_id'];assert conv and d['estado']=='pronto' and d['conteudo'] is None
        a=await pool.fetchrow('SELECT * FROM anexos WHERE id=$1',d['anexo_id'])
        assert a['atendimento_id'] and a['mensagem_id']
        # O agente recebe o anexo no pacote (resumo compacto, nunca o texto inteiro) e na mensagem do lote.
        async def le(payload):
            assert (await skill(d['anexo_id'],payload['conversation_id'])).status_code==200
            return {'resposta':['Recebi o boleto. A equipe vai conferir.'],'acao':'nenhuma'}
        roteiro[:]=[le]
        assert await p.processar_duravel(T,conv)=='concluido'
        pk=pacotes[-1]
        assert pk['recursos']['documentos']=={'ler':True,'tipos':['pdf','xml','jpg','png','webp']}
        [anexo]=pk['anexos']
        assert (anexo['anexo_id'],anexo['estado'],anexo['formato'],anexo['paginas'],anexo['origem'],anexo['tipo_documento'],anexo['tabelas'])==(d['anexo_id'],'pronto','pdf',2,'misto','outro',1)
        assert anexo['trecho_inicial'].startswith('Recibo do pagador') and len(anexo['trecho_inicial'])<=301 and anexo['campos']=={} and anexo['verificacao_pagamento'] is None
        assert len(json.dumps(pk['anexos']))<=3*1024
        [m]=pk['mensagens_do_lote'];assert (m['tipo'],m['anexo_id'],m['texto'])==('documento',d['anexo_id'],'[anexo: boleto.pdf]\nSegue o boleto de outubro')
        # A resposta do agente no canal api vai pelo webhook de saída.
        [resp]=await eventos('conversa.resposta')
        assert (resp['conversation_id'],resp['contato'],resp['texto'],resp['parte'])==(conv,'5511900000301','Recebi o boleto. A equipe vai conferir.',1)
        # Skill ler_documento: estrutura, página, busca, tabela; outra conversa não vê.
        s=(await skill(d['anexo_id'],conv)).json()
        assert [x['pagina'] for x in s['estrutura']]==[1,2] and s['estrutura'][0]['tabelas'][0]['id']=='tab_1_1' and s['campos']=={}
        s=(await skill(d['anexo_id'],conv,pagina=1)).json();assert s['texto'].startswith('Recibo') and s['parcial'] is False
        s=(await skill(d['anexo_id'],conv,busca='beneficiário')).json();assert s['trechos'][0]['pagina']==1 and 'Empresa Exemplo' in s['trechos'][0]['trecho']
        s=(await skill(d['anexo_id'],conv,tabela='tab_1_1')).json();assert s['linhas'][1]==['Mensalidade','152,30'] and s['total_linhas']==2
        assert (await skill(d['anexo_id'],conv,tabela='tab_9_1')).status_code==404
        outra=(await enviar(modo='agente',contato='5511900000302')).json()['conversation_id']
        assert (await skill(d['anexo_id'],outra)).status_code==404
        reg=await pool.fetch("SELECT skill,status FROM skill_registro WHERE tenant_id=$1 AND skill='ler_documento' ORDER BY id",T)
        assert len(reg)>=5 and reg[0]['status']==200
    cenario(tmp_path,monkeypatch,corpo)


def test_leitura_atrasada_queda_e_leitor_fora_do_ar(tmp_path,monkeypatch):
    async def corpo(pool,c,p,app,enviar,eventos,D,**_):
        # Atrasada: 202 com anexo_id e onde consultar; termina depois e avisa pelo webhook.
        r=await enviar(aguardar_s=0);assert r.status_code==202 and r.json()['estado']=='processando' and r.json()['consultar_em'].endswith(r.json()['anexo_id'])
        for t in list(app.state.documentos_tarefas): await t
        h={'X-API-Key':_['chave_empresa'],'Authorization':''}
        assert (await c.get(f'/api/v1/documentos/{r.json()["anexo_id"]}',headers=h)).json()['estado']=='pronto'
        # Queda no meio (posse vencida): a varredura retoma.
        await pool.execute("UPDATE anexos SET estado='processando',processando_ate=now()-interval '1 minute',resultado=NULL WHERE id=$1",r.json()['anexo_id'])
        await D.varrer(app.state);assert await pool.fetchval('SELECT estado FROM anexos WHERE id=$1',r.json()['anexo_id'])=='pronto'
    cenario(tmp_path,monkeypatch,corpo,atraso=0.5)

    async def fora(pool,c,app,enviar,eventos,D,chamadas,**_):
        r=await enviar(aguardar_s=1);aid=r.json()['anexo_id'];assert r.json()['estado']=='processando'
        for t in list(app.state.documentos_tarefas): await t
        for _n in range(3):
            await pool.execute('UPDATE anexos SET processando_ate=now()-interval \'1 second\' WHERE id=$1',aid)
            await D.varrer(app.state)
        assert tuple(await pool.fetchrow('SELECT estado,motivo FROM anexos WHERE id=$1',aid))==('falhou','leitor_indisponivel') and len(chamadas)==3
        [e]=await eventos('documento.falhou');assert (e['anexo_id'],e['motivo'])==(aid,'leitor_indisponivel')
    cenario(tmp_path,monkeypatch,fora,falhar=5)


def test_imagem_xml_painel_retencao_e_lgpd(tmp_path,monkeypatch):
    async def corpo(pool,c,p,app,enviar,eventos,pasta,D,**_):
        # Foto: guardada, sem leitura até o OCR (ARQ1-a2).
        jpg=b'\xff\xd8\xff\xe0'+b'\x00'*200
        r=await enviar(arquivo=jpg,nome='foto do boleto.jpeg');assert (r.json()['estado'],r.json()['motivo'],r.json()['nome'])==('sem_texto','imagem_sem_ocr','foto do boleto.jpg')
        # XML da NF-e: campos lidos do XML, marcados lido_do_texto (a validação por DV é do ARQ1-a3); CPF mascarado no pacote.
        r=await enviar(arquivo=P.NFE_XML.format(chave=P.CHAVE).encode(),nome='nota.xml',modo='agente',contato='5511900000311')
        d=r.json();assert (d['estado'],d['tipo_documento'])==('pronto','nfe')
        await p.processar_duravel(T,d['conversation_id'])
        [anexo]=_['pacotes'][-1]['anexos']
        assert anexo['campos']['chave_acesso']=={'valor':P.CHAVE,'confianca':'lido_do_texto'} and anexo['campos']['destinatario_documento']['valor']=='***.456.789-**'
        assert anexo['campos']['valor_total']['valor']=='49.00' and anexo['tipo_documento']=='nfe'
        # Painel: lista, detalhe com todos os campos, texto, download do original, apagar (admin).
        base=f'/admin/v1/tenants/{T}/anexos'
        lista=(await c.get(base)).json()['itens'];assert {x['formato'] for x in lista}=={'jpg','xml'}
        det=(await c.get(f'{base}/{d["anexo_id"]}')).json();assert det['conteudo']['campos']['destinatario_documento']['valor']=='12345678909'
        assert (await c.get(f'{base}/{d["anexo_id"]}/texto',params={'pagina':1})).json()['texto'].startswith('Chave de acesso')
        orig=await c.get(f'{base}/{d["anexo_id"]}/original')
        assert orig.content==P.NFE_XML.format(chave=P.CHAVE).encode() and orig.headers['content-disposition'].startswith('attachment') and orig.headers['x-content-type-options']=='nosniff'
        foto=lista[-1]['id'] if lista[-1]['formato']=='jpg' else lista[0]['id']
        assert (await c.delete(f'{base}/{foto}',headers={'X-Role':'membro'})).status_code==403
        r=await c.delete(f'{base}/{foto}',headers=ADMIN);assert r.json()['estado']=='apagado' and r.json()['apagado_em']
        assert (await c.get(f'{base}/{foto}/original')).status_code==404 and len(list(pasta.rglob('anx_*')))==1
        # Retenção: vencido → expirado e arquivo apagado.
        await pool.execute("UPDATE anexos SET expira_em=now()-interval '1 second' WHERE id=$1",d['anexo_id'])
        await D.varrer(app.state)
        a=await pool.fetchrow('SELECT * FROM anexos WHERE id=$1',d['anexo_id'])
        assert (a['estado'],a['resultado'],a['caminho'],a['referencia'])==('expirado',None,None,None) and not list(pasta.rglob('anx_*'))
        # LGPD: anonimizar o contato apaga os documentos da conversa.
        r=await enviar(modo='agente',contato='5511900000312');aid=r.json()['anexo_id']
        cid=await pool.fetchval('SELECT contato_id FROM conversas WHERE conversation_id=$1',r.json()['conversation_id'])
        assert (await c.request('DELETE',f'/admin/v1/tenants/{T}/contatos/{cid}',json={'confirmacao':'5511900000312'},headers={'X-Role':'plataforma'})).status_code==200
        assert await pool.fetchval('SELECT estado FROM anexos WHERE id=$1',aid)=='apagado'
    cenario(tmp_path,monkeypatch,corpo)


def test_capacidade_no_modelo_e_pacote_desligado_intacto(tmp_path,monkeypatch):
    async def corpo(pool,c,p,enviar,pacotes,**_):
        h={'X-Role':'plataforma'}
        r=await c.post('/admin/v1/modelos-agente',json={'id':'docs_teste','nome':'Docs','capacidades':{'documentos':{'ativo':True,'tipos':['pdf'],'retencao_dias':10}}},headers=h)
        assert r.status_code==201,r.text
        assert r.json()['capacidades']=={'documentos':{'ativo':True,'tipos':['pdf'],'retencao_dias':10}}
        for ruim in ({'documentos':{'ativo':'sim'}},{'documentos':{'tipos':['exe']}},{'outra':{}},{'documentos':{'retencao_dias':400}}):
            assert (await c.post('/admin/v1/modelos-agente',json={'id':'x_'+str(len(str(ruim))),'nome':'X','capacidades':ruim},headers=h)).status_code==400,ruim
        # Capacidade desligada (padrão do delivery): o pacote não ganha anexos nem recursos.documentos.
        await pool.execute("UPDATE modelos_agente SET capacidades='{}' WHERE id IN (SELECT modelo_id FROM agentes WHERE tenant_id=$1 AND id=$2)",T,A)
        canal=await pool.fetchval("SELECT id FROM tenant_canais WHERE tenant_id=$1 AND nome='API'",T)
        r=await c.post(f'/admin/v1/tenants/{T}/agentes/{A}/simular',json={'canal_id':canal,'identificador':'5511900000321','texto':'oi','message_id':'m-321'})
        await p.processar_duravel(T,r.json()['conversation_id'])
        assert 'anexos' not in pacotes[-1] and 'documentos' not in pacotes[-1]['recursos']
        assert (await c.get(f'/admin/v1/tenants/{T}/agentes/{A}/capacidades')).json()['documentos']['ativo'] is False
    cenario(tmp_path,monkeypatch,corpo,ligar=True)
