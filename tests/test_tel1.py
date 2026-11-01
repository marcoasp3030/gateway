"""TEL1: lista do piloto e contatos de alerta normalizados para 55 + DDD + número (sem espaços, traços, parênteses e +);
10 ou 11 dígitos ganham o 55, 12 ou 13 começando com 55 ficam, e o resultado precisa ser número brasileiro (DDD 11 a 99 sem zero;
celular com 9 dígitos começando com 9 ou fixo com 8 começando com 2 a 5); o resto é recusado com 400 e "Use DDD + número, ex.: 11912345678".
A migração 055 aplica a mesma regra aos registros já gravados, sem repetir número na lista do piloto."""
import shutil
import pytest
from test_admin import preparar,criar
from test_migrations import banco,migrar,DSN
from nucleo.telefone import telefone_br,contato_do_piloto,lista_do_piloto

pytestmark=pytest.mark.skipif(not DSN,reason='Banco de testes obrigatório')
MSG='Use DDD + número, ex.: 11912345678'


def test_regra():
    for entrada,saida in (('11912345678','5511912345678'),('1133334444','551133334444'),('+55 (11) 91234-5678','5511912345678'),
                          ('5511912345678','5511912345678'),('551133334444','551133334444'),(' (11) 3333-4444 ','551133334444')):
        assert telefone_br(entrada)==saida,entrada
    for ok in ('99912345678','1122223333','1155556666','5521912345678'): assert telefone_br(ok),ok
    for ruim in ('912345678','4411912345678','55119123456789','11.91234.5678','11 9123a5678','','１１９１２３４５６７８',None,11912345678,
                 '0191234-5678','1091234-5678','5501912345678','5510912345678',      # DDD com zero
                 '11812345678','11312345678','5511812345678',                         # 9 dígitos sem o 9 na frente
                 '1166667777','1177778888','1188889999','1191234567','1112345678','551191234567'):   # fixo fora de 2 a 5
        assert telefone_br(ruim) is None,ruim
    assert contato_do_piloto('teste-marco-01')=='teste-marco-01' and contato_do_piloto('teste marco') is None
    assert lista_do_piloto(['11912345678','5511912345678','+55 11 91234-5678','teste-01'])==['5511912345678','teste-01']
    assert lista_do_piloto(['11912345678','123']) is None


def test_api_normaliza_e_recusa_com_a_mensagem():
    async def cenario(conn):
        await migrar.aplicar(conn);app,c=preparar(conn)
        async with c:
            a,_=await criar(c);q=f'/admin/v1/tenants/alpha/agentes/{a}'
            linha=lambda: conn.fetchrow('SELECT contatos_permitidos,alerta_telefone,transbordo_alerta FROM agentes WHERE id=$1',a)
            # Ficha do agente: normaliza e tira repetidos; identificador do canal api fica.
            r=await c.patch(q,json={'contatos_permitidos':['11912345678','+55 (11) 91234-5678','teste-marco-01','(21) 3333-4444'],
                                    'alerta_telefone':'11 98888-7777','transbordo_alerta':'(11) 97777-6666'})
            assert r.status_code==200,r.text
            assert dict(await linha())=={'contatos_permitidos':['5511912345678','teste-marco-01','552133334444'],
                                         'alerta_telefone':'5511988887777','transbordo_alerta':'5511977776666'}
            # Fora da regra: 400 com a mensagem e o campo; nada muda.
            for corpo,campo in (({'contatos_permitidos':['5511912345678','912345678']},'contatos_permitidos'),
                                ({'contatos_permitidos':['4411912345678']},'contatos_permitidos'),
                                ({'contatos_permitidos':['5511812345678']},'contatos_permitidos'),
                                ({'alerta_telefone':'(01) 91234-5678'},'alerta_telefone'),({'transbordo_alerta':'11 7777-6666'},'transbordo_alerta'),
                                ({'alerta_telefone':'+1 415 555 26710'},'alerta_telefone'),({'transbordo_alerta':'55119123456789'},'transbordo_alerta'),
                                ({'transbordo_alerta':''},'transbordo_alerta')):
                r=await c.patch(q,json=corpo)
                assert r.status_code==400,corpo
                assert r.json()['erro']['mensagem']==MSG and r.json()['erro']['detalhes']['campo']==campo,r.json()
            assert (await linha())['contatos_permitidos']==['5511912345678','teste-marco-01','552133334444']
            # Alerta vazio limpa (como antes).
            assert (await c.patch(q,json={'alerta_telefone':''})).status_code==200 and (await linha())['alerta_telefone'] is None
            # Configuração da empresa e alerta de transbordo da empresa.
            r=await c.put('/admin/v1/tenants/alpha/config',json={'contatos_permitidos':['11 91234-5678','5511912345678']})
            assert r.status_code==200,r.text
            assert await conn.fetchval("SELECT contatos_permitidos FROM tenant_config WHERE tenant_id='alpha'")==['5511912345678']
            r=await c.put('/admin/v1/tenants/alpha/config',json={'alerta_telefone':'11-9999'})
            assert (r.status_code,r.json()['erro']['mensagem'],r.json()['erro']['detalhes']['campo'])==(400,MSG,'alerta_telefone')
            assert (await c.patch('/admin/v1/tenants/alpha',json={'whatsapp_alerta_transbordo':'(11) 90000-0010'})).status_code==200
            assert await conn.fetchval("SELECT whatsapp_alerta_transbordo FROM tenants WHERE id='alpha'")=='5511900000010'
            r=await c.patch('/admin/v1/tenants/alpha',json={'whatsapp_alerta_transbordo':'900000010'})
            assert (r.status_code,r.json()['erro']['mensagem'],r.json()['erro']['detalhes']['campo'])==(400,MSG,'whatsapp_alerta_transbordo')
            # Outros erros de validação continuam com a mensagem de sempre.
            r=await c.patch(q,json={'debounce_s':99});assert r.status_code==400 and r.json()['erro']['mensagem']=='Confira os campos informados.'
    banco(cenario)


def test_migracao_055_normaliza_os_gravados_sem_repetir(tmp_path):
    async def cenario(conn):
        ate=tmp_path/'ate54';ate.mkdir()
        for f in sorted(migrar.PASTA.glob('*.sql')):
            if int(f.name[:3])<=54: shutil.copy(f,ate/f.name)
        await migrar.aplicar(conn,ate);app,c=preparar(conn)
        async with c: a,_=await criar(c)
        await conn.execute("""UPDATE agentes SET contatos_permitidos=ARRAY['11912345678','5511912345678','teste-marco-01','21 3333-4444','912345678','1177776666'],
            alerta_telefone='11988887777',transbordo_alerta='5511977776666' WHERE id=$1""",a)
        await conn.execute("UPDATE tenant_config SET contatos_permitidos=ARRAY['+55 (11) 91234-5678'],transbordo_alerta='1133334444' WHERE tenant_id='alpha'")
        await conn.execute("UPDATE tenants SET whatsapp_alerta_transbordo='11900000010' WHERE id='alpha'")
        assert (await migrar.aplicar(conn))[0]=='055_telefone_com_55.sql'
        r=await conn.fetchrow('SELECT contatos_permitidos,alerta_telefone,transbordo_alerta FROM agentes WHERE id=$1',a)
        # Fora da regra (9 dígitos; fixo começando com 7) fica como estava: a migração não inventa número.
        assert dict(r)=={'contatos_permitidos':['5511912345678','teste-marco-01','552133334444','912345678','1177776666'],
                         'alerta_telefone':'5511988887777','transbordo_alerta':'5511977776666'}
        r=await conn.fetchrow("SELECT contatos_permitidos,transbordo_alerta FROM tenant_config WHERE tenant_id='alpha'")
        assert dict(r)=={'contatos_permitidos':['5511912345678'],'transbordo_alerta':'551133334444'}
        assert await conn.fetchval("SELECT whatsapp_alerta_transbordo FROM tenants WHERE id='alpha'")=='5511900000010'
    banco(cenario)
