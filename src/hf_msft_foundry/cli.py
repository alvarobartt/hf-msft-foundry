import argparse
import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from typing import Any

from azure.core.exceptions import ClientAuthenticationError

from hf_msft_foundry.client import Foundry
from hf_msft_foundry.errors import FoundryError
from hf_msft_foundry.harnesses import HARNESSES, launch

logger = logging.getLogger("hf_msft_foundry")

TEMPLATE_COLUMNS = [
    "name",
    "accelerator_type",
    "accelerator_count",
    "max_model_len",
    "environment",
    "price_per_hour",
    "default",
]
DEPLOYMENT_COLUMNS = ["name", "state", "model", "template", "accelerator_type", "instances"]
EXPORTED_PROPERTIES = ("uri", "url", "endpoint", "openai_base_url")


def serialize(data: Any) -> Any:
    if isinstance(data, list):
        return [serialize(item) for item in data]
    if is_dataclass(data) and not isinstance(data, type):
        values = asdict(data)
        for prop in EXPORTED_PROPERTIES:
            if isinstance(getattr(type(data), prop, None), property):
                values[prop] = getattr(data, prop)
        return values
    return data


def table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    cells = [["" if row.get(c) is None else str(row.get(c)) for c in columns] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
    lines = [[c.upper() for c in columns], *cells]
    return "\n".join("  ".join(v.ljust(w) for v, w in zip(line, widths, strict=True)).rstrip() for line in lines)


def render(data: Any, columns: list[str] | None) -> str:
    if isinstance(data, dict):
        width = max(len(k) for k in data)
        return "\n".join(
            f"{k.ljust(width)}  {', '.join(map(str, v)) if isinstance(v, list) else ('' if v is None else v)}"
            for k, v in data.items()
        )
    if columns:
        return table(data, columns)
    return "\n".join(map(str, data))


def emit(args: argparse.Namespace, data: Any, columns: list[str] | None = None, empty: str = "") -> None:
    data = serialize(data)
    if args.output == "json":
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
    elif not data:
        logger.warning(empty)
    else:
        sys.stdout.write(render(data, columns) + "\n")


def run(args: argparse.Namespace) -> int | None:
    foundry = Foundry(subscription_id=args.subscription_id, account_name=args.account)
    action = getattr(args, "action", None)
    model_id = getattr(args, "model_id", None)
    if args.command == "models":
        if model_id == "list" and action is None:
            model_id = None
        if model_id is not None:
            if args.search:
                raise ValueError("Use either a model ID or --search, not both")
            action = action or "info"
    match args.command, action:
        case "models", None if model_id is None:
            scope = f"match '{args.search}'" if args.search else "are available"
            empty = f"No models {scope} in the Hugging Face collection on Microsoft Foundry"
            emit(args, foundry.registry.models(args.search), empty=empty)
        case "models", "info":
            emit(args, foundry.registry.model(args.model_id, args.version))
        case "models", "deployment-templates":
            model = foundry.registry.model(args.model_id, args.version)
            empty = f"{args.model_id} has no deployment templates"
            emit(args, foundry.registry.templates(model, foundry.prices), TEMPLATE_COLUMNS, empty)
        case "deploy", _:
            deployment = foundry.deploy(
                args.model_id, args.deployment_template, args.name, args.instances, args.version
            )
            emit(args, deployment if args.no_wait else foundry.deployments.wait(deployment.name))
        case "deployments", "list":
            empty = f"No deployments in the Microsoft Foundry resource '{foundry.account.name}'"
            emit(args, foundry.deployments.all(), DEPLOYMENT_COLUMNS, empty)
        case "deployments", "status":
            emit(args, foundry.deployments.get(args.name))
        case "deployments", "wait":
            emit(args, foundry.deployments.wait(args.name))
        case "deployments", "scale":
            deployment = foundry.deployments.update(args.name, args.instances)
            emit(args, deployment if args.no_wait else foundry.deployments.wait(deployment.name))
        case "deployments", "delete":
            foundry.deployments.delete(args.name)
            if args.wait:
                foundry.deployments.wait_deleted(args.name)
        case "accounts", "list":
            empty = f"No Microsoft Foundry resources found in subscription {foundry.subscription_id}"
            emit(args, foundry.accounts.all(), ["name", "resource_group", "location", "endpoint"], empty)
        case "accounts", "info":
            emit(args, foundry.account)
        case "accounts", "create":
            emit(args, foundry.accounts.create(args.name, args.resource_group, args.location, args.project))
        case "accounts", "keys":
            emit(args, foundry.accounts.keys(foundry.account))
        case "capacity", _:
            empty = f"No managed compute capacity available in subscription {foundry.subscription_id}"
            emit(args, foundry.accounts.capacity(), ["accelerator_type", "available", "largest_deployment"], empty)
        case "quota", _:
            location = args.location or foundry.account.location
            empty = f"No managed compute quota in {location} for subscription {foundry.subscription_id}"
            emit(args, foundry.accounts.quota(location), ["accelerator_type", "used", "limit"], empty)
        case "pi" | "opencode", _:
            return launch(foundry, args.command, args.deployment, args.harness_args)
    return None


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--subscription-id", help="Azure subscription ID, defaults to AZURE_SUBSCRIPTION_ID")
    common.add_argument("--account", help="Microsoft Foundry resource name, defaults to FOUNDRY_ACCOUNT_NAME")
    common.add_argument(
        "-o", "--output", choices=["table", "json"], default="table", help="Output format, json also applies to errors"
    )

    root = argparse.ArgumentParser(
        prog="hf msft-foundry",
        description="Deploy open models from the Hugging Face collection on Microsoft Foundry",
    )
    commands = root.add_subparsers(dest="command", required=True)

    def group(name: str, help: str) -> argparse._SubParsersAction:
        return commands.add_parser(name, help=help).add_subparsers(dest="action", required=True)

    models = commands.add_parser(
        "models",
        parents=[common],
        help="Browse the Hugging Face collection on Microsoft Foundry",
        description="Run `hf msft-foundry models list` to list models, or see a model’s deployment templates with "
        "`hf msft-foundry models google/gemma-4-31B-it deployment-templates`",
    )
    models.add_argument(
        "model_id", nargs="?", help="Pass list or omit this argument to browse, or enter a Hub ID to show a model"
    )
    models.add_argument("action", nargs="?", choices=["info", "deployment-templates"], help="Defaults to info")
    models.add_argument("--search", help="Filter the models by name, e.g. 'gemma 4'")
    models.add_argument("--version", help="Model version, defaults to the latest")

    deploy = commands.add_parser("deploy", parents=[common], help="Deploy a model")
    deploy.add_argument("model_id", help="Hub ID, e.g. google/gemma-4-31B-it")
    deploy.add_argument("--version", help="Model version, defaults to the latest")
    deploy.add_argument(
        "--deployment-template",
        "--template",
        dest="deployment_template",
        help="Deployment template name (or its suffix, e.g. 256k-nvidia-h100), defaults to the model's default",
    )
    deploy.add_argument(
        "--name",
        help="Deployment name, i.e. the model to set in requests, defaults to <author>--<model>--<7-digit timestamp>",
    )
    deploy.add_argument("--instances", type=int, default=1, help="Number of instances")
    deploy.add_argument("--no-wait", action="store_true", help="Return without waiting for the deployment")

    deployments = group("deployments", "Manage the deployments")
    deployments.add_parser("list", parents=[common], help="List the deployments")
    for action, help in [
        ("status", "Show a deployment"),
        ("wait", "Wait until a deployment succeeds"),
        ("scale", "Change the number of instances"),
        ("delete", "Delete a deployment"),
    ]:
        sub = deployments.add_parser(action, parents=[common], help=help)
        sub.add_argument("name")
        if action == "scale":
            sub.add_argument("--instances", type=int, required=True)
            sub.add_argument("--no-wait", action="store_true")
        if action == "delete":
            sub.add_argument("--wait", action="store_true", help="Wait until the deployment is deleted")

    accounts = group("accounts", "Manage the Microsoft Foundry resources")
    accounts.add_parser("list", parents=[common], help="List the Microsoft Foundry resources")
    accounts.add_parser("info", parents=[common], help="Show the Microsoft Foundry resource and its endpoint")
    accounts.add_parser("keys", parents=[common], help="Show the API keys of the Microsoft Foundry resource")
    create = accounts.add_parser("create", parents=[common], help="Create a Microsoft Foundry resource")
    create.add_argument("name")
    create.add_argument("--resource-group", required=True, help="Created if it doesn't exist")
    create.add_argument("--location", required=True, help="Azure region, e.g. eastus")
    create.add_argument("--project", help="Project name, defaults to <name>-project")

    commands.add_parser("capacity", parents=[common], help="Show the available accelerators")
    quota = commands.add_parser("quota", parents=[common], help="Show the managed compute quota")
    quota.add_argument("--location", help="Azure region, defaults to the Microsoft Foundry resource's")

    for harness in HARNESSES:
        agent = commands.add_parser(harness, parents=[common], help=f"Launch {harness} with a Foundry deployment")
        agent.add_argument(
            "deployment",
            nargs="?",
            help="Deployment name, defaults to FOUNDRY_DEPLOYMENT or the only ready deployment",
        )
        agent.epilog = f"Put {harness} arguments after --. Output from the harness uses its own format."

    return root


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logger.setLevel(logging.INFO)
    arguments = sys.argv[1:]
    harness_args: list[str] = []
    if arguments and arguments[0] in HARNESSES and "--" in arguments:
        separator = arguments.index("--")
        arguments, harness_args = arguments[:separator], arguments[separator + 1 :]
    args = parser().parse_args(arguments)
    args.harness_args = harness_args
    try:
        return run(args) or 0
    except (FoundryError, ValueError, FileNotFoundError, ClientAuthenticationError) as error:
        message = error.message if isinstance(error, ClientAuthenticationError) else str(error)
        if isinstance(error, ClientAuthenticationError):
            message = f"Azure authentication failed, run `az login` first: {message}"
        if getattr(args, "output", "table") == "json":
            sys.stderr.write(json.dumps({"error": type(error).__name__, "message": message}) + "\n")
        else:
            logger.error(message)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
