"""Executado pelo test_suite.sh dentro de container temporário."""
import os
from urllib.parse import urlsplit
import pytest
os.environ['MIGRATION_TEST_DATABASE_URL'] = urlsplit(os.environ['DATABASE_URL'])._replace(path='/gateway_migrations_test').geturl()
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','tests']))
