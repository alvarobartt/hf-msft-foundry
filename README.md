# `hf-msft-foundry`

> [!WARNING]
> `hf-msft-foundry` is an experimental and independent open source project, not affiliated with or endorsed by Microsoft. Its interface may change without notice.

`hf-msft-foundry` is an extension for the [`hf` CLI](https://huggingface.co/docs/huggingface_hub/guides/cli) to deploy open models from the Hugging Face collection on [Microsoft Foundry](https://ai.azure.com), on managed compute within your own Azure subscription, without going through the Microsoft Foundry portal.

## Installation

```bash
hf extensions install alvarobartt/hf-msft-foundry
```

Or install it from [PyPI](https://pypi.org/project/hf-msft-foundry/) with `pip install hf-msft-foundry` or `uv tool install hf-msft-foundry`, and use it as `hf-msft-foundry` instead of `hf msft-foundry`.

## Authentication

Log in with `az login`, or set the environment variables for a service principal or managed identity. The subscription and the Microsoft Foundry resource default to the Azure CLI selection and the only resource in the subscription, otherwise set `AZURE_SUBSCRIPTION_ID` and `FOUNDRY_ACCOUNT_NAME`, or pass `--subscription-id` and `--account`.

If you don't have a Microsoft Foundry resource yet, create one with:

```bash
hf msft-foundry accounts create my-foundry --resource-group my-foundry-rg --location eastus
```

## Getting started

| Model | Size | Default deployment template |
|---|---|---|
| [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) | 321B | 4 x AMD Instinct MI300X at 512K context |
| [`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8) | 120B (12B active) | 2 x NVIDIA H100 at 256K context |
| [`google/gemma-4-26B-A4B-it`](https://huggingface.co/google/gemma-4-26B-A4B-it) | 26B (4B active) | 1 x NVIDIA A100 at 256K context |

Browse the catalog, search for a model, or see its deployment templates with accelerator, context length and hourly price per instance.

```bash
hf msft-foundry models list
hf msft-foundry models list --search "gemma 4"
hf msft-foundry models google/gemma-4-26B-A4B-it deployment-templates
```

```console
NAME                                          ACCELERATOR_TYPE  ACCELERATOR_COUNT  MAX_MODEL_LEN  ENVIRONMENT             PRICE_PER_HOUR  DEFAULT
google--gemma-4-26b-a4b-it--256k-amd-mi300x   MI300_192GB       1                  262144         hf-vllm-v0-33-0-amd     7.91            False
google--gemma-4-26b-a4b-it--256k-nvidia-a100  A100_80GB         1                  262144         hf-vllm-v0-33-0-nvidia  3.95            True
google--gemma-4-26b-a4b-it--256k-nvidia-h100  H100_80GB         1                  262144         hf-vllm-v0-33-0-nvidia  7.91            False
google--gemma-4-26b-a4b-it--256k-nvidia-h200  H200_141GB        1                  262144         hf-vllm-v0-33-0-nvidia  9.1             False
```

Check `hf msft-foundry quota` and `hf msft-foundry capacity` for the accelerators you need, then deploy with the default template, or another one via `--deployment-template`:

```bash
hf msft-foundry deploy google/gemma-4-26B-A4B-it
hf msft-foundry deploy google/gemma-4-26B-A4B-it --deployment-template 256k-nvidia-h100
```

It waits until the deployment is ready, which takes about 20 minutes, unless `--no-wait` is set. The deployment name defaults to `<author>--<model>--<timestamp>` e.g. `google--gemma-4-26b-a4b-it--5083872`, or set it with `--name`.

Then send OpenAI-compatible requests with the deployment name as the `model`:

```bash
export OPENAI_BASE_URL=$(hf msft-foundry accounts info -o json | jq -r .openai_base_url)
export OPENAI_API_KEY=$(hf msft-foundry accounts keys -o json | jq -r .key1)
```

```python
from openai import OpenAI

client = OpenAI()
completion = client.chat.completions.create(
    model="google--gemma-4-26b-a4b-it--5083872",
    messages=[{"role": "user", "content": "What's the capital of France?"}],
    max_completion_tokens=1024,
)
print(completion.choices[0].message.content)
```

> [!NOTE]
> Deployments can't reach the internet, so send images as base64-encoded data URIs rather than URLs.

Finally, delete the deployment to stop paying for it:

```bash
hf msft-foundry deployments delete google--gemma-4-26b-a4b-it--5083872 --wait
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

All commands accept `-o json` for machine-readable output, including errors.

## Agents

Launch Pi or OpenCode from your project directory. Pass a ready chat deployment name, or omit it when the account has only one ready deployment.

```bash
hf msft-foundry pi my-gemma-4
hf msft-foundry opencode my-gemma-4
```

[Pi and OpenCode setup and session examples](https://github.com/alvarobartt/hf-msft-foundry/blob/main/docs/agent-harnesses.md)

The [`hf-msft-foundry` skill](https://github.com/alvarobartt/hf-msft-foundry/blob/main/skills/hf-msft-foundry/SKILL.md) teaches agents how to find, deploy, use and delete models on Microsoft Foundry with `hf-msft-foundry`.
