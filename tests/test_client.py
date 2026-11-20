import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
import requests
from azure.core.credentials import AccessToken
from requests.adapters import BaseAdapter

from hf_msft_foundry.client import Foundry
from hf_msft_foundry.deployments import default_deployment_name
from hf_msft_foundry.errors import AzureRequestError, DeploymentFailedError, NotFoundError
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
    body: Any = None


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
        body = json.loads(request.body) if request.body else None
        resp = self.handler(Req(request.method or "GET", parts.path, dict(parse_qsl(parts.query)), body))
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


def usage(scope: str, used: int, limit: int) -> dict[str, Any]:
    return {
        "name": {"value": f"ManagedCompute.{scope}.H100_80GB"},
        "currentValue": used,
        "limit": limit,
        "offerScope": scope,
    }


CAPACITY = {
    "acceleratorType": "H100_80GB",
    "availableAccelerators": 8,
    "offerScope": "Global",
    "deploymentSizeCapacities": [{"modelInstanceAcceleratorCount": 2, "largestDeploymentCapacity": 4}],
}


class FakeArm:
    def __init__(self) -> None:
        self.deployments: dict[str, dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = []
        self.provisioned: list[str] = []
        self.failure: Resp | None = None

    def __call__(self, request: Req) -> Resp:
        path = request.path
        if self.failure and path.startswith(f"/subscriptions/{SUBSCRIPTION}"):
            return self.failure
        if not path.startswith(f"/subscriptions/{SUBSCRIPTION}"):
            return registry(request)
        if path.endswith("/providers/Microsoft.CognitiveServices/accounts"):
            accounts = [{"id": ACCOUNT_ID, "name": "demo", "location": "eastus", "kind": "AIServices"}]
            return Resp(200, {"value": [*accounts, {"id": ACCOUNT_ID, "name": "other", "kind": "OpenAI"}]})
        if path.endswith("/listKeys"):
            return Resp(200, {"key1": "secret1", "key2": "secret2"})
        if path.endswith("/managedComputeCapacities"):
            assert request.params["offer"] == "GlobalManagedCompute"
            items = [{"properties": CAPACITY}, {"properties": {**CAPACITY, "offerScope": "DataZone"}}]
            return Resp(200, {"value": items})
        if path.endswith("/locations/eastus/managedComputeUsages"):
            return Resp(200, {"value": [usage("Global", 2, 8), usage("DataZone", 0, 0)]})
        if "/managedComputeDeployments" in path:
            return self.deployment(request)
        if request.method == "PUT":
            self.provisioned.append(path)
            return Resp(201, {})
        if request.method == "GET" and path in self.provisioned:
            return Resp(200, {"properties": {"provisioningState": "Succeeded"}})
        return Resp(404, {"error": {"code": "NotFound", "message": "Not found"}})

    def deployment(self, request: Req) -> Resp:
        name = request.path.rsplit("/managedComputeDeployments", 1)[1].strip("/")
        if not name:
            return Resp(200, {"value": list(self.deployments.values())})
        if request.method == "PUT":
            self.created.append(request.body)
            properties = {**request.body["properties"], "provisioningState": "Creating"}
            self.deployments[name] = {"name": name, **request.body, "properties": properties}
            return Resp(201, self.deployments[name])
        if name not in self.deployments:
            return Resp(404, {"error": {"code": "NotFound", "message": "Not found"}})
        item = self.deployments[name]
        if request.method == "PATCH":
            item["sku"]["capacity"] = request.body["sku"]["capacity"]
        elif request.method == "DELETE":
            self.deployments.pop(name)
            return Resp(202)
        elif item["properties"]["provisioningState"] == "Creating":
            item["properties"]["provisioningState"] = "Succeeded"
        return Resp(200, item)


@pytest.fixture
def arm() -> FakeArm:
    return FakeArm()


def make_foundry(arm: FakeArm, account: str = "demo") -> Foundry:
    session = requests.Session()
    session.mount("https://", Adapter(arm))
    return Foundry(subscription_id=SUBSCRIPTION, account_name=account, credential=Credential(), session=session)


@pytest.fixture
def foundry(arm: FakeArm) -> Foundry:
    return make_foundry(arm)


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


def test_missing_account(arm: FakeArm) -> None:
    with pytest.raises(NotFoundError, match="'missing' not found"):
        _ = make_foundry(arm, "missing").account


def test_create_account(foundry: Foundry, arm: FakeArm) -> None:
    account = foundry.accounts.create("new", "new-rg", "eastus", interval=0)
    assert account.endpoint == "https://new.services.ai.azure.com"
    assert [path.rsplit("/", 2)[-2:] for path in arm.provisioned] == [
        ["resourcegroups", "new-rg"],
        ["accounts", "new"],
        ["projects", "new-project"],
    ]


def test_deploy_scale_delete(foundry: Foundry, arm: FakeArm) -> None:
    deployment = foundry.deploy(
        "google/gemma-4-31B-it", template="google--gemma-4-31b-it--256k-nvidia-2xh100", name="gemma"
    )
    body = arm.created[0]
    assert body["properties"]["model"] == MODEL
    assert body["properties"]["deploymentTemplate"].endswith(
        "google--gemma-4-31b-it--256k-nvidia-2xh100/labels/latest"
    )
    assert body["properties"]["acceleratorType"] == "H100_80GB"
    assert body["sku"] == {"name": "GlobalManagedCompute", "capacity": 1}
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


def test_rejects_invalid_deployment_names(foundry: Foundry) -> None:
    for name in ["", "a", "gemma-4.1"]:
        with pytest.raises(ValueError, match="Invalid deployment name"):
            foundry.deployments.delete(name)


def test_network_errors_raise_azure_request_error() -> None:
    def disconnect(request: Req) -> Resp:
        raise requests.ConnectionError("Remote end closed connection without response")

    session = requests.Session()
    session.mount("https://", Adapter(disconnect))
    foundry = Foundry(subscription_id=SUBSCRIPTION, account_name="demo", credential=Credential(), session=session)
    with pytest.raises(AzureRequestError, match="Remote end closed connection"):
        foundry.deployments.all()


def test_delete_and_scale_missing_deployment(foundry: Foundry) -> None:
    with pytest.raises(NotFoundError, match="Deployment 'missing' not found"):
        foundry.deployments.delete("missing")
    with pytest.raises(NotFoundError, match="Deployment 'missing' not found"):
        foundry.deployments.update("missing", instances=2)


def test_wait_raises_on_failure(foundry: Foundry, arm: FakeArm) -> None:
    arm.deployments["broken"] = {
        "name": "broken",
        "properties": {"provisioningState": "Failed"},
    }
    with pytest.raises(DeploymentFailedError, match="Failed"):
        foundry.deployments.wait("broken", interval=0)


def test_azure_error_message(foundry: Foundry, arm: FakeArm) -> None:
    _ = foundry.account
    arm.failure = Resp(409, {"error": {"code": "RequestConflict", "message": "Deployment is being created"}})
    with pytest.raises(AzureRequestError, match="409: RequestConflict: Deployment is being created"):
        foundry.deployments.all()
