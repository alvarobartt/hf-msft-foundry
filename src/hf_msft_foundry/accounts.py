import logging
import time
from dataclasses import dataclass
from typing import Any

from hf_msft_foundry.azure import ARM, AzureClient
from hf_msft_foundry.errors import FoundryError, NotFoundError

logger = logging.getLogger("hf_msft_foundry")

OFFER = "GlobalManagedCompute"
API_VERSION = "2026-11-01"
PROVIDER = "providers/Microsoft.CognitiveServices"


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

    @property
    def resource_url(self) -> str:
        return (
            f"{ARM}/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}/{PROVIDER}"
            f"/accounts/{self.name}"
        )


def resource_group(resource_id: str) -> str:
    return resource_id.split("/resourceGroups/", 1)[1].split("/", 1)[0]


class Accounts:
    def __init__(self, azure: AzureClient, subscription_id: str) -> None:
        self.azure = azure
        self.subscription_id = subscription_id
        self.params = {"api-version": API_VERSION}

    @property
    def subscription_url(self) -> str:
        return f"{ARM}/subscriptions/{self.subscription_id}"

    def all(self) -> list[Account]:
        items = self.azure.paginate(
            f"{self.subscription_url}/{PROVIDER}/accounts",
            self.params,
            not_found=f"Subscription {self.subscription_id} not found",
        )
        return [
            Account(self.subscription_id, resource_group(item["id"]), item["name"], item["location"])
            for item in items
            if item.get("kind") == "AIServices"
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

    def provision(self, url: str, body: dict[str, Any], interval: int = 10) -> None:
        self.azure.request("PUT", url, params=self.params, json=body)
        while True:
            state = self.azure.request("GET", url, params=self.params).get("properties", {}).get("provisioningState")
            if state == "Succeeded":
                return
            if state in {"Failed", "Canceled"}:
                raise FoundryError(f"{url.split('/providers/', 1)[-1]} ended in state {state}")
            time.sleep(interval)

    def create(
        self, name: str, resource_group: str, location: str, project: str | None = None, interval: int = 10
    ) -> Account:
        logger.info("Creating resource group '%s' in %s", resource_group, location)
        self.azure.request(
            "PUT",
            f"{self.subscription_url}/resourcegroups/{resource_group}",
            params={"api-version": "2021-04-01"},
            json={"location": location},
        )
        account = Account(self.subscription_id, resource_group, name, location)
        logger.info("Creating Microsoft Foundry resource '%s'", name)
        self.provision(
            account.resource_url,
            {
                "location": location,
                "kind": "AIServices",
                "sku": {"name": "S0"},
                "identity": {"type": "SystemAssigned"},
                "properties": {
                    "customSubDomainName": name,
                    "allowProjectManagement": True,
                    "publicNetworkAccess": "Enabled",
                },
            },
            interval,
        )
        project = project or f"{name}-project"
        logger.info("Creating Microsoft Foundry project '%s'", project)
        self.provision(
            f"{account.resource_url}/projects/{project}",
            {"location": location, "identity": {"type": "SystemAssigned"}, "properties": {}},
            interval,
        )
        return account

    def keys(self, account: Account) -> dict[str, str]:
        keys = self.azure.request(
            "POST",
            f"{account.resource_url}/listKeys",
            not_found=f"Microsoft Foundry resource '{account.name}' not found",
            params=self.params,
        )
        return {"key1": keys.get("key1", ""), "key2": keys.get("key2", "")}

    def capacity(self) -> list[dict[str, Any]]:
        items = self.azure.paginate(
            f"{self.subscription_url}/{PROVIDER}/managedComputeCapacities",
            {**self.params, "offer": OFFER},
            not_found=f"Subscription {self.subscription_id} not found",
        )
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
        items = self.azure.paginate(
            f"{self.subscription_url}/{PROVIDER}/locations/{location}/managedComputeUsages",
            self.params,
            not_found=f"Location '{location}' not found",
        )
        return [
            {
                "accelerator_type": item["name"]["value"].rsplit(".", 1)[-1],
                "used": item["currentValue"],
                "limit": item["limit"],
            }
            for item in items
            if item.get("offerScope") == "Global"
        ]
