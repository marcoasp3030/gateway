"""ENC1-a: atendimento identificado — começo, fim e reabertura (docs/ESPEC_ENC1A.md, decisões de 20/10/2026).

Turnos completos pela fila durável com o agente simulado chamando as Skills, como em tests/test_multi1b_retrato.py."""
import json
from pathlib import Path
import httpx
import pytest
from test_migrations import DSN,banco,migrar
from test_fase4_pedidos import montar
from test_fila import banco_real,montar as montar_fila,limpar

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
T,A='bykoji','bykoji_atendimento'
H={'X-Role':'plataforma'}
UNIDADE_WHATS='5511900000091'


def cenario_by_koji(tmp_path,corpo):
    """Monta o By Koji (catálogo, unidade aberta, canal API, WhatsApp da unidade) e entrega ao `corpo` um kit de passos."""
    async def cenario(pool):
        app,c,r,p,pref=await montar_fila(pool,True,tmp_path)
        pacotes=[];roteiro=[]
        async def agente(http,repo,cofre,tenant,cfg,payload):
            pacotes.append(payload);return await roteiro.pop(0)(payload)
        p.chamar=agente
        try:
            async with c:
                conversa,ids,api,_=await montar(pool,c,app)
                canal=await pool.fetchval("SELECT id FROM tenant_canais WHERE tenant_id=$1 AND tipo='api' AND nome='API' AND ativo",T)
                await pool.execute("UPDATE tenant_unidades SET horarios=$2::jsonb,whatsapp_pedidos=$3,aceita_retirada=true WHERE tenant_id=$1 AND codigo='patio_guedala'",
                                   T,json.dumps({d:[['00:00','23:59']] for d in ('seg','ter','qua','qui','sex','sab','dom')}),UNIDADE_WHATS)
                n={'i':0}

                async def enviar(usuario,texto):
                    n['i']+=1
                    resp=await c.post(f'/admin/v1/tenants/{T}/agentes/{A}/simular',json={'canal_id':canal,'identificador':usuario,'texto':texto,'message_id':f'{usuario}-{n["i"]}'})
                    assert resp.status_code==200,resp.text
                    return resp.json()['conversation_id']

                def fala(texto,antes=None,**campos):
                    async def passo(payload):
                        if antes: await antes(payload)
                        return {'resposta':[texto] if texto else [],'acao':'nenhuma',**campos}
                    return passo

                async def turno(usuario,texto,*fases):
                    pacotes.clear()
                    conv=await enviar(usuario,texto)
                    roteiro[:]=list(fases)
                    assert await p.processar_duravel(T,conv)=='concluido'
                    assert not roteiro
                    return conv

                async def pedido_fechado(usuario):
                    """Três turnos: adiciona item, define retirada e fecha o pedido."""
                    async def adicionar(payload):
                        assert (await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001'],'quantidade':2})).status_code==200
                    conv=await turno(usuario,'quero 2 edamame',fala('Anotei. Mais alguma coisa?',adicionar))
                    async def entrega(payload):
                        assert (await api('PUT',f'/carrinho/{payload["conversation_id"]}/entrega',json={'unidade':'patio_guedala','modalidade':'retirada'})).status_code==200
                    await turno(usuario,'retiro no pátio',fala('Total R$ 68,00. Posso fechar?',entrega))
                    async def fechar(payload):
                        r=await api('POST','/pedidos',json={'conversation_id':payload['conversation_id']});assert r.status_code==201,r.text
                    await turno(usuario,'pode fechar',fala('Pedido feito!',fechar,acao='pedido_fechado',desfecho='pedido_feito'))
                    return conv,await pool.fetchval('SELECT numero FROM pedidos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1',T,conv)

                async def atendimentos(conv):
                    return [dict(a) for a in await pool.fetch('SELECT * FROM atendimentos WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY numero',T,conv)]

                kit=dict(pool=pool,c=c,p=p,api=api,ids=ids,canal=canal,pacotes=pacotes,enviar=enviar,fala=fala,turno=turno,
                         pedido_fechado=pedido_fechado,atendimentos=atendimentos,roteiro=roteiro)
                await corpo(**kit)
        finally:
            await limpar(r,pref)
    banco_real(cenario)


def test_pedido_concluido_com_agradecimento_aviso_depois_e_retorno_com_resumo(tmp_path):
    async def corpo(pool,c,p,turno,fala,pedido_fechado,atendimentos,pacotes,**_):
        cli='5511900000201'
        conv,numero=await pedido_fechado(cli)
        # Primeiro turno do atendimento: novo=true; os seguintes, false. Sem anterior.
        [a]=await atendimentos(conv)
        assert (a['numero'],a['estado'],a['motivo_inicio'])==(1,'aberto','primeiro_contato')
        # "obrigado, e o horário?" tem pergunta: não encerra.
        await turno(cli,'obrigado, e o horário?',fala('Retirada a partir das 12h.'))
        assert (await atendimentos(conv))[0]['estado']=='aberto'
        assert pacotes[0]['atendimento']['novo'] is False and pacotes[0]['atendimento']['anterior'] is None
        # Só agradecimento, com o agente em silêncio: encerra na hora (E3), sem despedida.
        enviadas=await pool.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida'",T,conv)
        await turno(cli,'obrigado 🙏',fala(None,acao='silencio',motivo='agradecimento'))
        [a]=await atendimentos(conv)
        assert (a['estado'],a['motivo_encerramento'])==('encerrado','pedido_concluido')
        assert await pool.fetchval("SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND direcao='saida'",T,conv)==enviadas
        assert a['resumo'].startswith('Encerrado: pedido concluído') and f'Pedido {numero} (retirada, Pátio Guedala, R$ 68,00)' in a['resumo']
        assert await pool.fetchval('SELECT encerrada_em IS NOT NULL FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
        assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1 AND acao='atendimento.encerrado'",T)==1

        # Aviso de status do pedido depois do encerramento: chega e não reabre (fica sem atendimento até o próximo).
        canal=await pool.fetchval("SELECT id FROM tenant_canais WHERE tenant_id=$1 AND nome='API'",T)
        await p.receber(T,canal,{'identificador':UNIDADE_WHATS,'texto':f'OK {numero}','message_id':'unidade-1','tipo':'texto'})
        from modulos.pedidos.avisos import despachar
        await despachar(p)
        aviso=await pool.fetchrow("SELECT id,atendimento_id FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND origem='sistema' ORDER BY id DESC LIMIT 1",T,conv)
        assert aviso and aviso['atendimento_id'] is None
        assert [x['estado'] for x in await atendimentos(conv)]==['encerrado']

        # "obrigado" respondendo ao aviso (E5): abre atendimento novo normalmente; histórico só dele (o aviso adotado),
        # e o anterior só como resumo, com o pedido e o status atual (9: vale mesmo voltando em menos de 30 min).
        await turno(cli,'obrigado!',fala(None,acao='silencio',motivo='agradecimento'))
        a1,a2=await atendimentos(conv)
        assert (a2['numero'],a2['motivo_inicio'])==(2,'retorno_apos_encerramento')
        b=pacotes[0]['atendimento']
        assert (b['id'],b['numero'],b['novo'],b['motivo_inicio'])==(a2['id'],2,True,'retorno_apos_encerramento')
        assert (b['anterior']['numero'],b['anterior']['motivo_encerramento'],b['anterior']['desfecho'])==(1,'pedido_concluido','pedido_feito')
        assert f'Pedido {numero}' in b['anterior']['resumo'] and 'confirmado pela unidade' in b['anterior']['resumo']
        assert pacotes[0]['cliente']['resumo_anterior']==b['anterior']['resumo']
        assert [h['role'] for h in pacotes[0]['historico']]==['assistant']   # só o aviso do pedido
        assert await pool.fetchval('SELECT atendimento_id FROM mensagens WHERE tenant_id=$1 AND id=$2',T,aviso['id'])==a2['id']
        # O novo atendimento não tem pedido: o "obrigado" não o encerra.
        assert a2['estado']=='aberto'
        # Nenhuma ação de Skill do atendimento anterior aparece como recente.
        assert pacotes[0]['acoes_recentes']==[]
    cenario_by_koji(tmp_path,corpo)


def test_pedido_sem_retorno_30_min_e_corrida_com_mensagem(tmp_path):
    async def corpo(pool,c,p,turno,fala,pedido_fechado,atendimentos,enviar,roteiro,**_):
        async def atrasar(conv):
            """O pedido foi fechado há 31 min e as mensagens anteriores, há 40."""
            await pool.execute("UPDATE pedidos SET criado_em=now()-interval '31 minutes' WHERE tenant_id=$1 AND conversation_id=$2",T,conv)
            await pool.execute("UPDATE mensagens SET criado_em=now()-interval '40 minutes' WHERE tenant_id=$1 AND conversation_id=$2",T,conv)
        # Pedido fechado e nada do cliente por 30 min → pedido_sem_retorno.
        conv,_n=await pedido_fechado('5511900000211')
        assert await p.encerrar_sem_retorno()==0
        await atrasar(conv)
        assert await p.encerrar_sem_retorno()==1
        [a]=await atendimentos(conv)
        assert (a['estado'],a['motivo_encerramento'])==('encerrado','pedido_sem_retorno')
        # Cliente falou depois do fechamento: a regra não vale (o atendimento segue para a inatividade do ENC1-b).
        conv2,_n=await pedido_fechado('5511900000212')
        await turno('5511900000212','tem hashi?',fala('Tem sim.'))
        await pool.execute("UPDATE pedidos SET criado_em=now()-interval '31 minutes' WHERE tenant_id=$1 AND conversation_id=$2",T,conv2)
        await pool.execute("UPDATE mensagens SET criado_em=now()-interval '40 minutes' WHERE tenant_id=$1 AND conversation_id=$2 AND conteudo<>'tem hashi?'",T,conv2)
        assert await p.encerrar_sem_retorno()==0
        # Conversa com atendente: fica como está.
        conv3,_n=await pedido_fechado('5511900000213')
        await atrasar(conv3)
        await pool.execute("UPDATE conversas SET aguardando_atendente_desde=now(),aguardando_motivo='transferido_pelo_agente' WHERE tenant_id=$1 AND conversation_id=$2",T,conv3)
        assert await p.encerrar_sem_retorno()==0 and (await atendimentos(conv3))[0]['estado']=='aberto'

        # Corrida: "obrigado" no lote e outra mensagem chegando antes de o turno concluir → não encerra.
        assert (await c.patch(f'/admin/v1/tenants/{T}/agentes/{A}',json={'refazer_turno':'off'})).status_code==200
        conv4,_n=await pedido_fechado('5511900000214')
        async def chega(payload): await enviar('5511900000214','e o horário?')
        await turno('5511900000214','obrigado',fala(None,chega,acao='silencio',motivo='agradecimento'))
        assert (await atendimentos(conv4))[0]['estado']=='aberto'
    cenario_by_koji(tmp_path,corpo)


def test_encerrar_do_agente_painel_reabrir_e_api(tmp_path):
    async def corpo(pool,c,p,api,ids,turno,fala,atendimentos,pacotes,**_):
        cli='5511900000221'
        conv=await turno(cli,'oi',fala('Olá!'))
        # Agente encerra: motivo concluido, com o desfecho.
        conv=await turno(cli,'só isso, tchau',fala('Até logo!',acao='encerrar',desfecho='duvida_respondida'))
        [a]=await atendimentos(conv)
        assert (a['estado'],a['motivo_encerramento'],a['desfecho'])==('encerrado','concluido','duvida_respondida')
        assert a['resumo']=='Encerrado pelo agente.'
        # Volta: atendimento 2, histórico curto.
        await turno(cli,'oi de novo',fala('Oi!'))
        assert pacotes[0]['historico']==[] and pacotes[0]['atendimento']['anterior']['resumo']=='Encerrado pelo agente.'
        # Painel: lista de conversas com o atendimento atual.
        lista=(await c.get(f'/admin/v1/tenants/{T}/conversas',headers=H)).json()['itens']
        item=next(i for i in lista if i['conversation_id']==conv)
        assert {k:item['atendimento'][k] for k in ('numero','estado','motivo_encerramento')}=={'numero':2,'estado':'aberto','motivo_encerramento':None}
        # Encerrar pelo painel (supervisor para cima; PAPEL1-a), reabrir só o último.
        q=httpx.URL(f'/admin/v1/tenants/{T}/conversas/{conv}').path
        assert (await c.post(q+'/encerrar',json={'motivo':'manual'},headers={'X-Role':'membro'})).status_code==403
        r=await c.post(q+'/encerrar',json={'motivo':'manual'},headers={'X-Role':'supervisor'});assert r.status_code==200,r.text
        assert r.json()['atendimento']['motivo_encerramento']=='manual'
        assert (await c.post(q+'/encerrar',headers=H)).status_code==409
        assert (await c.post(q+'/encerrar',json={'motivo':'outro'},headers=H)).status_code==400
        r=await c.post(q+'/reabrir',headers={'X-Role':'supervisor'});assert r.status_code==200 and r.json()['atendimento']['estado']=='aberto'
        assert (await c.post(q+'/reabrir',headers=H)).status_code==409
        a1,a2=await atendimentos(conv)
        assert (a2['estado'],a2['motivo_inicio'],a2['resumo'])==('aberto','reaberto_manual',None)
        acoes=[x['acao'] for x in await pool.fetch("SELECT acao FROM audit_log WHERE tenant_id=$1 AND acao LIKE 'atendimento.%' ORDER BY id",T)]
        assert acoes==['atendimento.aberto','atendimento.encerrado','atendimento.aberto','atendimento.encerrado','atendimento.reaberto']
        # Histórico de atendimentos e mensagens filtradas por atendimento.
        h=(await c.get(q+'/atendimentos',headers=H)).json()
        assert [i['numero'] for i in h['itens']]==[2,1] and h['itens'][1]['resumo']=='Encerrado pelo agente.' and h['itens'][0]['pedidos']==[]
        assert h['itens'][1]['mensagens']==4
        assert [i['numero'] for i in (await c.get(q+'/atendimentos',params={'limite':1},headers=H)).json()['itens']]==[2]
        assert [i['numero'] for i in (await c.get(q+'/atendimentos',params={'cursor':2},headers=H)).json()['itens']]==[1]
        m=(await c.get(q+'/mensagens',params={'atendimento':a1['id']},headers=H)).json()['itens']
        assert [x['conteudo'] for x in m]==['oi','Olá!','só isso, tchau','Até logo!'] and {x['atendimento_id'] for x in m}=={a1['id']}
        assert (await c.get(q+'/mensagens',params={'atendimento':'x'},headers=H)).status_code==400
    cenario_by_koji(tmp_path,corpo)


def test_novo_persistido_skill_ligada_ao_turno_e_carrinho_anterior(tmp_path):
    async def corpo(pool,c,p,api,ids,canal,enviar,turno,fala,atendimentos,pacotes,roteiro,**_):
        cli='5511900000231'
        # Worker caiu no meio do primeiro turno (turno "processando" largado): o retomado continua novo=true.
        conv=await enviar(cli,'oi')
        [a]=await atendimentos(conv)
        await pool.execute("""INSERT INTO turnos(tenant_id,conversation_id,agente_id,canal_id,request_id,n_mensagens,status,etapa,origem,mensagem_ids)
            VALUES ($1,$2,$3,$4,gen_random_uuid(),1,'em_andamento','processando','cliente','{}')""",T,conv,A,canal)
        # Skill chamada dentro do turno: ligada a ele.
        async def busca(payload):
            assert (await api('GET','/catalogo/busca',params={'conversation_id':payload['conversation_id'],'q':'edamame'})).status_code==200
            assert (await api('POST',f'/carrinho/{payload["conversation_id"]}/itens',json={'item_id':ids['GUE-0001']})).status_code==200
        roteiro[:]=[fala('Temos Edamame.',busca,prompt_versao='v6.7')]
        assert await p.processar_duravel(T,conv)=='concluido'
        assert pacotes[-1]['atendimento']['novo'] is True
        t=await pool.fetchrow("SELECT id,prompt_versao FROM turnos WHERE tenant_id=$1 AND conversation_id=$2 AND etapa='concluido' ORDER BY id DESC LIMIT 1",T,conv)
        assert t['prompt_versao']=='v6.7'
        regs=await pool.fetch('SELECT turno_id,fora_do_turno,atendimento_id FROM skill_registro WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
        assert regs and {(r['turno_id'],r['fora_do_turno'],r['atendimento_id']) for r in regs}=={(t['id'],False,a['id'])}
        ch=await pool.fetch('SELECT turno_id,fora_do_turno FROM skill_chamadas WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
        assert {(r['turno_id'],r['fora_do_turno']) for r in ch}=={(t['id'],False)}
        # Fora do turno (o gateway já desistiu, ou chamada manual): o último turno, marcado.
        assert (await api('GET','/catalogo/busca',params={'conversation_id':conv,'q':'ebi'})).status_code==200
        ultimo=await pool.fetchrow('SELECT turno_id,fora_do_turno FROM skill_registro WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY id DESC LIMIT 1',T,conv)
        assert (ultimo['turno_id'],ultimo['fora_do_turno'])==(t['id'],True)
        reg=(await c.get(f'/admin/v1/tenants/{T}/skills/registro',params={'turno_id':t['id']},headers=H)).json()['itens']
        assert len(reg)==len(regs)+1 and {i['turno_id'] for i in reg}=={t['id']}
        # Segundo turno: novo=false; ação da Skill recente aparece.
        await turno(cli,'quero mais',fala('Certo.'))
        assert pacotes[0]['atendimento']['novo'] is False and pacotes[0]['acoes_recentes']
        assert pacotes[0]['contexto_modulos']['pedidos']['carrinho']['de_atendimento_anterior'] is False
        # Encerrado e de volta: o carrinho guardado vem marcado e as ações do atendimento anterior não aparecem.
        q=httpx.URL(f'/admin/v1/tenants/{T}/conversas/{conv}').path
        assert (await c.post(q+'/encerrar',headers=H)).status_code==200
        await turno(cli,'voltei',fala('Oi de novo!'))
        assert pacotes[0]['contexto_modulos']['pedidos']['carrinho']['de_atendimento_anterior'] is True
        assert pacotes[0]['acoes_recentes']==[] and pacotes[0]['atendimento']['numero']==2
        # Métricas por versão do prompt.
        m=(await c.get(f'/admin/v1/tenants/{T}/metricas',headers=H)).json()['por_versao_do_prompt']
        assert m['v6.7']['turnos']==1 and m['sem_versao']['turnos']>=2
    cenario_by_koji(tmp_path,corpo)


def test_prompt_versao_no_retorno(monkeypatch):
    """Formato v + números e pontos, até 40 caracteres; fora disso (ou ausente) vira nulo sem recusar a resposta. resumo é ignorado."""
    from agentes import skyone
    from test_skyone_adaptador import preparar_agente
    async def validar(url,dominios): pass
    monkeypatch.setattr(skyone.seguranca,'validar_url_saida',validar)
    async def cenario(conn):
        repo,cofre,cfg=await preparar_agente(conn)
        body=await skyone.pacote(repo,'bykoji','bykoji:canal:x',100,'Oi',cfg)
        for versao,esperado in (('v6.7','v6.7'),('v7','v7'),('v1.2.30','v1.2.30'),('6.7',None),('v6.7-beta',None),('v'+'1'*40,None),(67,None),(None,None)):
            extra={} if versao is None else {'prompt_versao':versao}
            def responder(req):
                p=json.loads(req.content)
                return httpx.Response(200,json={'resposta':'Ok','acao':'encerrar','status':'success','tipo':'text','resumo':'texto do agente',
                                                'request_id':p['request_id'],'conversation_id':p['conversation_id'],**extra})
            async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
                r=await skyone.chamar(client,repo,cofre,'bykoji',cfg,body)
            assert r.get('prompt_versao')==esperado and 'resumo' not in r and r['resposta']==['Ok'],versao
    banco(cenario)


def test_migracao_das_conversas_existentes_e_anonimizacao(tmp_path):
    """Migração 045 com dados gravados pela imagem anterior: um atendimento por conversa, aberto ou encerrado com o desfecho de hoje,
    com mensagens, turnos e pedidos ligados. LGPD: a anonimização leva os atendimentos e apaga o resumo."""
    for arquivo in migrar.PASTA.glob('*.sql'):
        if arquivo.name < '045': (tmp_path/arquivo.name).write_bytes(arquivo.read_bytes())
    async def cenario(conn):
        await migrar.aplicar(conn,tmp_path)
        await conn.execute("INSERT INTO tenant_canais(id,tenant_id,nome,tipo,agente_id) VALUES ('can_x','bykoji','C','api','bykoji_atendimento')")
        for conv,fechada in (('bykoji:can_x:1',False),('bykoji:can_x:2',True)):
            await conn.execute("""INSERT INTO conversas(conversation_id,canal,tenant_id,canal_id,agente_id,encerrada_em,desfecho)
                VALUES ($1,'api','bykoji','can_x','bykoji_atendimento',CASE WHEN $2 THEN now() END,CASE WHEN $2 THEN 'duvida_respondida' END)""",conv,fechada)
            await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('bykoji',$1,'entrada','cliente','oi')",conv)
            await conn.execute("INSERT INTO turnos(tenant_id,conversation_id,agente_id,canal_id,request_id,n_mensagens,status) VALUES ('bykoji',$1,'bykoji_atendimento','can_x',gen_random_uuid(),1,'ok')",conv)
        assert (await migrar.aplicar(conn))[0]=='045_enc1a_atendimentos.sql'   # e as seguintes
        rows=await conn.fetch("SELECT conversation_id,numero,estado,motivo_inicio,motivo_encerramento,desfecho,id FROM atendimentos ORDER BY conversation_id")
        assert [tuple(r)[:6] for r in rows]==[('bykoji:can_x:1',1,'aberto','migracao',None,None),('bykoji:can_x:2',1,'encerrado','migracao','concluido','duvida_respondida')]
        for r in rows:
            assert await conn.fetchval('SELECT atendimento_id FROM conversas WHERE conversation_id=$1',r['conversation_id'])==r['id']
            assert await conn.fetchval('SELECT atendimento_id FROM mensagens WHERE conversation_id=$1',r['conversation_id'])==r['id']
            assert await conn.fetchval('SELECT atendimento_id FROM turnos WHERE conversation_id=$1',r['conversation_id'])==r['id']
        # Linha gravada depois (pela imagem anterior, sem saber do atendimento): o gatilho liga ao aberto; encerrado não recebe.
        await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('bykoji','bykoji:can_x:1','saida','bot','olá')")
        await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('bykoji','bykoji:can_x:2','saida','sistema','aviso')")
        assert [r['atendimento_id'] for r in await conn.fetch("SELECT atendimento_id FROM mensagens WHERE conteudo IN ('olá','aviso') ORDER BY id")]==[rows[0]['id'],None]
        # Reaplicar não duplica.
        assert await migrar.aplicar(conn)==[]
    banco(cenario)


def test_anonimizacao_leva_atendimentos_e_apaga_resumo(tmp_path):
    async def corpo(pool,c,turno,fala,atendimentos,**_):
        cli='5511900000241'
        conv=await turno(cli,'oi',fala('Olá!'))
        await turno(cli,'tchau',fala('Até!',acao='encerrar'))
        await turno(cli,'oi de novo',fala('Oi!'))
        assert [a['resumo'] for a in await atendimentos(conv)]==['Encerrado pelo agente.',None]
        cid=await pool.fetchval('SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$2',T,conv)
        r=await c.request('DELETE',f'/admin/v1/tenants/{T}/contatos/{cid}',json={'confirmacao':cli},headers=H);assert r.status_code==200,r.text
        novo=await pool.fetchval('SELECT conversation_id FROM conversas WHERE tenant_id=$1 AND contato_id=$2',T,cid)
        assert novo!=conv and await atendimentos(conv)==[]
        depois=await atendimentos(novo)
        assert [(a['numero'],a['resumo']) for a in depois]==[(1,None),(2,None)]
    cenario_by_koji(tmp_path,corpo)
