"""Migração 014: estados do turno, envio por parte, aguardando atendente e callback."""
import uuid
from datetime import timedelta
import asyncpg,pytest
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')


async def base(conn):
    await migrar.aplicar(conn);app,c=preparar(conn)
    async with c:
        a,can=await criar(c);b,_=await criar(c,'bravo')
    conv=f'alpha:{can}:5511999990000'
    await conn.execute("INSERT INTO conversas(conversation_id,canal,tenant_id,agente_id,canal_id) VALUES ($1,'api','alpha',$2,$3)",conv,a,can)
    return a,b,can,conv


async def falha(conn,classe,sql,*args):
    async with conn.transaction():
        try: await conn.execute('SAVEPOINT s'); await conn.execute(sql,*args)
        except classe: await conn.execute('ROLLBACK TO SAVEPOINT s'); return True
    return False


def test_014_turno_compativel_e_etapas_validas():
    async def cenario(conn):
        a,_,can,conv=await base(conn)
        # Gravação no formato atual (sem etapa) continua valendo e nasce concluída.
        rid=uuid.uuid4()
        await conn.execute("INSERT INTO turnos(tenant_id,conversation_id,agente_id,canal_id,request_id,n_mensagens,status) VALUES ('alpha',$1,$2,$3,$4,1,'ok')",conv,a,can,rid)
        assert await conn.fetchval('SELECT etapa FROM turnos WHERE request_id=$1',rid)=='concluido'
        for etapa in ('processando','respondido','aguardando_callback','abandonado'):
            await conn.execute('UPDATE turnos SET etapa=$2 WHERE request_id=$1',rid,etapa)
        assert await falha(conn,asyncpg.CheckViolationError,"UPDATE turnos SET etapa='inventada' WHERE request_id=$1",rid)
        # request_id é único: um turno não pode ser aberto duas vezes.
        assert await falha(conn,asyncpg.UniqueViolationError,"INSERT INTO turnos(tenant_id,conversation_id,agente_id,canal_id,request_id,n_mensagens,status) VALUES ('alpha',$1,$2,$3,$4,1,'ok')",conv,a,can,rid)
        await conn.execute('UPDATE turnos SET mensagem_ids=$2 WHERE request_id=$1',rid,[1,2,3])
    banco(cenario)


def test_014_INV01_parte_de_resposta_gravada_uma_vez():
    async def cenario(conn):
        _,_,_,conv=await base(conn);rid=uuid.uuid4()
        sql="INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,turno_request_id,parte,entrega) VALUES ('alpha',$1,'saida','bot','oi',$2,$3,$4)"
        await conn.execute(sql,conv,rid,0,'enviando')
        assert await falha(conn,asyncpg.UniqueViolationError,sql,conv,rid,0,'enviando')
        await conn.execute(sql,conv,rid,1,'enviada')
        await conn.execute("UPDATE mensagens SET entrega='incerta' WHERE turno_request_id=$1 AND parte=0",rid)
        assert await falha(conn,asyncpg.CheckViolationError,"UPDATE mensagens SET entrega='perdida' WHERE turno_request_id=$1",rid)
        # Parte sem turno (ou turno sem parte) é recusada; saída antiga sem os dois continua aceita.
        assert await falha(conn,asyncpg.CheckViolationError,"INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo,parte) VALUES ('alpha',$1,'saida','bot','x',0)",conv)
        await conn.execute("INSERT INTO mensagens(tenant_id,conversation_id,direcao,origem,conteudo) VALUES ('alpha',$1,'saida','bot','formato atual')",conv)
    banco(cenario)


def test_014_aguardando_atendente_coerente():
    async def cenario(conn):
        _,_,_,conv=await base(conn)
        for motivo in ('erro_agente','tempo_esgotado','circuito_aberto','entrega_incerta'):
            await conn.execute("UPDATE conversas SET aguardando_atendente_desde=now(),aguardando_motivo=$2 WHERE conversation_id=$1",conv,motivo)
        await conn.execute("UPDATE conversas SET aguardando_atendente_desde=NULL,aguardando_motivo=NULL WHERE conversation_id=$1",conv)
        assert await falha(conn,asyncpg.CheckViolationError,"UPDATE conversas SET aguardando_motivo='erro_agente' WHERE conversation_id=$1",conv)
        assert await falha(conn,asyncpg.CheckViolationError,"UPDATE conversas SET aguardando_atendente_desde=now(),aguardando_motivo='qualquer' WHERE conversation_id=$1",conv)
    banco(cenario)


def test_014_INV08_callback_so_com_agente_do_mesmo_tenant():
    async def cenario(conn):
        a,b,_,conv=await base(conn)
        sql="INSERT INTO agente_callbacks(request_id,tenant_id,agente_id,conversation_id,token_hash,expira_em) VALUES ($1,'alpha',$2,$3,'hash',now()+$4)"
        rid=uuid.uuid4()
        await conn.execute(sql,rid,a,conv,timedelta(minutes=5))
        assert await conn.fetchval('SELECT recebido_em FROM agente_callbacks WHERE request_id=$1',rid) is None
        assert await falha(conn,asyncpg.UniqueViolationError,sql,rid,a,conv,timedelta(minutes=5))
        # Agente de outro tenant não pode ser referenciado.
        assert await falha(conn,asyncpg.ForeignKeyViolationError,sql,uuid.uuid4(),b,conv,timedelta(minutes=5))
        assert await falha(conn,asyncpg.CheckViolationError,sql,uuid.uuid4(),a,conv,timedelta(minutes=-1))
    banco(cenario)
