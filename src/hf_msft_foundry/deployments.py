import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from hf_msft_foundry.accounts import API_VERSION, Account
from hf_msft_foundry.azure import AzureClient
from hf_msft_foundry.errors import DeploymentFailedError, NotFoundError
from hf_msft_foundry.registry import Model, Template, template_name

logger = logging.getLogger("hf_msft_foundry")

SKU = "GlobalManagedCompute"
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{2,64}")
PENDING_STATES = {"Creating", "Accepted", "Updating", "Running"}


def default_deployment_name(model: str, now: float | None = None) -> str:
    suffix = f"{int(time.time() if now is None else now) % 10**7:07d}"
    name = re.sub(r"[^a-z0-9-]", "-", model.lower())
    return f"{name[: 64 - len(suffix) - 2].rstrip('-')}--{suffix}"


def validate_name(name: str) -> None:
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Invalid deployment name '{name}', use 2 to 64 letters, digits, underscores or hyphens (no dots)"
        )


@dataclass
class Deployment:
    name: str
    state: str | None
    model: str | None
    template: str | None
    accelerator_type: str | None
    instances: int | None

    @classmethod
    def from_arm(cls, item: dict[str, Any]) -> "Deployment":
        properties = item.get("properties", {})
        model = properties.get("model", "")
        template = properties.get("deploymentTemplate", "")
        return cls(
            name=item["name"],
            state=properties.get("provisioningState"),
            model=model.split("/models/", 1)[-1].split("/", 1)[0] if model else None,
            template=template_name(template) if template else None,
            accelerator_type=properties.get("acceleratorType"),
            instances=(item.get("sku") or {}).get("capacity"),
        )


class Deployments:
    def __init__(self, azure: AzureClient, account: Account) -> None:
        self.azure = azure
        self.account = account
        self.url = f"{account.resource_url}/managedComputeDeployments"
        self.params = {"api-version": API_VERSION}

    def not_found(self, name: str) -> str:
        return f"Deployment '{name}' not found in the Microsoft Foundry resource '{self.account.name}'"

    def all(self) -> list[Deployment]:
        items = self.azure.paginate(
            self.url, self.params, not_found=f"Microsoft Foundry resource '{self.account.name}' not found"
        )
        return [Deployment.from_arm(item) for item in items]

    def get(self, name: str) -> Deployment:
        validate_name(name)
        item = self.azure.request("GET", f"{self.url}/{name}", not_found=self.not_found(name), params=self.params)
        return Deployment.from_arm(item)

    def create(self, model: Model, template: Template, name: str | None = None, instances: int = 1) -> Deployment:
        name = name or default_deployment_name(model.name)
        validate_name(name)
        price = f", ${template.price_per_hour}/hour" if template.price_per_hour is not None else ""
        logger.info(
            "Deploying %s (version %s) as '%s' with %s (%s x %s x %s instance(s)%s)",
            model.hub_id or model.name,
            model.version,
            name,
            template.name,
            template.accelerator_count,
            template.accelerator_type,
            instances,
            price,
        )
        self.azure.request(
            "PUT",
            f"{self.url}/{name}",
            not_found=f"Microsoft Foundry resource '{self.account.name}' not found",
            params=self.params,
            json={
                "sku": {"name": SKU, "capacity": instances},
                "properties": {
                    "model": model.uri,
                    "deploymentTemplate": template.uri,
                    "acceleratorType": template.accelerator_type,
                    "versionUpgradeOption": "OnceNewDefaultVersionAvailable",
                },
            },
        )
        return self.get(name)

    def update(self, name: str, instances: int) -> Deployment:
        logger.info("Scaling deployment '%s' to %s instance(s)", name, instances)
        self.azure.request(
            "PATCH",
            f"{self.url}/{name}",
            not_found=self.not_found(name),
            params=self.params,
            json={"sku": {"name": SKU, "capacity": instances}},
        )
        return self.get(name)

    def delete(self, name: str) -> None:
        self.get(name)
        self.azure.request("DELETE", f"{self.url}/{name}", not_found=self.not_found(name), params=self.params)
        logger.info("Deleting deployment '%s'", name)

    def wait(self, name: str, interval: int = 30) -> Deployment:
        start = time.monotonic()
        while True:
            deployment = self.get(name)
            elapsed = time.monotonic() - start
            if deployment.state == "Succeeded":
                logger.info(
                    "Deployment '%s' succeeded after %.0fs, use '%s' as the model in requests", name, elapsed, name
                )
                return deployment
            if deployment.state not in PENDING_STATES:
                raise DeploymentFailedError(f"Deployment '{name}' ended in state {deployment.state}")
            logger.info("Deployment '%s' is %s (%.0fs)", name, deployment.state, elapsed)
            time.sleep(interval)

    def wait_deleted(self, name: str, interval: int = 15) -> None:
        start = time.monotonic()
        while True:
            try:
                deployment = self.get(name)
            except NotFoundError:
                logger.info("Deployment '%s' deleted after %.0fs", name, time.monotonic() - start)
                return
            logger.info("Deployment '%s' is %s (%.0fs)", name, deployment.state, time.monotonic() - start)
            time.sleep(interval)
