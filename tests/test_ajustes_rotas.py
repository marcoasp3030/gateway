"""Ajustes de rota ARQ1/FUP1 (22/10/2026): motivo obrigatório ao reativar o follow-up, filtros da lista de documentos e rotação
do segredo do webhook de saída com assinatura dupla por 24 h."""
import hashlib,hmac,json,uuid
from datetime import datetime
import httpx,pytest
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
PLAT={'X-Role':'plataforma'}
ADMIN={'X-Role':'admin','X-Tenant-Scope':'alpha'}
MEMBRO={'X-Role':'membro','X-Tenant-Scope':'alpha'}


def test_reativar_follow_up_exige_motivo_limpo_e_audita(monkeypatch):
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            await criar(c)
            cid='ct_'+uuid.uuid4().hex
            await conn.execute("INSERT INTO contatos(id,tenant_id,telefone,nome_cadastro) VALUES ($1,'alpha','5511900000901','Joana Teste')",cid)
            o=f'/admin/v1/tenants/alpha/contatos/{cid}/opt-out-follow-up'
            assert (await c.put(o,headers=MEMBRO)).status_code==200
            # Sem corpo, sem motivo, curto, só espaços ou longo demais: 400. Membro: 403.
            for corpo in (None,{},{'motivo':'ok'},{'motivo':'   ab   '},{'motivo':'x'*301},{'motivo':7},{'motivo':'válido','outro':1}):
                r=await c.request('DELETE',o,**({} if corpo is None else {'json':corpo}),headers=ADMIN)
                assert r.status_code==400,(corpo,r.status_code)
            assert (await c.request('DELETE',o,json={'motivo':'pedido do cliente'},headers=MEMBRO)).status_code==403
            assert await conn.fetchval("SELECT removido_em FROM follow_up_opt_out WHERE tenant_id='alpha' AND contato_id=$1",cid) is None
            # Válido: o motivo é gravado sem dado pessoal (telefone, e-mail, nome do contato), com quem e quando.
            r=await c.request('DELETE',o,json={'motivo':'  Joana ligou do 11 90000-1234 (joana@exemplo.com) e pediu para voltar  '},headers=ADMIN)
            assert r.status_code==200,r.text
            corpo=r.json()
            assert corpo['ativo'] is False and corpo['removido_por']=='teste' and corpo['removido_em']
            assert corpo['removido_motivo']=='[nome] ligou do [número] ([email]) e pediu para voltar'
            a=await conn.fetchrow("SELECT actor,dados,criado_em FROM audit_log WHERE tenant_id='alpha' AND acao='follow_up.opt_out_removido' AND alvo=$1",cid)
            dados=json.loads(a['dados'])
            assert a['actor']=='teste' and dados['motivo']==corpo['removido_motivo'] and dados['em']
            assert '90000' not in a['dados'] and 'Joana' not in a['dados']
            # Já reativado: 404.
            assert (await c.request('DELETE',o,json={'motivo':'de novo'},headers=ADMIN)).status_code==404
    banco(cenario)


async def _anexo(conn,tipo,criado,estado='pronto'):
    aid='anx_'+uuid.uuid4().hex[:24]
    await conn.execute("""INSERT INTO anexos(id,tenant_id,origem,modo,nome,formato,tamanho_bytes,sha256,estado,tipo_documento,criado_em,expira_em)
        VALUES ($1,'alpha','api','extrair','doc.pdf','pdf',10,$2,$3,$4,$5::timestamptz,$5::timestamptz+interval '30 days')""",
        aid,'0'*64,estado,tipo,datetime.fromisoformat(criado))
    return aid


def test_lista_de_documentos_filtra_por_tipo_e_periodo_em_sao_paulo():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            await criar(c)
            # 23:30 de 20/10 em São Paulo = 02:30 UTC de 21/10: conta como dia 20.
            ids={}
            for nome,tipo,quando in (('boleto_20','boleto','2026-10-20T23:30:00-03:00'),('nfe_21','nfe','2026-10-21T00:10:00-03:00'),
                                     ('pix_21','comprovante_pix','2026-10-21T12:00:00-03:00'),('sem_tipo_22',None,'2026-10-22T09:00:00-03:00'),
                                     ('outro_22','outro','2026-10-22T10:00:00-03:00'),('conc_22','boleto_concessionaria','2026-10-22T11:00:00-03:00'),
                                     ('nfce_23','nfce','2026-10-23T00:00:00-03:00')):
                ids[nome]=await _anexo(conn,tipo,quando)
            nomes={v:k for k,v in ids.items()}
            async def lista(**q):
                r=await c.get('/admin/v1/tenants/alpha/anexos',params=q,headers=ADMIN);assert r.status_code==200,r.text
                return r.json()
            async def todos(**q):
                saida=[];cursor=None
                while True:
                    p=await lista(**q,**({'cursor':cursor} if cursor else {}))
                    saida+=[nomes[i['id']] for i in p['itens']];cursor=p['proximo_cursor']
                    if not cursor: return sorted(saida)
            assert await todos(tipo_documento='boleto')==['boleto_20']
            assert await todos(tipo_documento='outro')==['outro_22','sem_tipo_22']
            assert await todos(tipo_documento='boleto_concessionaria')==['conc_22']
            assert await todos(de='2026-10-20',ate='2026-10-20')==['boleto_20']
            assert await todos(de='2026-10-21',ate='2026-10-21')==['nfe_21','pix_21']
            assert await todos(de='2026-10-22')==['conc_22','nfce_23','outro_22','sem_tipo_22']
            assert await todos(ate='2026-10-21')==['boleto_20','nfe_21','pix_21']
            # Combinados entre si, com os filtros de antes e com o cursor (página de 1).
            assert await todos(tipo_documento='nfe',de='2026-10-21',ate='2026-10-23',estado='pronto',limite=1)==['nfe_21']
            assert await todos(de='2026-10-21',ate='2026-10-22',limite=1)==['conc_22','nfe_21','outro_22','pix_21','sem_tipo_22']
            assert await todos(tipo_documento='nfe',estado='rejeitado')==[]
            # Inválidos: 400 com o campo.
            for q,campo in (({'tipo_documento':'cnh'},'tipo_documento'),({'de':'21/10/2026'},'de'),({'ate':'2026-13-01'},'ate'),
                            ({'de':'2026-10-22','ate':'2026-10-21'},'de'),({'de':'2026-10-2'},'de')):
                r=await c.get('/admin/v1/tenants/alpha/anexos',params=q,headers=ADMIN)
                assert r.status_code==400 and r.json()['erro']['detalhes']['campo']==campo,(q,r.text)
    banco(cenario)


def test_rotacao_do_segredo_do_webhook_assina_com_os_dois_por_24h(monkeypatch):
    from nucleo import webhooks
    async def validar(url,dominios=None): pass
    monkeypatch.setattr(webhooks,'validar_url_saida',validar)
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            await criar(c)
            r=await c.post('/admin/v1/tenants/alpha/webhooks-saida',json={'url':'https://destino.exemplo.com.br/aviso','eventos':['teste'],'ativo':True},headers=ADMIN)
            assert r.status_code==201,r.text
            wid,antigo=r.json()['id'],r.json()['segredo']
            q=f'/admin/v1/tenants/alpha/webhooks-saida/{wid}'
            assert (await c.post('/admin/v1/tenants/alpha/webhooks-saida/wh_inexistente/rotacionar-segredo',headers=ADMIN)).status_code==404
            r=await c.post(q+'/rotacionar-segredo',headers=ADMIN)
            assert r.status_code==200,r.text
            novo=r.json()['segredo']
            assert novo and novo!=antigo and r.json()['segredo_anterior_ate'] and r.json()['segredo_rotacionado_em']
            # O segredo sai uma vez só: nem o detalhe nem a lista o mostram.
            detalhe=(await c.get(q,headers=ADMIN)).json()
            assert 'segredo' not in detalhe and detalhe['segredo_anterior_ate']
            assert novo not in (await c.get('/admin/v1/tenants/alpha/webhooks-saida',headers=ADMIN)).text
            a=await conn.fetchrow("SELECT actor,dados FROM audit_log WHERE tenant_id='alpha' AND acao='webhook.segredo_rotacionado' AND alvo=$1",wid)
            assert a['actor']=='teste' and json.loads(a['dados'])['anterior_valido_ate'] and novo not in a['dados'] and antigo not in a['dados']
            # Entrega real pelo despachante: cabeçalho com as duas assinaturas, a do novo primeiro.
            vistos=[]
            def receber(req):
                vistos.append((req.headers['X-Gateway-Signature'],req.headers['X-Gateway-Timestamp'],req.content));return httpx.Response(200)
            async def entregar():
                assert (await c.post(q+'/testar',headers=ADMIN)).status_code==202
                async with httpx.AsyncClient(transport=httpx.MockTransport(receber)) as http:
                    await webhooks.despachar(app.state.db,http,app.state.cofre)
                return vistos[-1]
            def hm(s,ts,corpo): return 'sha256='+hmac.new(s.encode(),ts.encode()+b'.'+corpo,hashlib.sha256).hexdigest()
            sig,ts,corpo=await entregar()
            assert sig==hm(novo,ts,corpo)+','+hm(antigo,ts,corpo)
            # Passadas as 24 h: só a do novo.
            await conn.execute("UPDATE tenant_webhooks_saida SET segredo_anterior_ate=now()-interval '1 second' WHERE tenant_id='alpha' AND id=$1",wid)
            sig,ts,corpo=await entregar()
            assert sig==hm(novo,ts,corpo)
            assert (await c.get(q,headers=ADMIN)).json()['segredo_anterior_ate'] is None
            # Rotacionar de novo: o "anterior" passa a ser o que estava em uso.
            terceiro=(await c.post(q+'/rotacionar-segredo',headers=ADMIN)).json()['segredo']
            sig,ts,corpo=await entregar()
            assert sig==hm(terceiro,ts,corpo)+','+hm(novo,ts,corpo)
    banco(cenario)
