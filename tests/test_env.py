"""API key loading: the key in .env must win over an old one in the system environment."""

import matcher.__main__ as cli


def test_dotenv_key_overrides_a_stale_system_key(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-new-key\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-old-key")
    assert cli._load_env() == ".env"
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-new-key"
    assert "different ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_dotenv_with_windows_bom_and_quotes_is_read(tmp_path, monkeypatch):
    (tmp_path / ".env").write_bytes('﻿ANTHROPIC_API_KEY="sk-ant-quoted"\n'.encode("utf-8"))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cli._load_env()
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-quoted"


def test_auth_and_billing_errors_get_plain_english_hints():
    import anthropic
    import httpx2

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    def err(cls, status, msg):
        return cls(msg, response=httpx2.Response(status, request=req), body=None)
    assert "new key" in cli._api_error_hint(err(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    assert "Billing" in cli._api_error_hint(err(anthropic.BadRequestError, 400, "Your credit balance is too low"))
    assert cli._api_error_hint(ValueError("unrelated")) is None
