"""Shared fixtures: every test gets an isolated temp database."""
import pytest

from backend import config, db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)
    return db_path
