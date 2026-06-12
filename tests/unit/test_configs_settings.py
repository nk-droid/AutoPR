import configs.settings as settings

def test_get_settings_returns_singleton() -> None:
    settings._settings = None
    first = settings.get_settings()
    second = settings.get_settings()
    assert first is second
    assert first.app_name == "AutoPR"
    assert first.database.url
    assert first.database.async_url
