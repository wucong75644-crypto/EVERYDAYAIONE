"""Database test defaults must never turn into application-database access."""
from unittest.mock import Mock

import pytest

from tests import test_chat_image_lifecycle_postgres as fixtures


@pytest.mark.parametrize('dsn', [
    'host=production.example dbname=app',
    'host=127.0.0.1 port=55439 dbname=app',
    'host=/private/tmp port=5432 dbname=app',
])
def test_explicit_database_override_keeps_its_original_gate(monkeypatch, dsn):
    monkeypatch.setenv('CHAT_IMAGE_TEST_ADMIN_DSN', dsn)
    connect = Mock(side_effect=AssertionError('No SQL allowed'))
    monkeypatch.setattr(fixtures.psycopg, 'connect', connect)
    request = Mock()
    generator = fixtures.isolated_db.__wrapped__(request)
    with pytest.raises(AssertionError):
        next(generator)
    connect.assert_not_called()
    request.getfixturevalue.assert_not_called()


def test_default_requires_owned_socket_and_never_falls_back_to_application_url(monkeypatch):
    monkeypatch.delenv('CHAT_IMAGE_TEST_ADMIN_DSN', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://forbidden.example/production')
    connect = Mock(side_effect=AssertionError('No SQL allowed'))
    monkeypatch.setattr(fixtures.psycopg, 'connect', connect)
    request = Mock()
    request.getfixturevalue.side_effect = RuntimeError('private socket requested')
    generator = fixtures.isolated_db.__wrapped__(request)
    with pytest.raises(RuntimeError, match='private socket requested'):
        next(generator)
    request.getfixturevalue.assert_called_once_with('chat_image_postgres_socket')
    connect.assert_not_called()
