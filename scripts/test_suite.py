"""Executado pelo test_suite.sh dentro de container temporário."""
import os
from urllib.parse import urlsplit
import pytest
os.environ['MIGRATION_TEST_DATABASE_URL'] = urlsplit(os.environ['DATABASE_URL'])._replace(path='/gateway_migrations_test').geturl()
# SUITE_ALVO só existe para provar a trava (scripts/suite_travada.sh) num alvo pequeno; a implantação roda sempre tudo.
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','-rfEs',os.environ.get('SUITE_ALVO') or 'tests']))
