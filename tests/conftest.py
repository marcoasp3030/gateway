"""Nenhum teste pode usar um banco de produção."""
import os
from urllib.parse import unquote, urlsplit

import pytest


def pytest_configure(config):
    for nome in ('DATABASE_URL', 'MIGRATION_TEST_DATABASE_URL'):
        valor = os.getenv(nome)
        if valor and not unquote(urlsplit(valor).path).removeprefix('/').endswith('_test'):
            raise pytest.UsageError(f'{nome} precisa apontar para banco terminado em _test')
