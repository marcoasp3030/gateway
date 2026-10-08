from cryptography.fernet import Fernet
import pytest
from nucleo.credenciais import Cofre, CredencialInvalida
from nucleo.banco import Repositorio
from nucleo.configuracao import Configuracoes
from test_migrations import banco, migrar, DSN


def test_INV13_fernet_tenant_e_integridade():
    cofre = Cofre(Fernet.generate_key())
    valor = cofre.cifrar('a',{'token':'segredo-real'})
    assert 'segredo-real' not in valor
    assert cofre.abrir('a',valor) == {'token':'segredo-real'}
    for tenant,cifrado in [('b',valor),('a',valor[:-3]+'xxx')]:
        with pytest.raises(CredencialInvalida): cofre.abrir(tenant,cifrado)
    with pytest.raises(CredencialInvalida): Cofre('incorreta')


@pytest.mark.skipif(not DSN, reason='Postgres de teste obrigatório')
def test_INV08_acesso_config_cache_invalidacao():
    async def cenario(conn):
        await migrar.aplicar(conn)
        repo = Repositorio(conn)
        cfg = Configuracoes(repo)
        await repo.alterar('bykoji','agentes','bykoji_atendimento',{'send_mode':None,'history_turns':3})
        await repo.alterar('bykoji','tenant_config','bykoji',{'history_turns':8,'debounce_s':2})
        result = await cfg.efetiva('bykoji','bykoji_atendimento')
        assert result['history_turns']==3 and result['debounce_s']==2 and result['send_mode']=='dry'
        assert result['origem_config']['send_mode']=='modelo'
        await repo.alterar('bykoji','agentes','bykoji_atendimento',{'history_turns':7})
        assert (await cfg.efetiva('bykoji','bykoji_atendimento'))['history_turns']==3
        cfg.invalidar('bykoji')
        assert (await cfg.efetiva('bykoji','bykoji_atendimento'))['history_turns']==7
        result['contatos_permitidos'].append('mutacao')
        assert (await cfg.efetiva('bykoji','bykoji_atendimento'))['contatos_permitidos']==[]
        assert await repo.obter('outro','agentes','bykoji_atendimento') is None
        assert await repo.alterar('outro','agentes','bykoji_atendimento',{'nome':'errado'}) is None
        assert await repo.listar('outro','agentes') == []
        with pytest.raises(ValueError): await repo.obter('','agentes','x')
        with pytest.raises(LookupError): await cfg.efetiva('outro','bykoji_atendimento')
        cfg.relogio = lambda: float('inf')
        await repo.alterar('bykoji','agentes','bykoji_atendimento',{'history_turns':9})
        assert (await cfg.efetiva('bykoji','bykoji_atendimento'))['history_turns']==9
    banco(cenario)
