import json
import sys
from types import SimpleNamespace

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


@pytest.mark.parametrize("model_args", [[], ["list"]])
def test_models_list(
    model_args: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    names = ["google--gemma-4-26b-a4b-it"]

    def models(search: str | None) -> list[str]:
        assert search == "gemma 4"
        return names

    monkeypatch.setattr(cli, "Foundry", lambda **kwargs: SimpleNamespace(registry=SimpleNamespace(models=models)))
    monkeypatch.setattr(sys, "argv", ["hf-msft-foundry", "models", *model_args, "--search", "gemma 4", "-o", "json"])

    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == names
