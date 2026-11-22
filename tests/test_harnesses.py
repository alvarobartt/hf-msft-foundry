import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from hf_msft_foundry import cli, harnesses
from hf_msft_foundry.accounts import Account
from hf_msft_foundry.client import Foundry
from hf_msft_foundry.deployments import Deployment
from hf_msft_foundry.errors import FoundryError


@pytest.fixture
def foundry() -> Foundry:
    ready = Deployment("my-model", "Succeeded", "org--model", "my-template", "A100_80GB", 1)
    pending = Deployment("pending", "Creating", "org--model", "my-template", "A100_80GB", 1)
    deployments = {d.name: d for d in (ready, pending)}
    stub = SimpleNamespace(
        account=Account("subscription", "resource-group", "my-account", "eastus"),
        accounts=SimpleNamespace(keys=lambda _: {"key1": "test-secret"}),
        deployments=SimpleNamespace(get=deployments.__getitem__, all=lambda: list(deployments.values())),
        registry=SimpleNamespace(
            model=lambda _: SimpleNamespace(task="chat-completion", context_window="262144"),
            template=lambda _: SimpleNamespace(max_model_len="32768"),
        ),
    )
    return cast(Foundry, stub)


@pytest.mark.parametrize("harness", ["pi", "opencode"])
def test_harness_passes_arguments_and_exit_status(
    harness: str, foundry: Foundry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "Foundry", lambda **kwargs: foundry)
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        sys, "argv", ["hf-msft-foundry", harness, "my-model", "--account", "my-account", "--", "--flag", "test prompt"]
    )

    def execute(command: list[str], *, env: dict[str, str], check: bool) -> SimpleNamespace:
        assert command[0] == f"/bin/{harness}"
        assert command[-2:] == ["--flag", "test prompt"]
        assert env[harnesses.KEY_ENV] == "test-secret"
        assert check is False
        provider_id = "foundry-my-account"
        model = f"{provider_id}/my-model"
        if harness == "pi":
            assert command[1:-2] == ["--model", model]
            config_path = Path(env["PI_CODING_AGENT_DIR"]) / "models.json"
            config_json = config_path.read_text()
            provider = json.loads(config_json)["providers"][provider_id]
            assert provider["apiKey"] == f"${harnesses.KEY_ENV}"
            assert provider["baseUrl"] == "https://my-account.services.ai.azure.com/openai/v1"
            assert provider["models"][0]["contextWindow"] == 32768
        else:
            assert command[1:-2] == ["--standalone"]
            config_json = env["OPENCODE_CONFIG_CONTENT"]
            config = json.loads(config_json)
            provider = config["providers"][provider_id]
            assert config["model"] == model
            assert provider["env"] == [harnesses.KEY_ENV]
            assert provider["settings"]["baseURL"] == "https://my-account.services.ai.azure.com/openai/v1"
            assert provider["models"]["my-model"]["limit"] == {"context": 32768, "output": 4096}
        assert "test-secret" not in config_json
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(harnesses.subprocess, "run", execute)
    assert cli.main() == 7


def test_harness_deployment_selection(foundry: Foundry, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOUNDRY_DEPLOYMENT", raising=False)
    assert harnesses.select_deployment(foundry, None).name == "my-model"
    with pytest.raises(FoundryError, match="pending.*Creating"):
        harnesses.select_deployment(foundry, "pending")
    foundry.deployments.get("pending").state = "Succeeded"
    with pytest.raises(FoundryError, match="Multiple deployments.*my-model, pending"):
        harnesses.select_deployment(foundry, None)
    monkeypatch.setenv("FOUNDRY_DEPLOYMENT", "pending")
    assert harnesses.select_deployment(foundry, None).name == "pending"


def test_harness_requires_chat_model(foundry: Foundry, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(foundry.registry, "model", lambda _: SimpleNamespace(task="embeddings"))
    with pytest.raises(FoundryError, match="require a chat model"):
        harnesses.context_limit(foundry, foundry.deployments.get("my-model"))
