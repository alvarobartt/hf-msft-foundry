# `hf-msft-foundry`

> [!WARNING]
> `hf-msft-foundry` is an experimental and independent open source project, not affiliated with or endorsed by Microsoft. Its interface may change without notice.

`hf-msft-foundry` is an extension for the [`hf` CLI](https://huggingface.co/docs/huggingface_hub/guides/cli) to deploy open models from the Hugging Face collection on [Microsoft Foundry](https://ai.azure.com), on managed compute within your own Azure subscription, without going through the Microsoft Foundry portal.

## Installation

```bash
hf extensions install alvarobartt/hf-msft-foundry
```

Install the standalone `hf-msft-foundry` command with `pip install hf-msft-foundry` or `uv tool install hf-msft-foundry`. [PyPI](https://pypi.org/project/hf-msft-foundry/)

## Authentication

Sign in with `az login` or use a service principal or managed identity, then set `AZURE_SUBSCRIPTION_ID` and `FOUNDRY_ACCOUNT_NAME` if you need to override the active subscription or choose among multiple Microsoft Foundry resources. For an individual command, pass `--subscription-id` or `--account`.

To create a Microsoft Foundry resource, run the following command.

```bash
hf msft-foundry accounts create my-foundry --resource-group my-foundry-rg --location eastus
```

## Deploy a model

| Model | Size | Default deployment template |
|---|---|---|
| [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) | 321B | 4 x AMD Instinct MI300X at 512K context |
| [`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8) | 120B (12B active) | 2 x NVIDIA H100 at 256K context |
| [`google/gemma-4-26B-A4B-it`](https://huggingface.co/google/gemma-4-26B-A4B-it) | 26B (4B active) | 1 x NVIDIA A100 at 256K context |

Browse the catalog to see the GPUs, context lengths, and hourly prices for a model's recipes, then use `quota` and `capacity` to check GPU availability before deploying.

```bash
hf msft-foundry models list
hf msft-foundry models list --search "gemma 4"
hf msft-foundry models google/gemma-4-26B-A4B-it deployment-templates
hf msft-foundry deploy google/gemma-4-26B-A4B-it --name my-gemma-4
```

The CLI uses the default recipe and waits until the deployment is ready, which typically takes around 20 minutes. Use `--deployment-template 256k-nvidia-h100` to select another recipe or `--no-wait` to return immediately, and keep the generated deployment name from the command output if you omit `--name`.

## Use the endpoint

Export the key and the base URL ending in `/openai/v1`, then use the deployment name as the model ID when calling `/chat/completions` or, for supported models and templates, `/responses`.

```bash
export OPENAI_BASE_URL=$(hf msft-foundry accounts info -o json | jq -r .openai_base_url)
export OPENAI_API_KEY=$(hf msft-foundry accounts keys -o json | jq -r .key1)
```

```python
from openai import OpenAI

client = OpenAI()
completion = client.chat.completions.create(
    model="my-gemma-4",
    messages=[{"role": "user", "content": "What's the capital of France?"}],
    max_completion_tokens=1024,
)
print(completion.choices[0].message.content)
```

Send images as base64 data URIs because deployments cannot fetch remote URLs.

## Coding agents

Once the chat deployment reaches `Succeeded`, launch Pi 1.x or OpenCode 2.x from your project directory with its executable on `PATH` and an Azure identity that can list the Microsoft Foundry account keys. [Install Pi](https://docs.pi.dev/latest/quickstart) or [install OpenCode](https://opencode.ai/download).

```bash
hf msft-foundry pi my-gemma-4
hf msft-foundry opencode my-gemma-4
```

You can omit the deployment name when `FOUNDRY_DEPLOYMENT` is set or the account has one ready deployment. Both launchers configure the endpoint and credentials, with Pi saving sessions in a profile per account and deployment and OpenCode using its usual session storage through a standalone server. Choose a model that supports tool calling for file reads and edits.

Pass harness arguments after `--`, using `--continue` to resume the latest session or `--session` to select a specific one.

```bash
hf msft-foundry pi my-gemma-4 -- -p "Remember the codename Cedar."
hf msft-foundry pi my-gemma-4 -- --continue -p "What was the codename?"
hf msft-foundry opencode my-gemma-4 -- run "Remember the codename Cedar."
hf msft-foundry opencode my-gemma-4 -- run --continue "What was the codename?"
```

<details>
<summary>Manual provider setup</summary>

The examples use the [endpoint and key](#use-the-endpoint) with the 256K Gemma 4 template and a 4,096 token output limit, so adjust the context limit to match your deployment template.

For OpenCode, merge this provider into your project's `opencode.json` and select `foundry/my-gemma-4`.

```json
{
  "providers": {
    "foundry": {
      "package": "@opencode/ai/providers/openai-compatible",
      "env": ["OPENAI_API_KEY"],
      "settings": {"baseURL": "{env:OPENAI_BASE_URL}"},
      "models": {
        "my-gemma-4": {
          "name": "Gemma 4 26B A4B",
          "capabilities": {"tools": true, "input": ["text"], "output": ["text"]},
          "limit": {"context": 262144, "output": 4096}
        }
      }
    }
  }
}
```

For Pi, merge this provider into `~/.pi/agent/models.json`, set `baseUrl` to the value of `OPENAI_BASE_URL`, and select `foundry/my-gemma-4`.

```json
{
  "providers": {
    "foundry": {
      "baseUrl": "https://my-foundry.services.ai.azure.com/openai/v1",
      "api": "openai-completions",
      "apiKey": "$OPENAI_API_KEY",
      "compat": {"maxTokensField": "max_completion_tokens"},
      "models": [
        {"id": "my-gemma-4", "name": "Gemma 4 26B A4B", "input": ["text"], "contextWindow": 262144, "maxTokens": 4096}
      ]
    }
  }
}
```

</details>

## Agent skill

Install the `hf-msft-foundry` skill from your project directory to give your agent the steps for finding models, choosing a recipe, and managing deployments. [Read the skill](https://github.com/alvarobartt/hf-msft-foundry/blob/main/skills/hf-msft-foundry/SKILL.md).

```bash
npx skills add alvarobartt/hf-msft-foundry --skill hf-msft-foundry
```

## Commands

| Command | Description |
|---|---|
| `models [list\|<model> [info\|deployment-templates]] [--search] [--version]` | List models, show one, or list its deployment templates |
| `deploy <model> [--deployment-template] [--name] [--instances] [--version] [--no-wait]` | Deploy a model |
| `deployments {list,status,wait,scale,delete}` | Manage the deployments |
| `accounts {list,info,keys,create}` | Manage the Microsoft Foundry resources |
| `capacity` / `quota` | Show the available accelerators and the managed compute quota |
| `pi [<deployment>]` / `opencode [<deployment>]` | Launch a coding agent against a ready chat deployment |

For machine-readable results and errors, pass `-o json` to `hf msft-foundry`.

When you finish using the deployment, run `hf msft-foundry deployments delete my-gemma-4 --wait` to stop billing.
