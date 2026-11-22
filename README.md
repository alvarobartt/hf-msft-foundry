# `hf-msft-foundry`

> [!WARNING]
> `hf-msft-foundry` is an experimental and independent open source project, not affiliated with or endorsed by Microsoft. Its interface may change without notice.

`hf-msft-foundry` is an extension for the [`hf` CLI](https://huggingface.co/docs/huggingface_hub/guides/cli) to deploy open models from the Hugging Face collection on [Microsoft Foundry](https://ai.azure.com), on managed compute within your own Azure subscription, without going through the Microsoft Foundry portal.

## Installation

```bash
hf extensions install alvarobartt/hf-msft-foundry
```

For the standalone `hf-msft-foundry` command, install it with `pip install hf-msft-foundry` or `uv tool install hf-msft-foundry`. [PyPI](https://pypi.org/project/hf-msft-foundry/)

## Authentication

Sign in with `az login` or use a service principal or managed identity. The CLI uses the active Azure subscription and selects the Foundry resource when there is only one. Set `AZURE_SUBSCRIPTION_ID` and `FOUNDRY_ACCOUNT_NAME` to choose defaults, or pass `--subscription-id` and `--account`.

To create a Foundry resource, run the following command.

```bash
hf msft-foundry accounts create my-foundry --resource-group my-foundry-rg --location eastus
```

## Deploy a model

| Model | Size | Default deployment template |
|---|---|---|
| [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) | 321B | 4 x AMD Instinct MI300X at 512K context |
| [`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8) | 120B (12B active) | 2 x NVIDIA H100 at 256K context |
| [`google/gemma-4-26B-A4B-it`](https://huggingface.co/google/gemma-4-26B-A4B-it) | 26B (4B active) | 1 x NVIDIA A100 at 256K context |

Browse the catalog and check the GPU, context length, and hourly price for each recipe. Use `quota` and `capacity` to check GPU availability before deploying.

```bash
hf msft-foundry models list
hf msft-foundry models list --search "gemma 4"
hf msft-foundry models google/gemma-4-26B-A4B-it deployment-templates
hf msft-foundry deploy google/gemma-4-26B-A4B-it --name my-gemma-4
```

The CLI picks the default recipe and waits for the deployment to be ready, usually around 20 minutes. Pass `--deployment-template 256k-nvidia-h100` to choose a different recipe or `--no-wait` to return immediately. If you omit `--name`, use the generated deployment name from the command output.

## Use the endpoint

The base URL ends in `/openai/v1`. Keep the key in your environment and use the deployment name as the model ID. The endpoint supports `/chat/completions` for chat deployments and `/responses` when the model and template support it.

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

Install Pi 1.x or OpenCode 2.x and make sure the executable is on `PATH`. Launch from your project directory after the chat deployment reaches `Succeeded`. Your Azure identity needs permission to list the Foundry account keys. [Install Pi](https://docs.pi.dev/latest/quickstart) or [install OpenCode](https://opencode.ai/download).

```bash
hf msft-foundry pi my-gemma-4
hf msft-foundry opencode my-gemma-4
```

Set `FOUNDRY_DEPLOYMENT` to make a deployment the default. If the account has one ready deployment, you can omit the name. Both launchers configure the endpoint and credentials. Pi saves sessions in a separate profile per account and deployment. OpenCode runs a standalone server with its usual session storage. Choose a model that supports tool calling for file reads and edits.

Pass harness arguments after `--`. Use `--continue` for the latest session or `--session` for a specific one.

```bash
hf msft-foundry pi my-gemma-4 -- -p "Remember the codename Cedar."
hf msft-foundry pi my-gemma-4 -- --continue -p "What was the codename?"
hf msft-foundry opencode my-gemma-4 -- run "Remember the codename Cedar."
hf msft-foundry opencode my-gemma-4 -- run --continue "What was the codename?"
```

<details>
<summary>Manual provider setup</summary>

Use the endpoint and key from [Use the endpoint](#use-the-endpoint). These settings use the 256K Gemma 4 template and a 4,096 token output limit. Match the context limit to your deployment template.

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

Use the [`hf-msft-foundry` skill](https://github.com/alvarobartt/hf-msft-foundry/blob/main/skills/hf-msft-foundry/SKILL.md) to give an agent the deploy and management workflow.

## Commands

| Command | Description |
|---|---|
| `models [list\|<model> [info\|deployment-templates]] [--search] [--version]` | List models, show one, or list its deployment templates |
| `deploy <model> [--deployment-template] [--name] [--instances] [--version] [--no-wait]` | Deploy a model |
| `deployments {list,status,wait,scale,delete}` | Manage the deployments |
| `accounts {list,info,keys,create}` | Manage the Microsoft Foundry resources |
| `capacity` / `quota` | Show the available accelerators and the managed compute quota |
| `pi [<deployment>]` / `opencode [<deployment>]` | Launch a coding agent against a ready chat deployment |

Pass `-o json` for machine-readable CLI output and errors. Harness output follows the harness's own format.

When you finish using the deployment, run `hf msft-foundry deployments delete my-gemma-4 --wait` to stop billing.
