from gemini_stock.config import Settings


def test_remote_mysql_settings_are_not_part_of_active_business_config():
    settings = Settings(sync_remote_mysql=True, dashboard_data_source="mysql")

    assert settings.dashboard_data_source == "sqlite"
    assert not hasattr(settings, "sync_remote_mysql")
