# Use a Foundry deployment with OpenCode or Pi

## Launch from the CLI

Install Pi 1.x or OpenCode 2.x and make sure its executable is on `PATH`. Sign in with `az login` and wait for a chat deployment to reach `Succeeded`. [Pi installation](https://docs.pi.dev/latest/quickstart) · [OpenCode installation](https://opencode.ai/download)

From the project you want to work in, run either command with your deployment name.

```bash
hf msft-foundry pi my-gemma-4 --account my-foundry
hf msft-foundry opencode my-gemma-4 --account my-foundry
```

You can omit the name when `FOUNDRY_DEPLOYMENT` is set or the account has one ready deployment. `FOUNDRY_ACCOUNT_NAME` sets the default account. If you installed from PyPI, use `hf-msft-foundry` in place of `hf msft-foundry`.

The launcher configures the account endpoint and uses the deployment name as the API model ID. Your Azure identity needs permission to list the account keys. The launcher passes the key to the child process through its environment. Pi stores settings and sessions in a separate profile per account and deployment. OpenCode loads the Foundry provider for a standalone invocation and uses its usual session storage. Use a model and runtime that support tool calling for tasks that read or edit files.

Put native harness arguments after `--`. For example, send a prompt and resume it from the same project and deployment.

```bash
hf msft-foundry pi my-gemma-4 -- -p "Remember the service codename Cedar. Reply with READY."
hf msft-foundry pi my-gemma-4 -- --continue -p "What codename did I give you?"

hf msft-foundry opencode my-gemma-4 -- run "Remember the service codename Cedar. Reply with READY."
hf msft-foundry opencode my-gemma-4 -- run --continue "What codename did I give you?"
```

Use the harness’s `--session` option after `--` to resume a specific session.

### Live conversation examples

Both examples ran on Gemma 4 26B A4B on Foundry. Each turn used a fresh CLI invocation, with turns 2 and 3 resuming the first session.

| Turn | Pi 1.4.0 | OpenCode 2.2.5 |
| --- | --- | --- |
| Remember a service codename | `READY` for `CEDAR-PATH` | `READY` for `LUNAR-DOCK` |
| Read `http_port` from the local `service.json` | `7319` | `8427` |
| Combine the earlier codename and port without rereading the file | `CEDAR-PATH:7319` | `LUNAR-DOCK:8427` |

## Configure a harness manually

Use these settings to add Foundry as a provider in an existing harness profile. The examples use the default 256K Gemma 4 26B A4B template and cap each turn at 4,096 output tokens. Set the context limit to the limit of your deployment template when using another model.

Wait for the deployment to reach `Succeeded`, then load the account endpoint and key in the shell that launches your client. `FOUNDRY_DEPLOYMENT` is the deployment name returned by `hf msft-foundry deploy`.

```bash
export FOUNDRY_ACCOUNT_NAME=my-foundry
export FOUNDRY_DEPLOYMENT=my-gemma-4
export FOUNDRY_BASE_URL=$(hf msft-foundry accounts info -o json | jq -er .openai_base_url)
export FOUNDRY_API_KEY=$(hf msft-foundry accounts keys -o json | jq -er .key1)
```

The base URL ends in `/openai/v1`. The clients append `/chat/completions`. Keep the key in the environment and use the deployment name as the API model ID.

### OpenCode 2

Add the provider to your project's `opencode.json` and merge it with any existing settings.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "foundry/gemma-4",
  "providers": {
    "foundry": {
      "name": "Hugging Face on Microsoft Foundry",
      "package": "@opencode/ai/providers/openai-compatible",
      "env": ["FOUNDRY_API_KEY"],
      "settings": {
        "baseURL": "{env:FOUNDRY_BASE_URL}",
        "apiKey": "{env:FOUNDRY_API_KEY}"
      },
      "models": {
        "gemma-4": {
          "modelID": "{env:FOUNDRY_DEPLOYMENT}",
          "name": "Gemma 4 26B A4B",
          "capabilities": {
            "tools": true,
            "input": ["text"],
            "output": ["text"]
          },
          "limit": {"context": 262144, "output": 4096}
        }
      }
    }
  }
}
```

Run a smoke prompt with a private OpenCode server that inherits the exported variables.

```bash
opencode run --standalone --model foundry/gemma-4 "Reply with exactly Foundry OK."
```

[OpenCode provider configuration](https://opencode.ai/docs/providers/)

### Pi

Use Pi 1.x. Pi’s npm installation requires Node.js 22.19 or newer. [Pi installation guide](https://docs.pi.dev/latest/quickstart)

Merge this provider into `~/.pi/agent/models.json`. Replace `baseUrl` with the value of `FOUNDRY_BASE_URL` and `id` with your deployment name. Pi resolves `$FOUNDRY_API_KEY` when it sends a request.

```json
{
  "$schema": "https://pi.dev/schemas/models.schema.json",
  "providers": {
    "foundry": {
      "name": "Hugging Face on Microsoft Foundry",
      "baseUrl": "https://my-foundry.services.ai.azure.com/openai/v1",
      "api": "openai-completions",
      "apiKey": "$FOUNDRY_API_KEY",
      "compat": {"maxTokensField": "max_completion_tokens"},
      "models": [
        {
          "id": "my-gemma-4",
          "name": "Gemma 4 26B A4B",
          "input": ["text"],
          "contextWindow": 262144,
          "maxTokens": 4096
        }
      ]
    }
  }
}
```

```bash
pi --no-session --no-tools --model "foundry/$FOUNDRY_DEPLOYMENT" -p "Reply with exactly Foundry OK."
```

[Pi model configuration](https://docs.pi.dev/latest/models-configuration)

## Other clients

Use an OpenAI-compatible endpoint with the same base URL, key, and deployment name. Clients that take a complete endpoint URL can use `/openai/v1/chat/completions`. Use `/openai/v1/responses` when the deployed model and template support the Responses API.
