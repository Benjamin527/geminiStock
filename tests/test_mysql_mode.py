from pathlib import Path

from gemini_stock.config import Settings
from gemini_stock.main import maybe_sync_remote_mysql
from gemini_stock.storage.db import Database


def test_maybe_sync_remote_mysql_calls_syncer(monkeypatch, tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()
    captured = {}

    class FakeSyncer:
        def __init__(self, config):
            captured["config"] = config

        def sync_from_sqlite(self, sqlite_path):
            captured["sqlite_path"] = Path(sqlite_path)
            return {"features": 1}

    monkeypatch.setattr("gemini_stock.main.MySQLSync", FakeSyncer)

    settings = Settings(
        sync_remote_mysql=True,
        remote_mysql_host="127.0.0.1",
        remote_mysql_port=3306,
        remote_mysql_user="root",
        remote_mysql_password="secret",
        remote_mysql_database="gemini_stock",
    )

    maybe_sync_remote_mysql(settings, db)

    assert captured["config"].host == "127.0.0.1"
    assert captured["config"].database == "gemini_stock"
    assert captured["sqlite_path"] == db.path
