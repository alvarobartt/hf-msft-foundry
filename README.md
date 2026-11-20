# `hf-msft-foundry`

> [!WARNING]
> `hf-msft-foundry` is an experimental and independent open source project, not affiliated with or endorsed by Microsoft. Its interface may change without notice.

`hf-msft-foundry` is an extension for the [`hf` CLI](https://huggingface.co/docs/huggingface_hub/guides/cli) to deploy open models from the Hugging Face collection on [Microsoft Foundry](https://ai.azure.com), on managed compute within your own Azure subscription, powered by engines such as vLLM on NVIDIA A100, H100 and H200, or AMD Instinct MI300X.

It lets you browse the models and their deployment templates, check capacity and quota, and create, scale and delete deployments, all from the terminal and without having to go through the Microsoft Foundry portal.

## Installation

```bash
hf extensions install alvarobartt/hf-msft-foundry
```

Alternatively, it can also be installed as a standalone CLI from [PyPI](https://pypi.org/project/hf-msft-foundry/), and used as `hf-msft-foundry` instead of `hf msft-foundry`:

```bash
uv tool install hf-msft-foundry
# or
pip install hf-msft-foundry
```

It requires Python 3.10 or higher.

## Authentication

`hf-msft-foundry` authenticates with [`DefaultAzureCredential`](https://learn.microsoft.com/azure/developer/python/sdk/authentication/credential-chains#defaultazurecredential-overview), so either log in with the Azure CLI via `az login`, or set the environment variables for a service principal or a managed identity.

The subscription defaults to the one selected in the Azure CLI, and the Microsoft Foundry resource defaults to the only one in the subscription. If there's more than one, the commands fail listing them, so set them as follows:

```bash
export AZURE_SUBSCRIPTION_ID=...
export FOUNDRY_ACCOUNT_NAME=...
```

Or pass `--subscription-id` and `--account` to any command. If you don't have a Microsoft Foundry resource yet, you can create one with:

```bash
hf msft-foundry accounts create my-foundry --resource-group my-foundry-rg --location eastus
```

## Getting started

Some models to get started with, on different sizes:

| Model | Size | Default deployment template |
|---|---|---|
| [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) | 321B | 4 x AMD Instinct MI300X at 512K context |
| [`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8) | 120B (12B active) | 2 x NVIDIA H100 at 256K context |
| [`google/gemma-4-26B-A4B-it`](https://huggingface.co/google/gemma-4-26B-A4B-it) | 26B (4B active) | 1 x NVIDIA A100 at 256K context |

Check whether a model is available, and which deployment templates i.e., the ready-to-use configurations for each instance and context length, can be used with it and at which price:

```bash
hf msft-foundry models --search "gemma 4"
hf msft-foundry models google/gemma-4-26B-A4B-it
hf msft-foundry models google/gemma-4-26B-A4B-it deployment-templates
```

```console
NAME                                          ACCELERATOR_TYPE  ACCELERATOR_COUNT  MAX_MODEL_LEN  ENVIRONMENT             PRICE_PER_HOUR  DEFAULT
google--gemma-4-26b-a4b-it--256k-amd-mi300x   MI300_192GB       1                  262144         hf-vllm-v0-33-0-amd     7.91            False
google--gemma-4-26b-a4b-it--256k-nvidia-a100  A100_80GB         1                  262144         hf-vllm-v0-33-0-nvidia  3.95            True
google--gemma-4-26b-a4b-it--256k-nvidia-h100  H100_80GB         1                  262144         hf-vllm-v0-33-0-nvidia  7.91            False
google--gemma-4-26b-a4b-it--256k-nvidia-h200  H200_141GB        1                  262144         hf-vllm-v0-33-0-nvidia  9.1             False
```

The price is the on-demand list price in USD per hour and instance, from the [Azure Retail Prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices). Both commands use the latest version of the model, unless `--version` is provided.

Check that your subscription has enough quota, and that there's capacity available, for the accelerators of the template you want to use:

```bash
hf msft-foundry quota
hf msft-foundry capacity
```

Then deploy it with its default deployment template, or with another one via `--deployment-template`, either with its full name or its suffix:

```bash
hf msft-foundry deploy google/gemma-4-26B-A4B-it
hf msft-foundry deploy google/gemma-4-26B-A4B-it --deployment-template 256k-nvidia-h100
```

It waits until the deployment is ready, which can take 20+ minutes, unless `--no-wait` is provided. The deployment name defaults to `<author>--<model>--<7-digit timestamp>` e.g., `google--gemma-4-26b-a4b-it--5083872`, so that it doesn't collide with other deployments, and can be set with `--name` instead, using only letters, digits, underscores and hyphens.

Once deployed, you can send OpenAI-compatible Chat Completions requests to it, setting the deployment name as the `model`, with the endpoint and API key of your Microsoft Foundry resource:

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
> Deployments can't reach the internet, so send images as base64-encoded data URIs rather than as URLs.

Finally, delete the deployment to stop paying for it:

```bash
hf msft-foundry deployments delete google--gemma-4-26b-a4b-it--5083872 --wait
```

## Commands

| Command | Description |
|---|---|
| `models [--search]` | List the models in the Hugging Face collection |
| `models <model> [info] [--version]` | Show a model, its task, modalities and deployment templates |
| `models <model> deployment-templates [--version]` | List the deployment templates of a model, with their accelerators, context length and price |
| `deploy <model> [--version] [--deployment-template] [--name] [--instances] [--no-wait]` | Deploy a model |
| `deployments list` | List the deployments |
| `deployments status <name>` | Show a deployment |
| `deployments wait <name>` | Wait until a deployment succeeds |
| `deployments scale <name> --instances <n>` | Change the number of instances |
| `deployments delete <name> [--wait]` | Delete a deployment |
| `accounts list` | List the Microsoft Foundry resources |
| `accounts info` | Show the Microsoft Foundry resource and its endpoint |
| `accounts keys` | Show the API keys of the Microsoft Foundry resource |
| `accounts create <name> --resource-group --location` | Create a Microsoft Foundry resource and project |
| `capacity` | Show the available accelerators |
| `quota [--location]` | Show the managed compute quota |

All the commands accept `-o json` for machine-readable output, including errors, which makes `hf-msft-foundry` friendly to scripts and agents.

## Agent skill

The [`skills/hf-msft-foundry/SKILL.md`](https://github.com/alvarobartt/hf-msft-foundry/blob/main/skills/hf-msft-foundry/SKILL.md) skill teaches agents how to use `hf-msft-foundry` to find, deploy, use and delete models on Microsoft Foundry.

## Development

```bash
uv sync --group lint --group test
uv run pre-commit install
uv run pytest
```

The tests mock the Azure APIs, so they don't need an Azure subscription nor create any resource.

To release a new version, bump it with `uv version --bump <major|minor|patch>`, commit it, and push a matching `v<version>` tag, which publishes it to PyPI via [trusted publishing](https://docs.pypi.org/trusted-publishers/).

## License

MIT
