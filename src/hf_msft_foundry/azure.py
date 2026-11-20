from typing import Any

import requests
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hf_msft_foundry.errors import AzureRequestError, NotFoundError

ARM = "https://management.azure.com"
RETRY = Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504], respect_retry_after_header=True)


def session_with_retries() -> requests.Session:
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=RETRY))
    return session


class AzureClient:
    def __init__(self, credential: TokenCredential | None = None, session: requests.Session | None = None) -> None:
        self.credential = credential or DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self.session = session or session_with_retries()

    def request(self, method: str, url: str, not_found: str | None = None, **kwargs: Any) -> Any:
        token = self.credential.get_token(f"{ARM}/.default").token
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = self.session.request(method, url, headers=headers, timeout=120, **kwargs)
        except requests.RequestException as error:
            raise AzureRequestError(503, f"{method} {url.split('?')[0]} failed: {error}") from error
        if response.status_code == 404:
            raise NotFoundError(not_found or f"{url.split('?')[0]} does not exist")
        if not response.ok:
            raise AzureRequestError(response.status_code, error_message(response))
        return response.json() if response.content else {}

    def paginate(self, url: str, params: dict[str, str] | None = None, not_found: str | None = None) -> list[Any]:
        items: list[Any] = []
        next_url: str | None = url
        while next_url:
            data = self.request("GET", next_url, not_found=not_found, params=params)
            items.extend(data.get("value", []))
            next_url, params = data.get("nextLink"), None
        return items


def error_message(response: requests.Response) -> str:
    try:
        error = response.json().get("error", {})
        return f"{error['code']}: {error['message']}"
    except (ValueError, KeyError, TypeError, AttributeError):
        return response.text or response.reason
