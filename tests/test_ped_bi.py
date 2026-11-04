"""PED-BI (docs/ESPEC_PED_BI.md): visão de vendas da empresa. Só gestor e acima; venda é confirmado em diante;
teste/piloto/simulado fora por padrão; pedido anonimizado entra nos totais e fica fora do ranking de clientes."""
import json
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from test_migrations import banco,migrar,DSN
from test_admin import preparar
from test_pipeline_multi import RedisFila
from test_fase4_pedidos import montar

pytestmark=pytest.mark.skipif(not DSN,reason='Postgres de teste obrigatório')
T='bykoji'
SP=ZoneInfo('America/Sao_Paulo')
B=f'/admin/v1/tenants/{T}/pedidos/indicadores'
OUT='?de=2026-10-01&ate=2026-10-31'


def test_visao_de_vendas_numeros_papeis_e_filtros():
    async def cenario(conn):
        await migrar.aplicar(conn)
        app,client=preparar(conn);fila=RedisFila();app.state.redis=fila;app.state.pipeline.redis=fila
        async with client:
            conversa,ids,_,_=await montar(conn,client,app)
            item={c:(ids[c],n,cat) for c,n,cat in (('GUE-0001','Edamame','Entradas quentes'),('GUE-0002','Ebi no Tempurá','Pratos quentes'),
                                                   ('GUE-0007','Purin','Sobremesas'))}
            seq=iter(range(1000,9999))

            async def pedido(conv,quando,linhas,status='concluido',tel=None,nome=None,modalidade='retirada',taxa=0,forma='pix',bairro=None,
                             origem='real',motivo=None,frete=False,eventos=()):
                itens=[{'item_id':item[c][0],'codigo':c,'nome':item[c][1],'categoria':item[c][2],'quantidade':q,'preco_unitario':v/q,'subtotal':v}
                       for c,q,v in linhas]
                sub=sum(v for _,_,v in linhas)
                pid=await conn.fetchval("""INSERT INTO pedidos(tenant_id,numero,conversation_id,unidade,modalidade,itens,subtotal,taxa_entrega,total,status,
                        criado_em,cliente_nome,cliente_telefone,forma_pagamento,pagamento_rotulo,bairro,origem,motivo,frete_gratis)
                    VALUES ($1,$2,$3,'patio_guedala',$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18) RETURNING id""",
                    T,f'BI{next(seq)}',conv,modalidade,json.dumps(itens),sub,taxa,sub+taxa,status,quando,nome,tel,forma,
                    {'pix':'Pix','dinheiro':'Dinheiro'}.get(forma),bairro,origem,motivo,frete)
                for para,minutos in eventos:
                    await conn.execute("INSERT INTO pedido_eventos(tenant_id,pedido_id,para,por,origem,criado_em) VALUES ($1,$2,$3,'teste','painel',$4)",
                                       T,pid,para,quando+timedelta(minutes=minutos))
            d=lambda m,dia,h=12,mi=30: datetime(2026,m,dia,h,mi,tzinfo=SP)
            a=await conversa('cliente-bi-a');b=await conversa('cliente-bi-b');c=await conversa('cliente-bi-c');anon=await conversa('cliente-bi-d')
            A=dict(tel='5511900000081',nome='Cliente Teste A');Bc=dict(tel='5511900000082',nome='Cliente Teste B');C=dict(tel='5511900000083',nome='Cliente Teste C')
            # Outubro (período): A compra 3 vezes, B 1 vez (nova), um pedido anonimizado; fora da venda: aguardando, recusado, cancelado; um de teste.
            await pedido(a,d(10,1),[('GUE-0001',2,40)],modalidade='delivery',taxa=10,bairro='Centro',
                         eventos=(('confirmado',5),('saiu_entrega',25),('concluido',60)),**A)
            await pedido(a,d(10,8,19),[('GUE-0002',1,30)],forma='dinheiro',**A)
            await pedido(a,d(10,20),[('GUE-0001',1,20)],status='confirmado',**A)
            await pedido(b,d(10,15,20),[('GUE-0002',2,60),('GUE-0007',1,40)],modalidade='delivery',bairro='centro ',frete=True,eventos=(('confirmado',15),),**Bc)
            await pedido(anon,d(10,25),[('GUE-0007',1,10)],forma=None)
            await conn.execute('UPDATE conversas SET contato_id=NULL WHERE conversation_id=$1',anon)
            await pedido(b,d(10,16),[('GUE-0001',1,15)],status='recusado',motivo='sem entregador',**Bc)
            await pedido(b,d(10,17),[('GUE-0001',1,25)],status='cancelado',**Bc)
            await pedido(a,d(10,31,22),[('GUE-0002',1,40)],status='aguardando_loja',**A)
            await pedido(a,d(10,10),[('GUE-0002',1,999)],origem='teste',**A)
            # Antes: A em setembro (período anterior); C só em julho (sumido).
            await pedido(a,d(9,10),[('GUE-0001',1,20)],**A)
            await pedido(c,d(7,1),[('GUE-0002',1,30)],**C);await pedido(c,d(7,10),[('GUE-0002',1,30)],**C)

            for papel in ('supervisor','funcionario'):
                r=await client.get(B+OUT,headers={'X-Role':papel,'X-Actor':papel+'@exemplo.test'})
                assert (r.status_code,r.json()['erro']['detalhes']['motivo'])==(403,'papel_insuficiente'),papel
            r=await client.get(B+OUT,headers={'X-Role':'gestor','X-Actor':'gestor@exemplo.test'});assert r.status_code==200,r.text
            v=r.json();res=v['resumo']
            assert v['periodo']=={'de':'2026-10-01','ate':'2026-10-31','dias':31,'agrupar':'dia','fuso':'America/Sao_Paulo',
                                  'anterior':{'de':'2026-08-31','ate':'2026-09-30'}}
            assert (res['faturamento'],res['pedidos'],res['ticket_medio'],res['produtos'],res['taxas_entrega'],res['frete_gratis'],res['itens_vendidos'])==(210.0,5,42.0,200.0,10.0,1,8)
            assert (res['clientes'],res['clientes_novos'],res['clientes_recorrentes'],res['recebidos'])==(2,1,1,8)
            assert res['modalidade']=={'delivery':{'pedidos':2,'faturamento':150.0},'retirada':{'pedidos':3,'faturamento':60.0}}
            assert res['fora_da_venda']=={'aguardando_loja':{'pedidos':1,'valor':40.0},'recusado':{'pedidos':1,'valor':15.0},'cancelado':{'pedidos':1,'valor':25.0}}
            assert res['perda_pct']==28.6
            assert (v['anterior']['faturamento'],v['anterior']['pedidos'])==(20.0,1) and v['variacao_pct']['faturamento']==950.0
            assert len(v['evolucao'])==31 and sum(e['pedidos'] for e in v['evolucao'])==5
            assert v['evolucao'][0]=={'inicio':'2026-10-01','pedidos':1,'faturamento':50.0}
            assert {'dia_semana':4,'hora':12,'pedidos':1,'faturamento':50.0} in v['horarios']
            # Produtos e curva ABC.
            p=[(i['codigo'],i['quantidade'],i['faturamento'],i['curva']) for i in v['produtos']['itens']]
            assert p==[('GUE-0002',3,90.0,'A'),('GUE-0001',3,60.0,'A'),('GUE-0007',2,50.0,'C')],p
            assert v['produtos']['itens'][1]['quantidade_anterior']==1 and v['produtos']['itens'][1]['variacao_quantidade']==200.0
            assert v['produtos']['curva']=={'A':2,'B':0,'C':1} and v['produtos']['categorias'][0]=={'categoria':'Pratos quentes','quantidade':3,'faturamento':90.0}
            sem=v['produtos']['sem_venda'];codigos=[i['codigo'] for i in sem['itens']]
            assert 'GUE-0003' in codigos and not {'GUE-0001','GUE-0002','GUE-0007','GUE-0005'}&set(codigos)
            assert sem['total']==await conn.fetchval("SELECT count(*) FROM catalogo_itens WHERE tenant_id=$1 AND tipo='item' AND ativo",T)-3
            # Clientes: nome e telefone completo; o anonimizado fica fora; C sumido.
            cl=v['clientes']
            assert [(x['nome'],x['telefone'],x['pedidos'],x['faturamento']) for x in cl['por_valor']]==[('Cliente Teste A','+5511900000081',3,100.0),
                                                                                                    ('Cliente Teste B','+5511900000082',1,100.0)]
            assert [x['nome'] for x in cl['por_frequencia']]==['Cliente Teste A','Cliente Teste B']
            assert cl['sumidos']['total']==1 and cl['sumidos']['itens'][0]['nome']=='Cliente Teste C' and cl['sumidos']['itens'][0]['pedidos']==2
            assert cl['sumidos']['itens'][0]['dias_sem_comprar']==113
            assert [(x['forma'],x['pedidos']) for x in v['pagamentos']]==[('pix',3),('dinheiro',1),('nao_informado',1)]
            assert v['bairros']==[{'bairro':'Centro','pedidos':2,'faturamento':150.0,'taxa_media':5.0}]
            op=v['operacao']
            assert op['aceite']=={'mediana_min':10.0,'p90_min':14.0,'pedidos':2} and op['preparo']['mediana_min']==20.0 and op['total']['mediana_min']==60.0
            assert {'status':'recusado','motivo':'sem entregador','pedidos':1} in op['motivos_perda']
            assert v['unidades']==[{'unidade':'patio_guedala','nome':'Pátio Guedala','pedidos':5,'faturamento':210.0,'ticket_medio':42.0,'recebidos':8}]
            # Filtros: origem teste entra só quando pedida; outra unidade vem zerada; recusas de validação.
            assert (await client.get(B+OUT+'&origem=real,teste')).json()['resumo']['pedidos']==6
            z=(await client.get(B+OUT+'&unidade=morumbi')).json();assert (z['resumo']['pedidos'],z['resumo']['ticket_medio'],z['clientes']['por_valor'])==(0,0.0,[])
            assert (await client.get(B+'?de=2026-01-01&ate=2026-12-31')).json()['periodo']['agrupar']=='mes'
            for q,campo in (('?de=2026-10-02&ate=2026-10-01','de'),('?de=2025-01-01&ate=2026-10-01','periodo'),(OUT+'&origem=xyz','origem')):
                r=await client.get(B+q);assert (r.status_code,r.json()['erro']['detalhes']['campo'])==(400,campo),q
            # Padrão: últimos 30 dias.
            assert (await client.get(B)).json()['periodo']['dias']==30
    banco(cenario)
