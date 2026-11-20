import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

import pytest
import requests
from azure.core.credentials import AccessToken
from azure.core.exceptions import ResourceNotFoundError
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
from requests.adapters import BaseAdapter

from hf_msft_foundry.client import Foundry
from hf_msft_foundry.deployments import default_deployment_name
from hf_msft_foundry.errors import DeploymentFailedError, NotFoundError
from hf_msft_foundry.registry import to_foundry_name

SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"
ACCOUNT_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/demo-rg/providers/Microsoft.CognitiveServices/accounts/demo"
)
REGISTRY = (
    "/subscriptions/reg/resourceGroups/reg-rg/providers/Microsoft.MachineLearningServices/registries/azure-huggingface"
)
MODEL = "azureml://registries/azure-huggingface/models/google--gemma-4-31b-it/versions/9"
TEMPLATES = "azureml://registries/azure-huggingface/deploymenttemplates"


@dataclass
class Resp:
    status_code: int
    json: Any = None


@dataclass
class Req:
    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)


class Adapter(BaseAdapter):
    def __init__(self, handler: Callable[[Req], Resp]) -> None:
        super().__init__()
        self.handler = handler

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float, float] | tuple[float, None] | None = None,
        verify: bool | str = True,
        cert: bytes | str | tuple[bytes | str, bytes | str] | None = None,
        proxies: Mapping[str, str] | None = None,
    ) -> requests.Response:
        parts = urlsplit(request.url or "")
        resp = self.handler(Req(request.method or "GET", parts.path, dict(parse_qsl(parts.query))))
        response = requests.Response()
        response.status_code = resp.status_code
        response._content = json.dumps(resp.json).encode() if resp.json is not None else b""
        response.url = request.url or ""
        response.request = request
        return response

    def close(self) -> None:
        pass


class Credential:
    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken("token", 0)


def template(name: str, accelerator: str, count: int, labels: list[str]) -> dict[str, Any]:
    return {
        "properties": {
            "name": name,
            "version": "2" if "latest" in labels else "1",
            "displayName": name,
            "labels": labels,
            "acceleratorMaps": [{"acceleratorType": accelerator, "numberOfAcceleratorsPerModelInstance": count}],
            "environmentVariables": {"AZ_VLLM_MAX_MODEL_LEN": "262144"},
            "environmentId": "azureml://registries/azure-huggingface/environments/hf-vllm-v0-33-0-nvidia/versions/2",
        }
    }


def registry(request: Req) -> Resp:
    if request.path == "/api/retail/prices":
        items = [
            {"skuName": "Mngd H100_80GB Gl", "unitOfMeasure": "1 Hour", "retailPrice": 7.91},
            {"skuName": "Mngd H200_141GB Gl", "unitOfMeasure": "1 Hour", "retailPrice": 9.1},
            {"skuName": "Mngd Hosting 1", "unitOfMeasure": "1 Hour", "retailPrice": 35.15},
        ]
        return Resp(200, {"Items": items, "NextPageLink": None})
    if request.path.endswith("/discovery"):
        return Resp(
            200,
            {
                "subscriptionId": "reg",
                "resourceGroup": "reg-rg",
                "primaryRegionResourceProviderUri": "https://registry.test/",
            },
        )
    if request.path.endswith(f"{REGISTRY}/models"):
        names = ["google--gemma-4-31b-it", "google--gemma-4-e4b-it"]
        return Resp(200, {"value": [{"name": n} for n in names], "nextLink": "https://registry.test/next?page=2"})
    if request.path == "/next":
        return Resp(200, {"value": [{"name": "zai-org--glm-5.3-flash"}]})
    if request.path.endswith("/models/google--gemma-4-31b-it"):
        return Resp(200, {"properties": {"latestVersion": "9"}})
    if request.path.endswith("/models/google--gemma-4-31b-it/versions/8"):
        return Resp(200, {"properties": {"tags": {}, "allowedDeploymentTemplates": []}})
    if request.path.endswith("/models/google--gemma-4-31b-it/versions/9"):
        return Resp(
            200,
            {
                "properties": {
                    "tags": {"huggingface_model_id": "google/gemma-4-31B-it", "task": "chat-completion"},
                    "defaultDeploymentTemplate": {
                        "assetId": f"{TEMPLATES}/google--gemma-4-31b-it--256k-nvidia-h200/versions/3"
                    },
                    "allowedDeploymentTemplates": [
                        {"assetId": f"{TEMPLATES}/google--gemma-4-31b-it--256k-nvidia-h200/labels/latest"},
                        {"assetId": f"{TEMPLATES}/google--gemma-4-31b-it--256k-nvidia-2xh100/labels/latest"},
                    ],
                }
            },
        )
    if request.path.endswith("/deploymenttemplates"):
        name = request.params["name"]
        accelerator, count = ("H200_141GB", 1) if name.endswith("h200") else ("H100_80GB", 2)
        return Resp(
            200, {"value": [template(name, accelerator, count, ["latest"]), template(name, accelerator, count, [])]}
        )
    return Resp(404, {"error": {"code": "NotFound"}})


class Item(SimpleNamespace):
    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.get("raw", {})


class FakeDeployments:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.created: list[Any] = []

    def list(self, resource_group: str, account: str) -> list[Item]:
        return [Item(raw=item) for item in self.items.values()]

    def get(self, resource_group: str, account: str, name: str) -> Item:
        if name not in self.items:
            raise ResourceNotFoundError("Not found")
        if self.items[name]["properties"]["provisioningState"] == "Creating":
            self.items[name]["properties"]["provisioningState"] = "Succeeded"
        return Item(raw=self.items[name])

    def begin_create_or_update(self, resource_group: str, account: str, name: str, resource: Any) -> None:
        self.created.append(resource)
        item = resource.as_dict()
        self.items[name] = {
            "name": name,
            **item,
            "properties": {**item["properties"], "provisioningState": "Creating"},
        }

    def begin_update(self, resource_group: str, account: str, name: str, properties: Any) -> None:
        if name not in self.items:
            raise ResourceNotFoundError("Not found")
        self.items[name]["sku"]["capacity"] = properties.sku.capacity

    def begin_delete(self, resource_group: str, account: str, name: str) -> None:
        self.items.pop(name)


def usage(scope: str, used: int, limit: int) -> Item:
    return Item(
        raw={
            "name": {"value": f"ManagedCompute.{scope}.H100_80GB"},
            "currentValue": used,
            "limit": limit,
            "offerScope": scope,
        }
    )


class FakeManagement:
    def __init__(self) -> None:
        self.managed_compute_deployments = FakeDeployments()
        self.accounts = SimpleNamespace(
            list=lambda: [Item(id=ACCOUNT_ID, name="demo", location="eastus", kind="AIServices")],
            list_keys=lambda resource_group, account: SimpleNamespace(key1="secret1", key2="secret2"),
        )
        capacity = {
            "acceleratorType": "H100_80GB",
            "availableAccelerators": 8,
            "offerScope": "Global",
            "deploymentSizeCapacities": [{"modelInstanceAcceleratorCount": 2, "largestDeploymentCapacity": 4}],
        }
        self.managed_compute_capacities = SimpleNamespace(
            list=lambda offer: [
                Item(raw={"properties": capacity}),
                Item(raw={"properties": {**capacity, "offerScope": "DataZone"}}),
            ]
        )
        self.managed_compute_usages_operation_group = SimpleNamespace(
            list=lambda location: [usage("Global", 2, 8), usage("DataZone", 0, 0)]
        )


@pytest.fixture
def management() -> FakeManagement:
    return FakeManagement()


def make_foundry(management: FakeManagement, account: str = "demo") -> Foundry:
    session = requests.Session()
    session.mount("https://", Adapter(registry))
    return Foundry(
        subscription_id=SUBSCRIPTION,
        account_name=account,
        credential=Credential(),
        session=session,
        management=cast(CognitiveServicesManagementClient, management),
    )


@pytest.fixture
def foundry(management: FakeManagement) -> Foundry:
    return make_foundry(management)


def test_names() -> None:
    assert to_foundry_name("google/gemma-4-31B-it") == "google--gemma-4-31b-it"
    assert default_deployment_name("zai-org--glm-5.3-flash", now=1795083872) == "zai-org--glm-5-3-flash--5083872"
    assert default_deployment_name("org--" + "a" * 80, now=1)[-9:] == "--0000001"
    assert len(default_deployment_name("org--" + "a" * 80)) == 64


def test_versions(foundry: Foundry) -> None:
    assert foundry.registry.model("google/gemma-4-31B-it", version="8").version == "8"
    with pytest.raises(NotFoundError, match="Version 7 .* the latest is 9"):
        foundry.registry.model("google/gemma-4-31B-it", version="7")


def test_resolve_template_by_name_suffix_or_uri(foundry: Foundry) -> None:
    model = foundry.registry.model("google/gemma-4-31B-it")
    name = "google--gemma-4-31b-it--256k-nvidia-2xh100"
    for value in [name, "256k-nvidia-2xh100", f"{TEMPLATES}/{name}/labels/latest"]:
        assert foundry.registry.resolve_template(model, value).name == name
    assert foundry.registry.resolve_template(model, None).name == "google--gemma-4-31b-it--256k-nvidia-h200"
    with pytest.raises(ValueError, match="use one of"):
        foundry.registry.resolve_template(model, "16k-nvidia-a100")


def test_template_prices(foundry: Foundry) -> None:
    model = foundry.registry.model("google/gemma-4-31B-it")
    prices = {t.name: t.price_per_hour for t in foundry.registry.templates(model, foundry.prices)}
    assert prices == {
        "google--gemma-4-31b-it--256k-nvidia-h200": 9.1,
        "google--gemma-4-31b-it--256k-nvidia-2xh100": 15.82,
    }
    assert foundry.prices.hourly("H100_80GB", 2, instances=2) == 31.64
    assert foundry.prices.hourly("MI300_192GB", 1) is None


def test_deploy_rejects_invalid_name(foundry: Foundry) -> None:
    with pytest.raises(ValueError, match="no dots"):
        foundry.deploy("google/gemma-4-31B-it", name="gemma-4.1")


def test_models_paginates_and_searches(foundry: Foundry) -> None:
    assert foundry.registry.models() == ["google--gemma-4-31b-it", "google--gemma-4-e4b-it", "zai-org--glm-5.3-flash"]
    assert foundry.registry.models("Gemma 4 31B") == ["google--gemma-4-31b-it"]


def test_model(foundry: Foundry) -> None:
    model = foundry.registry.model("google/gemma-4-31B-it")
    assert model.hub_id == "google/gemma-4-31B-it"
    assert model.url == "https://huggingface.co/google/gemma-4-31B-it"
    assert model.uri == MODEL
    assert model.default_template == "google--gemma-4-31b-it--256k-nvidia-h200"
    assert len(model.templates) == 2


def test_unknown_model(foundry: Foundry) -> None:
    with pytest.raises(NotFoundError, match="hf msft-foundry models --search"):
        foundry.registry.model("org/unknown")


def test_templates_use_latest_label(foundry: Foundry) -> None:
    model = foundry.registry.model("google/gemma-4-31B-it")
    templates = {t.name: t for t in foundry.registry.templates(model)}
    h100 = templates["google--gemma-4-31b-it--256k-nvidia-2xh100"]
    assert (h100.accelerator_type, h100.accelerator_count, h100.version) == ("H100_80GB", 2, "2")
    assert h100.max_model_len == "262144" and h100.environment == "hf-vllm-v0-33-0-nvidia"
    assert h100.uri.endswith("google--gemma-4-31b-it--256k-nvidia-2xh100/labels/latest")
    assert templates["google--gemma-4-31b-it--256k-nvidia-h200"].default is True


def test_account_capacity_quota_keys(foundry: Foundry) -> None:
    assert foundry.account.resource_group == "demo-rg"
    assert foundry.account.openai_base_url == "https://demo.services.ai.azure.com/openai/v1"
    assert foundry.accounts.capacity() == [
        {"accelerator_type": "H100_80GB", "available": 8, "largest_deployment": {2: 4}}
    ]
    assert foundry.accounts.quota("eastus") == [{"accelerator_type": "H100_80GB", "used": 2, "limit": 8}]
    assert foundry.accounts.keys(foundry.account) == {"key1": "secret1", "key2": "secret2"}


def test_missing_account(management: FakeManagement) -> None:
    with pytest.raises(NotFoundError, match="'missing' not found"):
        _ = make_foundry(management, "missing").account


def test_deploy_scale_delete(foundry: Foundry, management: FakeManagement) -> None:
    deployment = foundry.deploy(
        "google/gemma-4-31B-it", template="google--gemma-4-31b-it--256k-nvidia-2xh100", name="gemma"
    )
    body = management.managed_compute_deployments.created[0]
    assert body.properties.model == MODEL
    assert body.properties.deployment_template.endswith("google--gemma-4-31b-it--256k-nvidia-2xh100/labels/latest")
    assert body.properties.accelerator_type == "H100_80GB"
    assert body.sku.name == "GlobalManagedCompute" and body.sku.capacity == 1
    assert deployment.state == "Succeeded"
    assert foundry.deployments.wait("gemma", interval=0).state == "Succeeded"
    assert foundry.deployments.update("gemma", instances=2).instances == 2
    assert [d.name for d in foundry.deployments.all()] == ["gemma"]
    foundry.deployments.delete("gemma")
    foundry.deployments.wait_deleted("gemma", interval=0)
    assert foundry.deployments.all() == []


def test_deploy_uses_default_template(foundry: Foundry) -> None:
    assert foundry.deploy("google/gemma-4-31B-it").template == "google--gemma-4-31b-it--256k-nvidia-h200"


def test_deploy_rejects_unknown_template(foundry: Foundry) -> None:
    with pytest.raises(ValueError, match="not available"):
        foundry.deploy("google/gemma-4-31B-it", template="nope")


def test_deploy_default_name(foundry: Foundry) -> None:
    assert foundry.deploy("google/gemma-4-31B-it").name.startswith("google--gemma-4-31b-it--")


def test_delete_and_scale_missing_deployment(foundry: Foundry) -> None:
    with pytest.raises(NotFoundError, match="Deployment 'missing' not found"):
        foundry.deployments.delete("missing")
    with pytest.raises(NotFoundError, match="Deployment 'missing' not found"):
        foundry.deployments.update("missing", instances=2)


def test_wait_raises_on_failure(foundry: Foundry, management: FakeManagement) -> None:
    management.managed_compute_deployments.items["broken"] = {
        "name": "broken",
        "properties": {"provisioningState": "Failed"},
    }
    with pytest.raises(DeploymentFailedError, match="Failed"):
        foundry.deployments.wait("broken", interval=0)
