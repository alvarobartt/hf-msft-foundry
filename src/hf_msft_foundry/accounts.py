import logging
from dataclasses import dataclass
from typing import Any

from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
from azure.mgmt.cognitiveservices.models import (
    Account as AccountResource,
)
from azure.mgmt.cognitiveservices.models import (
    AccountProperties,
    Identity,
    Project,
    ProjectProperties,
    Sku,
)

from hf_msft_foundry.azure import ARM, AzureClient, azure_errors
from hf_msft_foundry.errors import NotFoundError

logger = logging.getLogger("hf_msft_foundry")

OFFER = "GlobalManagedCompute"


@dataclass
class Account:
    subscription_id: str
    resource_group: str
    name: str
    location: str

    @property
    def endpoint(self) -> str:
        return f"https://{self.name}.services.ai.azure.com"

    @property
    def openai_base_url(self) -> str:
        return f"{self.endpoint}/openai/v1"


def resource_group(resource_id: str) -> str:
    return resource_id.split("/resourceGroups/", 1)[1].split("/", 1)[0]


class Accounts:
    def __init__(
        self, management: CognitiveServicesManagementClient, azure: AzureClient, subscription_id: str
    ) -> None:
        self.management = management
        self.azure = azure
        self.subscription_id = subscription_id

    def all(self) -> list[Account]:
        with azure_errors(f"Subscription {self.subscription_id} not found"):
            return [
                Account(self.subscription_id, resource_group(item.id or ""), item.name or "", item.location or "")
                for item in self.management.accounts.list()
                if item.kind == "AIServices"
            ]

    def get(self, name: str | None = None) -> Account:
        accounts = self.all()
        if name:
            for account in accounts:
                if account.name == name:
                    return account
            raise NotFoundError(
                f"Microsoft Foundry resource '{name}' not found in subscription {self.subscription_id}"
            )
        if len(accounts) == 1:
            return accounts[0]
        if not accounts:
            raise NotFoundError(
                f"No Microsoft Foundry resources found in subscription {self.subscription_id}, "
                "create one with `hf msft-foundry accounts create`"
            )
        names = ", ".join(account.name for account in accounts)
        raise ValueError(f"Found {len(accounts)} Microsoft Foundry resources, set --account to one of: {names}")

    def create(self, name: str, resource_group: str, location: str, project: str | None = None) -> Account:
        logger.info("Creating resource group '%s' in %s", resource_group, location)
        self.azure.request(
            "PUT",
            f"{ARM}/subscriptions/{self.subscription_id}/resourcegroups/{resource_group}",
            params={"api-version": "2021-04-01"},
            json={"location": location},
        )
        logger.info("Creating Microsoft Foundry resource '%s'", name)
        with azure_errors(f"Resource group '{resource_group}' not found"):
            self.management.accounts.begin_create(
                resource_group,
                name,
                AccountResource(
                    location=location,
                    kind="AIServices",
                    sku=Sku(name="S0"),
                    identity=Identity(type="SystemAssigned"),
                    properties=AccountProperties(
                        custom_sub_domain_name=name,
                        allow_project_management=True,
                        public_network_access="Enabled",
                    ),
                ),
            ).result()
            project = project or f"{name}-project"
            logger.info("Creating Microsoft Foundry project '%s'", project)
            self.management.projects.begin_create(
                resource_group,
                name,
                project,
                Project(location=location, identity=Identity(type="SystemAssigned"), properties=ProjectProperties()),
            ).result()
        return Account(self.subscription_id, resource_group, name, location)

    def keys(self, account: Account) -> dict[str, str]:
        with azure_errors(f"Microsoft Foundry resource '{account.name}' not found"):
            keys = self.management.accounts.list_keys(account.resource_group, account.name)
        return {"key1": keys.key1 or "", "key2": keys.key2 or ""}

    def capacity(self) -> list[dict[str, Any]]:
        with azure_errors(f"Subscription {self.subscription_id} not found"):
            items = [item.as_dict() for item in self.management.managed_compute_capacities.list(offer=OFFER)]
        return [
            {
                "accelerator_type": item["properties"]["acceleratorType"],
                "available": item["properties"]["availableAccelerators"],
                "largest_deployment": {
                    size["modelInstanceAcceleratorCount"]: size["largestDeploymentCapacity"]
                    for size in item["properties"].get("deploymentSizeCapacities", [])
                },
            }
            for item in items
            if item["properties"].get("offerScope") == "Global"
        ]

    def quota(self, location: str) -> list[dict[str, Any]]:
        with azure_errors(f"Location '{location}' not found"):
            items = [item.as_dict() for item in self.management.managed_compute_usages_operation_group.list(location)]
        return [
            {
                "accelerator_type": item["name"]["value"].rsplit(".", 1)[-1],
                "used": item["currentValue"],
                "limit": item["limit"],
            }
            for item in items
            if item.get("offerScope") == "Global"
        ]
