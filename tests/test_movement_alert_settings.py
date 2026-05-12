from gemini_stock.storage.db import Database


def test_database_returns_default_movement_alert_thresholds(tmp_path):
    db = Database(tmp_path / "settings.db")
    db.initialize()

    assert db.get_movement_alert_thresholds() == [1.2, 2.0, 3.0]
    assert db.get_movement_alert_thresholds("fast_drop") == [1.2, 2.0, 3.0]
    assert db.get_movement_alert_thresholds("fast_rise") == [1.2, 2.0, 3.0]


def test_database_saves_movement_alert_thresholds(tmp_path):
    db = Database(tmp_path / "settings.db")
    db.initialize()

    saved = db.save_movement_alert_thresholds([0.8, 1.6, 2.4])

    assert saved == [0.8, 1.6, 2.4]
    assert db.get_movement_alert_thresholds() == [0.8, 1.6, 2.4]
    assert db.get_movement_alert_thresholds("fast_drop") == [0.8, 1.6, 2.4]


def test_database_can_save_separate_rise_and_drop_thresholds(tmp_path):
    db = Database(tmp_path / "settings.db")
    db.initialize()

    saved = db.save_movement_alert_thresholds(
        {
            "fast_drop": [3.0, 6.0, 10.0],
            "fast_rise": [2.0, 4.0, 8.0],
        }
    )

    assert saved == {
        "fast_drop": [3.0, 6.0, 10.0],
        "fast_rise": [2.0, 4.0, 8.0],
    }
    assert db.get_movement_alert_thresholds("fast_drop") == [3.0, 6.0, 10.0]
    assert db.get_movement_alert_thresholds("fast_rise") == [2.0, 4.0, 8.0]


def test_database_rejects_non_ascending_movement_thresholds(tmp_path):
    db = Database(tmp_path / "settings.db")
    db.initialize()

    try:
        db.save_movement_alert_thresholds([2.0, 1.0, 3.0])
    except ValueError as exc:
        assert "ascending" in str(exc)
    else:
        raise AssertionError("expected non-ascending thresholds to fail")
