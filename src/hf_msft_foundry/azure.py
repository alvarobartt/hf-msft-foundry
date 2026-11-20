from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import requests
from azure.core.credentials import TokenCredential
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from hf_msft_foundry.errors import AzureRequestError, NotFoundError

ARM = "https://management.azure.com"


@contextmanager
def azure_errors(not_found: str) -> Generator[None]:
    try:
        yield
    except ResourceNotFoundError as error:
        raise NotFoundError(not_found) from error
    except HttpResponseError as error:
        raise AzureRequestError(error.status_code or 500, error.message) from error


class AzureClient:
    def __init__(self, credential: TokenCredential | None = None, session: requests.Session | None = None) -> None:
        self.credential = credential or DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self.session = session or requests.Session()

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        token = self.credential.get_token(f"{ARM}/.default").token
        headers = {"Authorization": f"Bearer {token}"}
        response = self.session.request(method, url, headers=headers, timeout=120, **kwargs)
        if response.status_code == 404:
            raise NotFoundError(f"{url.split('?')[0]} does not exist")
        if not response.ok:
            raise AzureRequestError(response.status_code, response.text or response.reason)
        return response.json() if response.content else {}

    def paginate(self, url: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        while next_url:
            data = self.request("GET", next_url, params=params)
            items.extend(data.get("value", []))
            next_url, params = data.get("nextLink"), None
        return items
