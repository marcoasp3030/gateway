"""MULTI1-a: empresa da sessão (X-Tenant-Scope), modelos de agente como cadastro, módulos com três chaves,
motivos e tipos em camadas, configuração de pedido escondida sem o módulo e webhook da instância uazapi."""
import json,logging
import httpx,pytest
from nucleo import escopo_empresa,atendimento as A,solicitacoes as S
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')

PLAT={'X-Role':'plataforma'}
ADMIN_ALPHA={'X-Role':'admin','X-Tenant-Scope':'alpha'}
# As listas do By Koji (núcleo + módulo de pedidos + modelo de delivery), iguais às de antes do MULTI1.
MOTIVOS_BYKOJI=['pediu_humano','reclamacao','pedido_para_confirmar','pagamento','restricao_alimentar','evento_ou_encomenda','catering','fora_do_escopo','assunto_proibido']
TIPOS_BYKOJI=['evento','catering','encomenda','orcamento','parceria','outro']


def erro(r):
    corpo=r.json()['erro'];return r.status_code,corpo['codigo'],corpo['detalhes']


async def agente_de(client,tenant,modelo,versao=None,headers=PLAT):
    dados={'nome':'A '+modelo,'modelo_id':modelo,**({'modelo_versao':versao} if versao else {})}
    r=await client.post(f'/admin/v1/tenants/{tenant}/agentes',json=dados,headers=headers)
    return r


def test_empresa_da_sessao_isola_as_rotas_e_a_plataforma_ve_tudo(monkeypatch):
    alertas=[]
    async def disparar(self,tipo,nivel,titulo,texto,*a,**k): alertas.append((tipo,texto))
    monkeypatch.setattr('nucleo.alertas.Alertas.disparar',disparar)
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,ca=await criar(c);b,cb=await criar(c,'bravo')
            escopo_empresa._ultimo_log.clear();alertas.clear()
            # Rotas de outra empresa, de todas as famílias: 404, sem chamar a rota (nada é gravado).
            for papel in ('admin','membro'):
                h={'X-Role':papel,'X-Tenant-Scope':'alpha'}
                for metodo,rota in [('GET',''),('GET','/agentes'),('GET',f'/agentes/{b}'),('PATCH',f'/agentes/{b}'),('GET',f'/agentes/{b}/modulos'),
                                    ('GET',f'/canais/{cb}'),('POST',f'/canais/{cb}/webhook'),('GET','/atendimento'),('GET',f'/agentes/{b}/atendimento'),
                                    ('GET','/solicitacoes'),('GET','/modulos'),('PUT','/modulos/catalogo'),('GET','/config'),('PUT','/config'),
                                    ('GET','/auditoria'),('GET','/consumo'),('GET','/catalogo/itens'),('POST','/api-keys')]:
                    r=await c.request(metodo,'/admin/v1/tenants/bravo'+rota,json={'nome':'invasao'},headers=h)
                    assert r.status_code==404 and r.json()['erro']['codigo']=='NOT_FOUND',(papel,metodo,rota,r.text)
                # Empresa no parâmetro ?tenant=
                assert (await c.get('/admin/v1/modelos-agente?tenant=bravo',headers=h)).status_code==404
                # Visões e escritas de toda a plataforma: 403.
                for metodo,rota in [('POST','/admin/v1/tenants'),('GET','/admin/v1/fila'),('GET','/admin/v1/auditoria/plataforma'),
                                    ('POST','/admin/v1/modelos-agente'),('PATCH','/admin/v1/modelos-agente/delivery_restaurante')]:
                    r=await c.request(metodo,rota,json={'id':'x','nome':'x'},headers=h)
                    assert r.status_code==403,(papel,metodo,rota,r.text)
                # Lista de empresas: só da plataforma (PAPEL1-a).
                assert (await c.get('/admin/v1/tenants',headers=h)).status_code==403
            assert (await c.get(f'/admin/v1/tenants/bravo/agentes/{b}',headers=PLAT)).status_code==200   # bravo intacto
            assert await conn.fetchval("SELECT nome FROM agentes WHERE tenant_id='bravo' AND id=$1",b)=='A'
            # A própria empresa continua funcionando.
            assert (await c.get('/admin/v1/tenants/alpha/agentes',headers=ADMIN_ALPHA)).status_code==200
            assert (await c.patch(f'/admin/v1/tenants/alpha/agentes/{a}',json={'nome':'Novo'},headers=ADMIN_ALPHA)).status_code==200
            assert (await c.get('/admin/v1/tenants/alpha/modelos-disponiveis',headers=ADMIN_ALPHA)).status_code==200   # PAPEL1-a: /modelos-agente é só da plataforma
            # A plataforma vê tudo, com ou sem o cabeçalho.
            for h in (PLAT,{**PLAT,'X-Tenant-Scope':'alpha'}):
                assert (await c.get(f'/admin/v1/tenants/bravo/agentes/{b}',headers=h)).status_code==200
                assert {t['id'] for t in (await c.get('/admin/v1/tenants',headers=h)).json()['itens']}>={'alpha','bravo'}
                assert (await c.get('/admin/v1/auditoria/plataforma',headers=h)).status_code==200
            assert alertas==[]   # com o cabeçalho ou como plataforma, nada a avisar
    banco(cenario)


def test_sem_cabecalho_na_transicao_registra_e_avisa_e_com_a_exigencia_recusa(monkeypatch,caplog):
    alertas=[]
    async def disparar(self,tipo,nivel,titulo,texto,*a,**k): alertas.append((tipo,nivel,texto))
    monkeypatch.setattr('nucleo.alertas.Alertas.disparar',disparar)
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,ca=await criar(c);await criar(c,'bravo')
            escopo_empresa._ultimo_log.clear();alertas.clear();caplog.clear()
            monkeypatch.setenv('ESCOPO_EMPRESA_OBRIGATORIO','off')   # transição (o padrão ficou ligado no PAPEL1-a)
            with caplog.at_level(logging.WARNING,logger='gateway.escopo'):
                # Transição: segue como antes (inclusive outra empresa), com log e alerta, uma vez por minuto por rota.
                for _ in range(3): assert (await c.get('/admin/v1/tenants/bravo/agentes',headers={'X-Role':'admin'})).status_code==200
                assert (await c.get(f'/admin/v1/tenants/alpha/agentes/{a}',headers={'X-Role':'supervisor'})).status_code==200
            linhas=[r.getMessage() for r in caplog.records if r.name=='gateway.escopo']
            assert linhas==['Chamada do painel sem X-Tenant-Scope: papel=admin GET /admin/v1/tenants/{t}/agentes',
                            'Chamada do painel sem X-Tenant-Scope: papel=supervisor GET /admin/v1/tenants/{t}/agentes/{id}']
            assert a not in str(caplog.text) and a not in str(alertas)   # nem o id do agente vai para o log
            assert [x[:2] for x in alertas]==[('painel_sem_escopo','aviso')]*2 and 'bravo' not in str(alertas)
            # Plataforma sem cabeçalho não gera aviso.
            alertas.clear();assert (await c.get('/admin/v1/tenants/bravo/agentes',headers=PLAT)).status_code==200 and alertas==[]
            # Exigência ligada (quando o painel publicar): sem o cabeçalho, 403; com ele, normal; plataforma continua.
            monkeypatch.setenv('ESCOPO_EMPRESA_OBRIGATORIO','on')
            r=await c.get('/admin/v1/tenants/alpha/agentes',headers={'X-Role':'admin'});assert r.status_code==403
            assert (await c.get('/admin/v1/tenants/alpha/agentes',headers=ADMIN_ALPHA)).status_code==200
            assert (await c.get('/admin/v1/tenants/bravo/agentes',headers=PLAT)).status_code==200
            # Rotas fora da API admin não mudam.
            assert (await c.get('/health')).status_code in (200,503)
    banco(cenario)


def test_modelos_delivery_e_atendimento_geral_e_modelos_antigos():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            await criar(c)
            itens=(await c.get('/admin/v1/modelos-agente')).json()['itens']
            por={(m['id'],m['versao']):m for m in itens}
            v3,v4=por[('delivery_restaurante',3)],por[('delivery_restaurante',4)]
            assert v4['nome']=='Delivery' and v4['segmento']=='delivery' and v4['ativo'] and v4['modulos_permitidos']==v4['modulos_padrao']==['catalogo','pedidos']
            # O v3 (do By Koji) só ganhou os campos novos, com os mesmos valores do v4.
            for campo in ('segmento','modulos_permitidos','modulos_padrao','motivos_transferencia','tipos_solicitacao','ativo','comportamento_padrao','perfil_padrao'):
                assert v3[campo]==v4[campo],campo
            assert [m['id'] for m in v4['motivos_transferencia']]==['pagamento','restricao_alimentar','evento_ou_encomenda','catering','fora_do_escopo','assunto_proibido']
            assert [m['id'] for m in v4['motivos_transferencia'] if m.get('converte_em_solicitacao')]==['evento_ou_encomenda','catering']
            assert [t['id'] for t in v4['tipos_solicitacao']]==['evento','catering','encomenda','orcamento','parceria']
            g=por[('atendimento_geral',1)]
            assert (g['segmento'],g['modulos_permitidos'],g['ativo'])==('geral',[],True)
            assert [m['id'] for m in g['motivos_transferencia']]==['pagamento','fora_do_escopo','assunto_proibido'] and [t['id'] for t in g['tipos_solicitacao']]==['orcamento','parceria']
            # Filtros.
            assert {m['id'] for m in (await c.get('/admin/v1/modelos-agente?segmento=geral')).json()['itens']}>={'atendimento_geral'}
            inativos={m['id'] for m in (await c.get('/admin/v1/modelos-agente?ativo=false')).json()['itens']}
            assert inativos=={'assistente_generico','atendente_delivery'}
            # Modelo antigo: não serve para agente novo nem para troca de modelo; quem já usa continua.
            for velho in ('assistente_generico','atendente_delivery'):
                r=await agente_de(c,'alpha',velho);assert erro(r)==(400,'VALIDATION_ERROR',{'campo':'modelo_id','motivo':'modelo_inativo'}),r.text
            efetiva=await app.state.configuracoes.efetiva('bykoji','bykoji_atendimento')
            assert efetiva['modelo_id']=='atendente_delivery' and efetiva['modulos']==['catalogo','pedidos']
            assert (await c.patch('/admin/v1/tenants/bykoji/agentes/bykoji_atendimento',json={'nome':'Yuki'},headers=PLAT)).status_code==200
            # Agente novo sem versão nasce na mais recente.
            r=await agente_de(c,'alpha','delivery_restaurante');assert r.status_code==201 and r.json()['modelo_versao']==4
            # Criar e alterar modelo: só a plataforma.
            novo={'id':'clinica','nome':'Clínica','segmento':'clinica','modulos_permitidos':[]}
            for papel in ('admin','membro'):
                assert (await c.post('/admin/v1/modelos-agente',json=novo,headers={'X-Role':papel})).status_code==403
                assert (await c.patch('/admin/v1/modelos-agente/atendimento_geral',json={'nome':'x'},headers={'X-Role':papel})).status_code==403
            # Segmento: texto livre validado; listas com id e rótulo; id repetido recusado.
            for seg in ('Clínica','clin ica','x'*41,''):
                assert (await c.post('/admin/v1/modelos-agente',json={**novo,'segmento':seg},headers=PLAT)).status_code==400,seg
            r=await c.post('/admin/v1/modelos-agente',json={**novo,'motivos_transferencia':[{'id':'exame','rotulo':'Exame'},{'id':'exame','rotulo':'De novo'}]},headers=PLAT)
            assert erro(r)==(400,'VALIDATION_ERROR',{'campo':'motivos_transferencia','motivo':'repetido'})
            r=await c.post('/admin/v1/modelos-agente',json={**novo,'modulos_permitidos':['inexistente']},headers=PLAT);assert r.status_code==400
            r=await c.post('/admin/v1/modelos-agente',json={**novo,'skyone_ref':'fluxo-clinica-v1','motivos_transferencia':[{'id':'exame','rotulo':'Resultado de exame','converte_em_solicitacao':True}],
                'tipos_solicitacao':[{'id':'agendamento','rotulo':'Agendamento'}]},headers=PLAT)
            assert r.status_code==201 and (r.json()['segmento'],r.json()['skyone_ref'],r.json()['ativo'])==('clinica','fluxo-clinica-v1',True),r.text
            r=await c.patch('/admin/v1/modelos-agente/clinica',json={'ativo':False},headers=PLAT);assert r.json()['versao']==2 and r.json()['ativo'] is False
    banco(cenario)


def test_motivos_e_tipos_em_camadas_e_by_koji_igual():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            await criar(c)
            # By Koji (modelo de delivery com pedidos): as mesmas 9 opções e 6 tipos de antes, na mesma ordem.
            cfg=await app.state.configuracoes.efetiva('bykoji','bykoji_atendimento')
            assert [m['id'] for m in cfg['motivos_transferencia_permitidos']]==MOTIVOS_BYKOJI
            assert [t['id'] for t in cfg['tipos_solicitacao_permitidos']]==TIPOS_BYKOJI
            assert all(m['rotulo'] for m in cfg['motivos_transferencia_permitidos']) and list(S.que_nao_pausam(cfg))==['evento_ou_encomenda','catering']
            assert cfg['perfil_atendimento']['motivos_transferencia']==MOTIVOS_BYKOJI
            # Atendimento geral: núcleo + modelo, sem os do módulo de pedidos.
            g=(await agente_de(c,'alpha','atendimento_geral')).json()['id']
            cg=await app.state.configuracoes.efetiva('alpha',g)
            assert [m['id'] for m in cg['motivos_transferencia_permitidos']]==['pediu_humano','reclamacao','pagamento','fora_do_escopo','assunto_proibido']
            assert [t['id'] for t in cg['tipos_solicitacao_permitidos']]==['orcamento','parceria','outro']
            assert cg['perfil_atendimento']['motivos_transferencia']==['pediu_humano','reclamacao','pagamento','fora_do_escopo','assunto_proibido']
            # O esquema do painel só oferece as opções do escopo e recusa motivo de fora.
            E=f'/admin/v1/tenants/alpha/agentes/{g}/atendimento'
            esq=(await c.get(E,headers=ADMIN_ALPHA)).json()['esquema']
            assert esq['perfil']['motivos_transferencia']['opcoes']==['pediu_humano','reclamacao','pagamento','fora_do_escopo','assunto_proibido']
            r=await c.put(E+'/rascunho',json={'baseado_em':0,'perfil':{'motivos_transferencia':['pediu_humano','restricao_alimentar']}},headers=ADMIN_ALPHA)
            assert r.status_code==400 or r.json().get('bloqueios'),r.text
            # Motivo e tipo fora da lista do agente: motivo vira fora_do_escopo, tipo vira outro.
            assert S.motivo_do_catalogo('restricao_alimentar',[m['id'] for m in cg['motivos_transferencia_permitidos']])[0]=='fora_do_escopo'
            assert S.motivo_do_catalogo('pagamento',[m['id'] for m in cg['motivos_transferencia_permitidos']])==('pagamento',None)
    banco(cenario)


def test_modulo_com_tres_chaves_e_configuracao_de_pedido_escondida():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            await criar(c)
            d=(await agente_de(c,'alpha','delivery_restaurante')).json()['id'];g=(await agente_de(c,'alpha','atendimento_geral')).json()['id']
            D=f'/admin/v1/tenants/alpha/agentes/{d}';G=f'/admin/v1/tenants/alpha/agentes/{g}'
            ef=lambda ag: app.state.configuracoes.efetiva('alpha',ag)
            # Padrão: tudo o que o modelo permite, ligado (empresa sem registro = ligado).
            m=(await c.get(D+'/modulos',headers=ADMIN_ALPHA)).json()
            assert (m['modulos'],m['permitidos'],m['padrao_do_modelo'],m['ligados_no_agente'],m['segmento'])==(['catalogo','pedidos'],['catalogo','pedidos'],['catalogo','pedidos'],None,'delivery')
            assert m['modelo']=={'id':'delivery_restaurante','versao':4,'nome':'Delivery'} and m['da_empresa']=={'catalogo':True,'pedidos':True}
            assert (await c.get(D,headers=ADMIN_ALPHA)).json()['modulos']==['catalogo','pedidos'] and 'carrinho_expira_h' in await ef(d)
            assert (await c.get(G+'/modulos',headers=ADMIN_ALPHA)).json()['modulos']==[]
            mt=(await c.get('/admin/v1/tenants/alpha/modulos',headers=ADMIN_ALPHA)).json()
            assert (mt['em_uso'],mt['telas'])==(['catalogo','pedidos'],['cardapio','pedidos','avisos_de_pedido'])
            # Chave do agente: admin liga e desliga dentro do que o modelo permite; membro não.
            assert (await c.patch(D,json={'modulos_ligados':['catalogo']},headers={'X-Role':'membro','X-Tenant-Scope':'alpha'})).status_code==403
            r=await c.patch(D,json={'modulos_ligados':['catalogo','agenda']},headers=ADMIN_ALPHA);assert erro(r)==(400,'VALIDATION_ERROR',{'campo':'modulos_ligados','motivo':'fora_do_modelo'})
            r=await c.patch(G,json={'modulos_ligados':['pedidos']},headers=ADMIN_ALPHA);assert erro(r)[2]=={'campo':'modulos_ligados','motivo':'fora_do_modelo'}
            assert (await c.patch(D,json={'modulos_ligados':['catalogo']},headers=ADMIN_ALPHA)).status_code==200
            cfg=await ef(d);assert cfg['modulos']==['catalogo'] and 'carrinho_expira_h' not in cfg and 'acoes_recentes_min' not in cfg
            assert 'pedido_para_confirmar' not in [x['id'] for x in cfg['motivos_transferencia_permitidos']]
            # Configuração de pedido: escondida no efetivo e no esquema, e recusada na gravação.
            for ag in (d,g):
                A_=f'/admin/v1/tenants/alpha/agentes/{ag}'
                r=await c.patch(A_,json={'carrinho_expira_h':5},headers=ADMIN_ALPHA)
                assert erro(r)==(400,'VALIDATION_ERROR',{'campo':'carrinho_expira_h','motivo':'modulo_desligado'}),r.text
                vis=(await c.get(A_+'/atendimento',headers=ADMIN_ALPHA)).json()
                assert 'carrinho_expira_h' not in vis['esquema']['comportamentos'] and 'carrinho_expira_h' not in vis['efetivo']['comportamentos']
                r=await c.put(A_+'/atendimento/rascunho',json={'baseado_em':0,'comportamentos':{'carrinho_expira_h':5}},headers=ADMIN_ALPHA)
                assert r.status_code==400 and 'modulo_desligado' in r.text,r.text
            assert (await c.patch(D,json={'modulos_ligados':None},headers=ADMIN_ALPHA)).status_code==200 and (await ef(d))['modulos']==['catalogo','pedidos']
            assert (await c.patch(D,json={'carrinho_expira_h':5},headers=ADMIN_ALPHA)).status_code==200 and (await ef(d))['carrinho_expira_h']==5
            # Chave da empresa: só a plataforma; desligada, sai de todos os agentes da empresa.
            assert (await c.put('/admin/v1/tenants/alpha/modulos/pedidos',json={'ativo':False},headers=ADMIN_ALPHA)).status_code==403
            assert (await c.put('/admin/v1/tenants/alpha/modulos/pedidos',json={'ativo':False},headers=PLAT)).status_code==200
            cfg=await ef(d);assert cfg['modulos']==['catalogo'] and 'carrinho_expira_h' not in cfg
            m=(await c.get(D+'/modulos',headers=ADMIN_ALPHA)).json();assert m['da_empresa']=={'catalogo':True,'pedidos':False} and m['modulos']==['catalogo']
            mt=(await c.get('/admin/v1/tenants/alpha/modulos',headers=ADMIN_ALPHA)).json();assert (mt['em_uso'],mt['telas'])==(['catalogo'],['cardapio'])
            # A empresa vizinha não muda.
            await criar(c,'bravo');bd=(await agente_de(c,'bravo','delivery_restaurante')).json()['id']
            assert (await app.state.configuracoes.efetiva('bravo',bd))['modulos']==['catalogo','pedidos']
    banco(cenario)


def test_pacote_traz_o_bloco_agente(monkeypatch):
    from agentes import skyone
    from test_skyone_adaptador import preparar_agente
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        atual=await conn.fetchval("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('bykoji','bykoji:canal:x','entrada','cliente','oi') RETURNING id")
        p=await skyone.pacote(repo,'bykoji','bykoji:canal:x',atual,'oi',cfg)
        ag=p['agente']
        assert set(ag)=={'modelo','segmento','modulos','motivos_transferencia','tipos_solicitacao'}
        assert ag['modelo']['id']==cfg['modelo_id'] and ag['segmento']=='delivery' and ag['modulos']==['catalogo','pedidos']
        assert [m['id'] for m in ag['motivos_transferencia']]==MOTIVOS_BYKOJI and all(set(m)=={'id','rotulo'} for m in ag['motivos_transferencia'])
        assert [t['id'] for t in ag['tipos_solicitacao']]==TIPOS_BYKOJI
        assert 'instrucoes_modulos' not in json.dumps(p)   # decisão 7: sem texto de instruções de módulo no pacote
    banco(cenario)


def test_webhook_da_instancia_cadastrado_pelo_gateway(monkeypatch):
    chamadas=[];falhar={'status':None}
    def responder(req):
        chamadas.append((req.method,str(req.url),req.headers.get('token'),json.loads(req.content) if req.content else None))
        return httpx.Response(falhar['status'] or 200,json={'ok':True})
    async def sem_rede(url): pass
    monkeypatch.setattr('canais.uazapi.seguranca.validar_url_saida',sem_rede)
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(responder))
        app.state.infra.registrar_webhook_uazapi=True
        async with c:
            a,_=await criar(c)
            cred={'base_url':'https://instancia.exemplo.com','token':'tok-fixture'}
            r=await c.post('/admin/v1/tenants/alpha/canais',json={'nome':'W','tipo':'uazapi','agente_id':a,'credenciais':cred},headers=ADMIN_ALPHA)
            assert r.status_code==201,r.text
            canal=r.json()['id'];assert r.json()['webhook_uazapi']=={'registrado':True}
            metodo,url,token,corpo=chamadas[-1]
            assert (metodo,url,token)==('POST','https://instancia.exemplo.com/webhook','tok-fixture')
            assert corpo['url']==r.json()['webhook_url'] and corpo['url'].startswith(app.state.infra.public_url.rstrip('/')+f'/webhooks/uazapi/{canal}?s=')
            assert (corpo['events'],corpo['excludeMessages'],corpo['enabled'],corpo['addUrlEvents'],corpo['addUrlTypesMessages'])==(['messages','connection'],['isGroupYes'],True,False,False)
            primeiro=corpo['url']
            # Troca de credenciais (outra instância): cadastra de novo, mesmo endereço.
            r=await c.patch(f'/admin/v1/tenants/alpha/canais/{canal}',json={'credenciais':{**cred,'base_url':'https://outra.exemplo.com'}},headers=ADMIN_ALPHA)
            assert r.json()['webhook_uazapi']=={'registrado':True} and chamadas[-1][1]=='https://outra.exemplo.com/webhook' and chamadas[-1][3]['url']==primeiro
            n=len(chamadas)
            assert 'webhook_uazapi' not in (await c.patch(f'/admin/v1/tenants/alpha/canais/{canal}',json={'nome':'Só o nome'},headers=ADMIN_ALPHA)).json() and len(chamadas)==n
            # Rotação do segredo: endereço novo cadastrado.
            r=await c.post(f'/admin/v1/tenants/alpha/canais/{canal}/rotacionar-segredo',headers=ADMIN_ALPHA)
            assert r.status_code==200 and r.json()['webhook_uazapi']=={'registrado':True},r.text
            assert chamadas[-1][3]['url']!=primeiro and chamadas[-1][3]['url']==r.json()['webhook_url']
            atual=chamadas[-1][3]['url']
            # Refazer à mão: mesmo endereço guardado; resposta sem o segredo; membro não pode.
            assert (await c.post(f'/admin/v1/tenants/alpha/canais/{canal}/webhook',headers={'X-Role':'membro','X-Tenant-Scope':'alpha'})).status_code==403
            r=await c.post(f'/admin/v1/tenants/alpha/canais/{canal}/webhook',headers=ADMIN_ALPHA)
            assert r.json()=={'id':canal,'webhook_uazapi':{'registrado':True}} and chamadas[-1][3]['url']==atual and '?s=' not in r.text
            # Falhas: provedor recusa; canal sem credenciais; nada de segredo na resposta nem na auditoria.
            falhar['status']=401
            r=await c.post(f'/admin/v1/tenants/alpha/canais/{canal}/webhook',headers=ADMIN_ALPHA);assert r.json()['webhook_uazapi']=={'registrado':False,'motivo':'provedor_recusou'}
            falhar['status']=None
            r=await c.post('/admin/v1/tenants/alpha/canais',json={'nome':'Sem cred','tipo':'uazapi','agente_id':a},headers=ADMIN_ALPHA)
            assert r.status_code==201 and 'webhook_uazapi' not in r.json()
            r=await c.post(f'/admin/v1/tenants/alpha/canais/{r.json()["id"]}/webhook',headers=ADMIN_ALPHA);assert r.json()['webhook_uazapi']=={'registrado':False,'motivo':'sem_credenciais'}
            assert (await c.post(f'/admin/v1/tenants/alpha/canais/{_}/webhook',headers=ADMIN_ALPHA)).status_code in (400,404)
            acoes=[x['acao'] for x in (await c.get('/admin/v1/tenants/alpha/auditoria',headers=ADMIN_ALPHA)).json()['itens']]
            assert acoes.count('canal.webhook_registrado')==4 and acoes.count('canal.webhook_falhou')==2
            aud=json.dumps((await c.get('/admin/v1/tenants/alpha/auditoria',headers=ADMIN_ALPHA)).json())
            assert 'tok-fixture' not in aud and '?s=' not in aud
            # Desligado (REGISTRAR_WEBHOOK_UAZAPI=off): não chama a instância.
            app.state.infra.registrar_webhook_uazapi=False;n=len(chamadas)
            r=await c.post('/admin/v1/tenants/alpha/canais',json={'nome':'Off','tipo':'uazapi','agente_id':a,'credenciais':cred},headers=ADMIN_ALPHA)
            assert r.status_code==201 and 'webhook_uazapi' not in r.json() and len(chamadas)==n
    banco(cenario)
