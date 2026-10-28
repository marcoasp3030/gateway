"""Nenhum teste pode usar um banco de produção."""
import os
from urllib.parse import unquote, urlsplit

import pytest


def pytest_configure(config):
    for nome in ('DATABASE_URL', 'MIGRATION_TEST_DATABASE_URL'):
        valor = os.getenv(nome)
        if valor and not unquote(urlsplit(valor).path).removeprefix('/').endswith('_test'):
            raise pytest.UsageError(f'{nome} precisa apontar para banco terminado em _test')


@pytest.fixture(autouse=True)
def _escopo_da_empresa_desligado(monkeypatch):
    """PAPEL1-a ligou ESCOPO_EMPRESA_OBRIGATORIO por padrão. Os testes antigos das rotas mandam admin/membro sem X-Tenant-Scope:
    aqui a exigência fica desligada; os testes de escopo e de papel (test_escopo_obrigatorio, test_multi1a, test_papel1a) ligam."""
    monkeypatch.setenv('ESCOPO_EMPRESA_OBRIGATORIO', 'off')
