from pathlib import Path

from gemini_stock.security import find_secret_config_warnings


def test_secret_config_warnings_detect_real_looking_env_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=sk-live-looking-value",
                "FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/token",
                "REMOTE_MYSQL_PASSWORD=",
            ]
        ),
        encoding="utf-8",
    )

    warnings = find_secret_config_warnings(env_file)

    assert "OPENAI_API_KEY" in warnings
    assert "FEISHU_WEBHOOK_URL" in warnings
    assert "REMOTE_MYSQL_PASSWORD" not in warnings


def test_secret_config_warnings_ignore_example_placeholders(tmp_path):
    env_file = tmp_path / ".env.example"
    env_file.write_text("OPENAI_API_KEY=\nFEISHU_WEBHOOK_URL=\n", encoding="utf-8")

    assert find_secret_config_warnings(env_file) == []


def test_secret_config_warnings_detect_runtime_environment(monkeypatch, tmp_path):
    missing_file = tmp_path / ".env"
    monkeypatch.setenv("POLYGON_API_KEY", "real-looking-polygon-key")

    warnings = find_secret_config_warnings(missing_file)

    assert "POLYGON_API_KEY" in warnings
