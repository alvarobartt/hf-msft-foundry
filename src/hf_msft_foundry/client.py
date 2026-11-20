import os
import subprocess
from functools import cached_property

import requests
from azure.core.credentials import TokenCredential

from hf_msft_foundry.accounts import Account, Accounts
from hf_msft_foundry.azure import ARM, AzureClient
from hf_msft_foundry.deployments import Deployment, Deployments
from hf_msft_foundry.pricing import Prices
from hf_msft_foundry.registry import Registry


class Foundry:
    def __init__(
        self,
        subscription_id: str | None = None,
        account_name: str | None = None,
        credential: TokenCredential | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.azure = AzureClient(credential=credential, session=session)
        self.registry = Registry(self.azure)
        self._subscription_id = subscription_id or os.getenv("AZURE_SUBSCRIPTION_ID")
        self._account_name = account_name or os.getenv("FOUNDRY_ACCOUNT_NAME")

    @cached_property
    def subscription_id(self) -> str:
        if self._subscription_id:
            return self._subscription_id
        try:
            result = subprocess.run(
                ["az", "account", "show", "--query", "id", "-o", "tsv"], capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except FileNotFoundError:
            pass
        subscriptions = self.azure.paginate(f"{ARM}/subscriptions", {"api-version": "2022-12-01"})
        if len(subscriptions) != 1:
            raise ValueError(
                f"Found {len(subscriptions)} Azure subscriptions, set --subscription-id or AZURE_SUBSCRIPTION_ID"
            )
        return subscriptions[0]["subscriptionId"]

    @cached_property
    def accounts(self) -> Accounts:
        return Accounts(self.azure, self.subscription_id)

    @cached_property
    def account(self) -> Account:
        return self.accounts.get(self._account_name)

    @cached_property
    def deployments(self) -> Deployments:
        return Deployments(self.azure, self.account)

    @cached_property
    def prices(self) -> Prices:
        return Prices(self.azure.session)

    def deploy(
        self,
        model_id: str,
        template: str | None = None,
        name: str | None = None,
        instances: int = 1,
        version: str | None = None,
    ) -> Deployment:
        model = self.registry.model(model_id, version)
        selected = self.registry.resolve_template(model, template)
        selected.price_per_hour = self.prices.hourly(selected.accelerator_type, selected.accelerator_count, instances)
        return self.deployments.create(model, selected, name, instances)
