import json
import sys

import pytest

from hf_msft_foundry import cli


def test_cli_json_errors(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fail(args: object) -> None:
        raise ValueError("Template 'nope' is not available")

    monkeypatch.setattr(cli, "run", fail)
    monkeypatch.setattr(sys, "argv", ["hf-msft-foundry", "deployments", "list", "-o", "json"])
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().err) == {
        "error": "ValueError",
        "message": "Template 'nope' is not available",
    }
