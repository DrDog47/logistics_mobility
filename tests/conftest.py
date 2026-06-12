"""Shared pytest fixtures."""

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db


@pytest.fixture
def app():
    app = create_app(config=TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clear_recognition_cache():
    """Reset the inbox recognition cache so it never leaks between tests."""
    from app.docs.pipeline import clear_recognition_cache

    clear_recognition_cache()
    yield
    clear_recognition_cache()
