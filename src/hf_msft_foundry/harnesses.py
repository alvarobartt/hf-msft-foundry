import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from hf_msft_foundry.client import Foundry
from hf_msft_foundry.deployments import Deployment
from hf_msft_foundry.errors import FoundryError

HARNESSES = {"pi": "https://docs.pi.dev/latest/quickstart", "opencode": "https://opencode.ai/download"}
KEY_ENV = "HF_MSFT_FOUNDRY_API_KEY"


def select_deployment(foundry: Foundry, name: str | None) -> Deployment:
    name = name or os.getenv("FOUNDRY_DEPLOYMENT")
    if name:
        deployment = foundry.deployments.get(name)
        if deployment.state != "Succeeded":
            raise FoundryError(f"Deployment '{name}' is {deployment.state}. Wait for it to reach Succeeded.")
        return deployment

    ready = [deployment for deployment in foundry.deployments.all() if deployment.state == "Succeeded"]
    if len(ready) == 1:
        return ready[0]
    if not ready:
        raise FoundryError(
            "No ready deployments in this Microsoft Foundry resource. Run `hf msft-foundry deployments list`."
        )
    names = ", ".join(sorted(deployment.name for deployment in ready))
    raise FoundryError(f"Multiple deployments are ready. Pass a deployment name or set FOUNDRY_DEPLOYMENT: {names}")


def context_limit(foundry: Foundry, deployment: Deployment) -> int:
    if not deployment.model:
        raise FoundryError(f"Deployment '{deployment.name}' has no model metadata.")
    model = foundry.registry.model(deployment.model)
    if model.task and model.task != "chat-completion":
        raise FoundryError(
            f"Deployment '{deployment.name}' serves {model.task}. Coding harnesses require a chat model."
        )
    template = foundry.registry.template(deployment.template) if deployment.template else None
    for value in (template.max_model_len if template else None, model.context_window):
        if value and str(value).isdecimal() and int(value) >= 4:
            return int(value)
    return 8192


def pi_profile(account: str, deployment: str, config: dict[str, object]) -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    profile = data_home / "hf-msft-foundry" / "pi" / account / deployment
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A concurrent launch should only ever see a complete models.json.
    with TemporaryDirectory(dir=profile) as directory:
        pending = Path(directory) / "models.json"
        pending.write_text(json.dumps(config), encoding="utf-8")
        pending.replace(profile / "models.json")
    return profile


def launch(foundry: Foundry, harness: str, name: str | None, arguments: list[str]) -> int:
    executable = shutil.which(harness)
    if not executable:
        raise FoundryError(f"'{harness}' is missing from PATH. Install it from {HARNESSES[harness]}.")

    deployment = select_deployment(foundry, name)
    context = context_limit(foundry, deployment)
    output = min(4096, context // 4)
    account = foundry.account
    key = foundry.accounts.keys(account)["key1"]
    if not key:
        raise FoundryError(f"Microsoft Foundry resource '{account.name}' did not return an API key.")

    env = os.environ.copy()
    env[KEY_ENV] = key
    # Some launchers use PWD in preference to the process working directory.
    env["PWD"] = os.getcwd()
    name = deployment.name
    provider = f"foundry-{account.name}"
    model_id = f"{provider}/{name}"
    label = "Hugging Face on Microsoft Foundry"

    if harness == "pi":
        pi_config: dict[str, object] = {
            "providers": {
                provider: {
                    "name": label,
                    "baseUrl": account.openai_base_url,
                    "api": "openai-completions",
                    "apiKey": f"${KEY_ENV}",
                    "compat": {"maxTokensField": "max_completion_tokens"},
                    "models": [
                        {"id": name, "name": name, "input": ["text"], "contextWindow": context, "maxTokens": output}
                    ],
                }
            }
        }
        try:
            env["PI_CODING_AGENT_DIR"] = str(pi_profile(account.name, name, pi_config))
        except OSError as error:
            raise FoundryError(f"Could not prepare the Pi profile: {error}") from error
        command = [executable, "--model", model_id, *arguments]
    else:
        opencode_config = {
            "model": model_id,
            "small_model": model_id,
            "providers": {
                provider: {
                    "name": label,
                    "env": [KEY_ENV],
                    "package": "@opencode/ai/providers/openai-compatible",
                    "settings": {"baseURL": account.openai_base_url},
                    "models": {
                        name: {
                            "name": name,
                            "capabilities": {"tools": True, "input": ["text"], "output": ["text"]},
                            "limit": {"context": context, "output": output},
                        }
                    },
                }
            },
        }
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(opencode_config)
        command = [executable, "--standalone", *arguments]

    try:
        status = subprocess.run(command, env=env, check=False).returncode
    except OSError as error:
        raise FoundryError(f"Could not start {harness}: {error}") from error
    return status if status >= 0 else 128 - status
