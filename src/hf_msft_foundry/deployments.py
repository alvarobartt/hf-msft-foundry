import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
from azure.mgmt.cognitiveservices.models import (
    ManagedComputeDeployment,
    ManagedComputeDeploymentProperties,
    PatchResourceSku,
    Sku,
)

from hf_msft_foundry.accounts import Account
from hf_msft_foundry.azure import azure_errors
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
    def __init__(self, management: CognitiveServicesManagementClient, account: Account) -> None:
        self.operations = management.managed_compute_deployments
        self.account = account

    def not_found(self, name: str) -> str:
        return f"Deployment '{name}' not found in the Microsoft Foundry resource '{self.account.name}'"

    def all(self) -> list[Deployment]:
        with azure_errors(f"Microsoft Foundry resource '{self.account.name}' not found"):
            items = list(self.operations.list(self.account.resource_group, self.account.name))
        return [Deployment.from_arm(item.as_dict()) for item in items]

    def get(self, name: str) -> Deployment:
        with azure_errors(self.not_found(name)):
            item = self.operations.get(self.account.resource_group, self.account.name, name)
        return Deployment.from_arm(item.as_dict())

    def create(self, model: Model, template: Template, name: str | None = None, instances: int = 1) -> Deployment:
        name = name or default_deployment_name(model.name)
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"Invalid deployment name '{name}', use 2 to 64 letters, digits, underscores or hyphens (no dots)"
            )
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
        with azure_errors(f"Microsoft Foundry resource '{self.account.name}' not found"):
            self.operations.begin_create_or_update(
                self.account.resource_group,
                self.account.name,
                name,
                ManagedComputeDeployment(
                    sku=Sku(name=SKU, capacity=instances),
                    properties=ManagedComputeDeploymentProperties(
                        model=model.uri,
                        deployment_template=template.uri,
                        accelerator_type=template.accelerator_type,
                        version_upgrade_option="OnceNewDefaultVersionAvailable",
                    ),
                ),
            )
        return self.get(name)

    def update(self, name: str, instances: int) -> Deployment:
        logger.info("Scaling deployment '%s' to %s instance(s)", name, instances)
        with azure_errors(self.not_found(name)):
            self.operations.begin_update(
                self.account.resource_group,
                self.account.name,
                name,
                PatchResourceSku(sku=Sku(name=SKU, capacity=instances)),
            )
        return self.get(name)

    def delete(self, name: str) -> None:
        self.get(name)
        with azure_errors(self.not_found(name)):
            self.operations.begin_delete(self.account.resource_group, self.account.name, name)
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
