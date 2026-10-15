"""SOL1: avisar a equipe sem parar o atendimento (acao notificar_equipe), proteção contra transferência que abandona a venda,
toques contraditórios no mesmo lote e rotas do painel."""
import json
import httpx,pytest
from agentes import skyone
from nucleo import solicitacoes as S,atendimento as A
from modulos.pedidos import avisos
from test_migrations import banco,migrar,DSN
from test_admin import preparar,criar
from test_fila import banco_real,montar as montar_fila,limpar
from test_fase4_pedidos import montar as montar_pedidos
from test_skyone_adaptador import preparar_agente

T,AG='bykoji','bykoji_atendimento'
B=f'/admin/v1/tenants/{T}/solicitacoes'
EVENTO={'tipo':'evento','data':'25 de dezembro','pessoas':50}


def test_validacao_da_solicitacao_e_catalogo():
    v=S.validar
    assert v({'tipo':' Evento ','data':'  25 de   dezembro ','pessoas':50,'local':'Salão do prédio','observacao':'Só queria saber.','telefone':'5511999990000','cpf':'x'})=={
        'tipo':'evento','data':'25 de dezembro','pessoas':50,'local':'Salão do prédio','observacao':'Só queria saber.'}
    # Campo inválido é ignorado, sem erro; tipo fora do formato vira "outro" (o tipo fora da lista do agente vira "outro" no registro,
    # MULTI1-a); número em texto é aceito; limites de tamanho cortam.
    assert v({'tipo':'churrasco','data':5,'pessoas':'cinquenta','local':['x'],'observacao':None})=={'tipo':'churrasco'}
    assert v({'tipo':'Churrasco de 50 pessoas!'})=={'tipo':'outro'}
    assert v({'pessoas':'50'})=={'tipo':'outro','pessoas':50} and v({'pessoas':0})=={'tipo':'outro'} and v({'pessoas':100001})=={'tipo':'outro'} and v({'pessoas':True})=={'tipo':'outro'}
    assert len(v({'data':'x'*200})['data'])==80 and len(v({'local':'x'*500})['local'])==160 and len(v({'observacao':'x'*900})['observacao'])==500
    assert v({'observacao':'linha 1\nlinha 2\x00'})['observacao']=='linha 1 linha 2' and v({})=={'tipo':'outro'}
    assert v(None) is None and v('evento') is None and v([1]) is None
    assert [v({'tipo':t})['tipo'] for t in S.TIPOS]==list(S.TIPOS)==['evento','catering','encomenda','orcamento','parceria','outro']
    # Motivo: do catálogo, ou "fora_do_escopo" guardando o texto recebido. catering entrou no catálogo, ligado por padrão.
    assert S.motivo_do_catalogo('Evento_ou_encomenda ')==('evento_ou_encomenda',None) and S.motivo_do_catalogo('catering')==('catering',None)
    assert S.motivo_do_catalogo('cliente quer orçamento')==('fora_do_escopo','cliente quer orçamento') and S.motivo_do_catalogo(None)==('fora_do_escopo',None)
    assert 'catering' in A.MOTIVOS_TRANSFERENCIA and 'catering' in A.resolver_perfil()[0]['motivos_transferencia']
    # Alerta: telefone inteiro para a equipe; mascarado quando o destino é grupo externo; sem texto de conversa.
    s={'numero':'S-0001','tipo':'evento','dados':{'data':'25 de dezembro','pessoas':50,'observacao':'Só queria saber.'}}
    t=S.texto_do_alerta(s,'Jussara Santos','5511942717046',False,'https://painel.exemplo.com')
    assert t.split('\n')==['📋 SOLICITAÇÃO PARA A EQUIPE S-0001 — evento','Cliente: Jussara Santos — +55 11 94271-7046','Data: 25 de dezembro · Pessoas: 50','Obs.: Só queria saber.','O bot continua atendendo esta conversa.','Painel: https://painel.exemplo.com']
    m=S.texto_do_alerta(s,'Jussara Santos','5511942717046',True,None);assert '5511****7046' in m and '94271-7046' not in m and 'Painel' not in m
    assert S.texto_do_alerta({'numero':'S-0002','tipo':'outro','dados':{}},None,None,False,None,True).split('\n')[:2]==['🔄 SOLICITAÇÃO ATUALIZADA S-0002 — outro','Cliente: (sem nome) — (sem número)']


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_retorno_do_agente_aceita_notificar_equipe_e_o_atual_continua_igual(monkeypatch):
    async def livre(url,dominios): pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',livre)
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'Oi',cfg)
        base={'status':'success','tipo':'text','request_id':body['request_id'],'conversation_id':body['conversation_id']}
        async def retorno(**campos):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda q: httpx.Response(200,json={**base,**campos}))) as client:
                return await skyone.chamar(client,repo,cofre,'bykoji',cfg,body)
        # Skyone de hoje (sem a ação nova e sem solicitacao): nada muda.
        assert await retorno(resposta=['ok'],acao='nenhuma')=={'resposta':['ok'],'acao':'nenhuma'}
        assert await retorno(resposta=['Vou transferir.'],acao='transferir_humano',motivo='pediu_humano')=={'resposta':['Vou transferir.'],'acao':'transferir_humano','motivo':'pediu_humano'}
        # Ação nova: com solicitação validada, botões permitidos e resposta opcional.
        r=await retorno(resposta=['Já avisei a equipe.'],acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao={**EVENTO,'cpf':'x','local':7},
                        botoes={'opcoes':[{'id':'finalizar_pedido','titulo':'Finalizar pedido'}]})
        assert r=={'resposta':['Já avisei a equipe.'],'acao':'notificar_equipe','motivo':'evento_ou_encomenda','solicitacao':EVENTO,'botoes':{'opcoes':[{'id':'finalizar_pedido','titulo':'Finalizar pedido'}]}}
        assert await retorno(resposta=[],acao='notificar_equipe',motivo='catering')=={'resposta':[],'acao':'notificar_equipe','motivo':'catering'}
        # solicitacao em formato errado é ignorada; em ação que não a usa, também. Na transferência ela é guardada (serve à conversão).
        assert 'solicitacao' not in await retorno(resposta=['ok'],acao='notificar_equipe',motivo='catering',solicitacao='evento para 50')
        assert 'solicitacao' not in await retorno(resposta=['ok'],acao='nenhuma',solicitacao=EVENTO)
        assert (await retorno(resposta=['ok'],acao='transferir_humano',motivo='evento_ou_encomenda',solicitacao=EVENTO))['solicitacao']==EVENTO
        # Pacote: com o recurso desligado (padrão) não há bloco de solicitações; ligado, vem a lista (vazia sem nenhuma).
        assert 'solicitacoes' not in body['contexto_modulos']
        assert (await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'Oi',{**cfg,'notificar_equipe':'on'}))['contexto_modulos']['solicitacoes']==[]
    banco(cenario)


@pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
def test_toques_contraditorios_no_mesmo_lote():
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn);conv='bykoji:canal:x'
        async def bot(pid,opcoes,forma='botao'):
            await conn.execute("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,message_id,meta,processada_em)
                VALUES ('bykoji',$1,'saida','bot','pergunta',$2,$3::jsonb,now())""",conv,f'canal:{pid}',json.dumps({'botoes':{'opcoes':[{'id':o,'titulo':o.title()} for o in opcoes],'forma':forma,'motivo':None}}))
        async def cliente(texto,clique=None,origem='M1',tipo='botao'):
            meta={'interacao':{'tipo':tipo,'id':clique,'mensagem':origem}} if clique else None
            return await conn.fetchrow("""INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,meta)
                VALUES ('bykoji',$1,'entrada','cliente',$2,$3::jsonb) RETURNING id,conteudo,criado_em,enviada_em,tipo,meta""",conv,texto,json.dumps(meta) if meta else None)
        async def pacote(*lote): return await skyone.pacote(repo,'bykoji',conv,lote[0]['id'],'\n'.join(i['conteudo'] for i in lote),cfg,lote=list(lote))
        resumo=lambda p: [(m['texto'],(m.get('interacao') or {}).get('id'),(m.get('interacao') or {}).get('substituiu')) for m in p['mensagens_do_lote']]
        await bot('M1',['entrega','retirada','tanto_faz']);await bot('M2',['sim','nao']);await bot('L1',['morumbi','patio_guedala'],'lista')
        # Entrega e depois Retirada na mesma mensagem: fica só Retirada, com o que ela substituiu.
        e=await cliente('Entrega','entrega');r=await cliente('Retirada','retirada');p=await pacote(e,r)
        assert resumo(p)==[('Retirada','retirada',['entrega'])] and p['message']=='Retirada' and p['ultima_interacao']['id']=='retirada'
        i=p['mensagens_do_lote'][0]['interacao'];assert set(i)=={'tipo','id','atual','substituiu'} and (i['tipo'],i['id'],i['substituiu'])==('botao','retirada',['entrega'])
        # Três toques na mesma mensagem: fica o último; os dois anteriores, na ordem, em substituiu.
        t=await cliente('Tanto_Faz','tanto_faz');assert resumo(await pacote(e,r,t))==[('Tanto_Faz','tanto_faz',['entrega','retirada'])]
        # O mesmo botão duas vezes: fica um.
        e2=await cliente('Entrega','entrega');assert resumo(await pacote(e,e2))==[('Entrega','entrega',['entrega'])]
        # Cliques em mensagens diferentes: ficam os dois, sem substituiu.
        s=await cliente('Sim','sim','M2');assert resumo(await pacote(r,s))==[('Retirada','retirada',None),('Sim','sim',None)]
        # Clique + texto digitado: fica tudo, na ordem; a mistura com toque repetido só tira o toque antigo.
        x=await cliente('mas sem wasabi');assert resumo(await pacote(r,x))==[('Retirada','retirada',None),('mas sem wasabi',None,None)]
        p=await pacote(e,x,r,s);assert resumo(p)==[('mas sem wasabi',None,None),('Retirada','retirada',['entrega']),('Sim','sim',None)] and p['message']=='mas sem wasabi\nRetirada\nSim'
        # Lista: o mesmo comportamento.
        a=await cliente('Morumbi','morumbi','L1','lista');b=await cliente('Pátio Guedala','patio_guedala','L1','lista')
        assert resumo(await pacote(a,b))==[('Pátio Guedala','patio_guedala',['morumbi'])]
        # Toque que o gateway não reconhece como clique (mensagem de origem desconhecida) é texto comum: nada é tirado.
        u=await cliente('Entrega','entrega','NAO_EXISTE');v=await cliente('Retirada','retirada','NAO_EXISTE');assert resumo(await pacote(u,v))==[('Entrega',None,None),('Retirada',None,None)]
        # As mensagens descartadas do lote continuam gravadas (histórico e painel).
        assert await conn.fetchval("SELECT count(*) FROM mensagens WHERE conversation_id=$1 AND direcao='entrada' AND conteudo='Entrega'",conv)==3
    banco(cenario)


def test_notificar_equipe_nao_pausa_protecao_e_painel(tmp_path):
    async def cenario(pool):
        app,c,r,p,pref=await montar_fila(pool,True,tmp_path)
        try:
            async with c:
                conversa,ids,api,_=await montar_pedidos(pool,c,app)
                async def modo(**campos): assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{AG}',json=campos)).status_code==200,campos
                async def chegar(conv,texto): await pool.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ($1,$2,'entrada','cliente',$3)",T,conv,texto)
                saidas=lambda conv: pool.fetch("SELECT conteudo FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida' ORDER BY id",T,conv)
                estado=lambda conv: pool.fetchrow("SELECT bot_pausado_ate>now() AS pausada,aguardando_atendente_desde,aguardando_motivo FROM conversas WHERE conversation_id=$1",conv)
                turno=lambda conv: pool.fetchrow("SELECT acao,acao_original,status FROM turnos WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1",conv)
                sols=lambda conv: pool.fetch("SELECT * FROM solicitacoes WHERE conversation_id=$1 ORDER BY id",conv)
                alertas=lambda: pool.fetch("SELECT referencia,texto,destino,status FROM avisos_internos WHERE tipo='solicitacao' ORDER BY id")
                pacotes=[];roteiro=[]
                async def agente(http,repo,cofre,tenant,cfg,payload):
                    pacotes.append(payload);return await roteiro.pop(0)(payload)
                p.chamar=agente
                def fala(*textos,antes=None,**campos):
                    async def passo(payload):
                        if antes: await antes(payload)
                        return {'resposta':list(textos),'acao':'nenhuma',**campos}
                    return passo
                async def rodar(conv,*passos): roteiro[:]=list(passos);assert await p.processar_duravel(T,conv)=='concluido'
                async def item(conv): assert (await api('POST',f'/carrinho/{conv}/itens',json={'item_id':ids['GUE-0001'],'quantidade':1})).status_code==200
                assert (await c.patch(f'/admin/v1/tenants/{T}',json={'whatsapp_alerta_transbordo':'5511900000060'})).status_code==200
                cfg=await app.state.configuracoes.efetiva(T,AG);assert (cfg['notificar_equipe'],cfg['origem_config']['notificar_equipe'],cfg['transbordo_destino_externo'])==('off','plataforma',False)

                # 0) Desligado (padrão): a ação nova é tratada como "nenhuma" (o balão sai, nada é gravado nem avisado) e a proteção não atua.
                c0=await conversa('n0');await item(c0)
                await rodar(c0,fala('Avisei a equipe.',acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao=EVENTO))
                assert [m['conteudo'] for m in await saidas(c0)]==['Avisei a equipe.'] and await sols(c0)==[] and await alertas()==[] and not (await estado(c0))['pausada']
                await chegar(c0,'e o evento?');await rodar(c0,fala('Vou transferir.',acao='transferir_humano',motivo='evento_ou_encomenda'))
                e=await estado(c0);assert e['pausada'] and e['aguardando_motivo']=='transferido_pelo_agente' and (await turno(c0))['acao_original'] is None and await sols(c0)==[]

                # 1) Ligado: grava a solicitação, alerta uma vez, envia os balões e NÃO pausa nem marca aguardando; o turno seguinte é respondido.
                await modo(notificar_equipe='on')
                c1=await conversa('n1')
                await rodar(c1,fala('Fazemos eventos sim!','Já avisei nossa equipe.',acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao=EVENTO))
                assert [m['conteudo'] for m in await saidas(c1)]==['Fazemos eventos sim!','Já avisei nossa equipe.']
                e=await estado(c1);assert not e['pausada'] and e['aguardando_atendente_desde'] is None
                s=(await sols(c1))[0];assert (s['numero'],s['status'],s['motivo'],s['tipo'],s['origem'],json.loads(s['dados']),s['atualizacoes'])==('S-0001','aberta','evento_ou_encomenda','evento','agente',{'data':'25 de dezembro','pessoas':50},0)
                a=await alertas();assert len(a)==1 and a[0]['destino']=='5511900000060' and a[0]['texto'].startswith('📋 SOLICITAÇÃO PARA A EQUIPE S-0001 — evento') and 'Data: 25 de dezembro · Pessoas: 50' in a[0]['texto']
                assert (await turno(c1))['acao']=='notificar_equipe'
                await chegar(c1,'ok, obrigado');await rodar(c1,fala('Por nada!'));assert [m['conteudo'] for m in await saidas(c1)][-1]=='Por nada!'
                # O agente passa a ver a solicitação aberta no pacote.
                assert [(x['numero'],x['motivo'],x['tipo'],x['status'],x['data'],x['pessoas'],x['local']) for x in pacotes[-1]['contexto_modulos']['solicitacoes']]==[('S-0001','evento_ou_encomenda','evento','aberta','25 de dezembro',50,None)]

                # 2) Mesma conversa e mesmo motivo: atualiza a existente. Campo ausente não apaga; campo novo gera alerta só depois de 30 min.
                await chegar(c1,'vai ser no salão');await rodar(c1,fala('Anotado.',acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao={'tipo':'evento','local':'Salão do prédio'}))
                s=(await sols(c1));assert len(s)==1 and json.loads(s[0]['dados'])=={'data':'25 de dezembro','pessoas':50,'local':'Salão do prédio'} and s[0]['atualizacoes']==1 and len(await alertas())==1
                await pool.execute("UPDATE solicitacoes SET alertado_em=now()-interval '31 minutes' WHERE id=$1",s[0]['id'])
                # Trocar o valor de um campo já preenchido não alerta; campo que estava vazio, sim (passados os 30 min).
                await chegar(c1,'na verdade 60');await rodar(c1,fala('Ok.',acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao={'tipo':'evento','pessoas':60}))
                assert json.loads((await sols(c1))[0]['dados'])['pessoas']==60 and len(await alertas())==1
                await chegar(c1,'pode ser buffet');await rodar(c1,fala('Ok.',acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao={'tipo':'evento','observacao':'Quer buffet.'}))
                a=await alertas();assert len(a)==2 and a[1]['texto'].startswith('🔄 SOLICITAÇÃO ATUALIZADA S-0001') and 'Obs.: Quer buffet.' in a[1]['texto']
                # Outro motivo na mesma conversa abre outra; motivo fora do catálogo vira fora_do_escopo com o texto guardado; sem solicitacao também vale.
                await chegar(c1,'e catering?');await rodar(c1,fala('Avisei.',acao='notificar_equipe',motivo='catering'))
                await chegar(c1,'outra coisa');await rodar(c1,fala('Avisei.',acao='notificar_equipe',motivo='quer falar de parceria',solicitacao={'tipo':'parceria'}))
                s=await sols(c1);assert [(x['numero'],x['motivo'],x['tipo'],x['motivo_informado']) for x in s]==[('S-0001','evento_ou_encomenda','evento',None),('S-0002','catering','catering',None),('S-0003','fora_do_escopo','parceria','quer falar de parceria')]

                # 3) Resposta descartada por refazer não cria solicitação; só a que sai cria.
                await modo(refazer_turno='on');c2=await conversa('n2')
                async def chega(payload): await chegar(payload['conversation_id'],'só queria saber')
                await rodar(c2,fala('descartada',antes=chega,acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao=EVENTO),fala('Tudo bem!'))
                assert [m['conteudo'] for m in await saidas(c2)]==['Tudo bem!'] and await sols(c2)==[]

                # 4) Proteção: transferir_humano por evento/catering com carrinho com itens → vira aviso, não pausa, fica registrado.
                c3=await conversa('n3');await item(c3)
                await rodar(c3,fala('Anotei seu pedido.','Sobre o evento, vamos encaminhar para a equipe, ok?',acao='transferir_humano',motivo='evento_ou_encomenda'))
                e=await estado(c3);assert not e['pausada'] and e['aguardando_atendente_desde'] is None
                assert tuple(await turno(c3))==('notificar_equipe','transferir_humano','ok') and [(x['origem'],x['motivo']) for x in await sols(c3)]==[('convertida','evento_ou_encomenda')]
                await chegar(c3,'Ok');await rodar(c3,fala('Posso fechar o pedido?'));assert [m['conteudo'] for m in await saidas(c3)][-1]=='Posso fechar o pedido?'
                # ...e com pedido ativo na conversa (aguardando a loja), sem carrinho aberto. Motivo catering também.
                c4=await conversa('n4');await item(c4);assert (await api('PUT',f'/carrinho/{c4}/entrega',json={'modalidade':'retirada'})).status_code==200
                assert (await api('POST','/pedidos',json={'conversation_id':c4})).status_code==201
                await rodar(c4,fala('Vou passar para a equipe de catering.',acao='transferir_humano',motivo='catering',solicitacao={'tipo':'catering','pessoas':30}))
                assert not (await estado(c4))['pausada'] and tuple(await turno(c4))[:2]==('notificar_equipe','transferir_humano') and (await sols(c4))[0]['tipo']=='catering'
                # Sem venda em andamento (sem carrinho, ou carrinho vazio): transfere como hoje.
                c5=await conversa('n5');await rodar(c5,fala('Vou transferir.',acao='transferir_humano',motivo='evento_ou_encomenda'))
                e=await estado(c5);assert e['pausada'] and e['aguardando_motivo']=='transferido_pelo_agente' and tuple(await turno(c5))[:2]==('transferir_humano',None) and await sols(c5)==[]
                # Outros motivos nunca convertem, mesmo com carrinho: pediu_humano, reclamacao, pagamento, pedido_para_confirmar pausam.
                for n,motivo in enumerate(('pediu_humano','reclamacao','pagamento','pedido_para_confirmar')):
                    cx=await conversa(f'm{n}');await item(cx);transferir=fala('Vou transferir.',acao='transferir_humano',motivo=motivo)
                    async def com_recusa(payload,transferir=transferir,cx=cx):
                        # HOR2: pedido_para_confirmar só passa direto quando alguma Skill recusou algo no turno (aqui, um item sem preço).
                        await pool.execute("INSERT INTO skill_registro(tenant_id,conversation_id,skill,metodo,status,motivo,duracao_ms) VALUES ('bykoji',$1,'adicionar_item','POST',422,'sem_preco',1)",cx)
                        return await transferir(payload)
                    await rodar(cx,com_recusa if motivo=='pedido_para_confirmar' else transferir)
                    assert (await estado(cx))['pausada'] and tuple(await turno(cx))[:2]==('transferir_humano',None) and await sols(cx)==[],motivo

                # 5) Destino marcado como grupo externo mascara o telefone; sem destino cadastrado grava a solicitação e não cria alerta.
                await modo(transbordo_destino_externo=True);c6=await conversa('n6')
                await pool.execute("UPDATE conversas SET telefone='5511942717046' WHERE conversation_id=$1",c6)
                await rodar(c6,fala('Avisei.',acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao=EVENTO))
                ultimo=(await alertas())[-1]['texto'];assert '5511****7046' in ultimo and '94271-7046' not in ultimo
                await modo(transbordo_destino_externo=False);assert (await c.patch(f'/admin/v1/tenants/{T}',json={'whatsapp_alerta_transbordo':None})).status_code==200
                n=len(await alertas());c7=await conversa('n7');await rodar(c7,fala('Avisei.',acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao=EVENTO))
                assert len(await sols(c7))==1 and len(await alertas())==n
                # Envio de teste: o alerta não sai de verdade (fica simulado), como os demais avisos internos.
                await avisos.despachar(p);assert {x['status'] for x in await alertas()}=={'simulado'}

                # 6) Painel: lista, filtros, contagem, detalhe, mudança de status e conversa com a solicitação aberta.
                todas=(await c.get(B)).json()['itens'];assert [x['numero'] for x in todas]==['S-0007','S-0006','S-0005','S-0004','S-0003','S-0002','S-0001']
                d=todas[-1];assert (d['status'],d['motivo'],d['tipo'],d['origem'],d['dados'],d['atualizacoes'])==('aberta','evento_ou_encomenda','evento','agente',{'data':'25 de dezembro','pessoas':60,'local':'Salão do prédio','observacao':'Quer buffet.'},3)
                assert d['conversation_id']==c1 and d['conversa']=={'estado':'bot','pedido_aberto':None} and d['alerta']['status']=='simulado' and d['proximos_status']==['em_andamento','concluida','cancelada'] and set(d['cliente'])=={'nome','nome_origem','telefone','contato_id'}
                assert todas[2]['conversa']['pedido_aberto']['status']=='aguardando_loja' and todas[0]['alerta']=={'status':'sem_destino','em':None}
                assert [x['numero'] for x in (await c.get(B,params={'motivo':'catering'})).json()['itens']]==['S-0005','S-0002'] and [x['numero'] for x in (await c.get(B,params={'tipo':'parceria'})).json()['itens']]==['S-0003']
                assert [x['numero'] for x in (await c.get(B,params={'busca':'S-0004'})).json()['itens']]==['S-0004'] and (await c.get(B,params={'busca':'x'})).status_code==400 and (await c.get(B,params={'status':'qualquer'})).status_code==400
                pg=(await c.get(B,params={'limite':3})).json();assert [x['numero'] for x in (await c.get(B,params={'limite':3,'cursor':pg['proximo_cursor']})).json()['itens']]==['S-0004','S-0003','S-0002']
                k=(await c.get(B+'/contagem')).json();assert (k['abertas'],k['em_andamento'])==(7,0) and k['mais_antiga_em']
                um=f'{B}/{d["id"]}'
                # Qualquer papel atualiza (membro incluído); em andamento grava quem assumiu; status só anda para frente.
                r_=await c.patch(um,json={'status':'em_andamento','nota':'Liguei para a cliente.'},headers={'X-Actor':'joana','X-Role':'membro'});assert r_.status_code==200,r_.text
                assert (r_.json()['status'],r_.json()['atendido_por'],r_.json()['nota'],r_.json()['proximos_status'])==('em_andamento','joana','Liguei para a cliente.',['concluida','cancelada'])
                assert (await c.patch(um,json={'status':'concluida'},headers={'X-Actor':'bia'})).json()['concluido_em'] and (await c.get(um)).json()['atendido_por']=='joana'
                r_=await c.patch(um,json={'status':'em_andamento'},headers={'X-Actor':'bia'});assert r_.status_code==409 and r_.json()['erro']['detalhes']=={'campo':'status','status':'concluida'}
                assert (await c.patch(um,json={},headers={'X-Actor':'bia'})).status_code==400 and (await c.patch(um,json={'status':'aberta'},headers={'X-Actor':'bia'})).status_code==400 and (await c.patch(um,json={'nota':'x'},headers={'X-Actor':''})).status_code==400
                assert (await c.get(B,params={'status':'aberta,em_andamento'})).json()['itens'][-1]['numero']=='S-0002' and (await c.get(B+'/contagem')).json()['abertas']==6
                # Depois de concluída, a mesma conversa e o mesmo motivo abrem outra solicitação.
                await chegar(c1,'outro evento em janeiro');await rodar(c1,fala('Avisei.',acao='notificar_equipe',motivo='evento_ou_encomenda',solicitacao={'tipo':'evento','data':'janeiro'}))
                assert [(x['numero'],x['status']) for x in await sols(c1) if x['motivo']=='evento_ou_encomenda']==[('S-0001','concluida'),('S-0008','aberta')]
                conv={x['conversation_id']:x for x in (await c.get(f'/admin/v1/tenants/{T}/conversas',params={'limite':99})).json()['itens']}
                assert conv[c3]['solicitacao_aberta']['numero']=='S-0004' and conv[c3]['solicitacao_aberta']['status']=='aberta' and conv[c5]['solicitacao_aberta'] is None and conv[c1]['solicitacao_aberta']['numero']=='S-0008'
                m=(await c.get(f'/admin/v1/tenants/{T}/metricas')).json()['solicitacoes'];assert m=={'criadas':8,'atualizadas':3,'convertidas':2,'alertas_enviados':0}
                assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='solicitacao.criada'",T)==8 and await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='solicitacao.status' AND actor IN ('joana','bia')",T)==2
                # Isolamento: outra empresa não vê, não conta e não altera.
                await criar(c,'bravo');O='/admin/v1/tenants/bravo/solicitacoes'
                assert (await c.get(O)).json()['itens']==[] and (await c.get(O+'/contagem')).json()['abertas']==0 and (await c.get(f'{O}/{d["id"]}')).status_code==404
                assert (await c.patch(f'{O}/{d["id"]}',json={'nota':'x'},headers={'X-Actor':'x'})).status_code==404 and (await c.get('/admin/v1/tenants/nao_existe/solicitacoes')).status_code==404
                assert await S.abertas(pool,'bravo',c1)==[]
        finally:
            await limpar(r,pref)
    banco_real(cenario)
