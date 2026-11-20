from dataclasses import dataclass, field
from functools import cached_property

from hf_msft_foundry.azure import AzureClient
from hf_msft_foundry.errors import NotFoundError
from hf_msft_foundry.pricing import Prices

REGISTRY = "azure-huggingface"
REGISTRY_HOST = "https://eastus.api.azureml.ms"
MODELS_API_VERSION = "2021-10-01-dataplanepreview"
TEMPLATES_API_VERSION = "2026-05-01-preview"


def to_foundry_name(model_id: str) -> str:
    return model_id.strip().lower().replace("/", "--")


def template_name(asset_id: str) -> str:
    return asset_id.split("/deploymenttemplates/", 1)[1].split("/", 1)[0]


@dataclass
class Model:
    name: str
    version: str
    hub_id: str | None
    task: str | None
    input_modalities: str | None
    context_window: str | None
    default_template: str | None
    templates: list[str] = field(default_factory=list)

    @property
    def uri(self) -> str:
        return f"azureml://registries/{REGISTRY}/models/{self.name}/versions/{self.version}"

    @property
    def url(self) -> str | None:
        return f"https://huggingface.co/{self.hub_id}" if self.hub_id else None


@dataclass
class Template:
    name: str
    version: str
    description: str | None
    accelerator_type: str
    accelerator_count: int
    max_model_len: str | None
    environment: str
    price_per_hour: float | None = None
    default: bool = False

    @property
    def uri(self) -> str:
        return f"azureml://registries/{REGISTRY}/deploymenttemplates/{self.name}/labels/latest"


class Registry:
    def __init__(self, azure: AzureClient) -> None:
        self.azure = azure

    @cached_property
    def discovery(self) -> dict[str, str]:
        return self.azure.request("GET", f"{REGISTRY_HOST}/registrymanagement/v1.0/registries/{REGISTRY}/discovery")

    @property
    def path(self) -> str:
        return (
            f"/subscriptions/{self.discovery['subscriptionId']}/resourceGroups/{self.discovery['resourceGroup']}"
            f"/providers/Microsoft.MachineLearningServices/registries/{REGISTRY}"
        )

    @property
    def models_url(self) -> str:
        host = self.discovery["primaryRegionResourceProviderUri"].rstrip("/")
        return f"{host}/mferp/managementfrontend{self.path}/models"

    def models(self, search: str | None = None) -> list[str]:
        params = {"api-version": MODELS_API_VERSION, "listViewType": "ActiveOnly"}
        names = sorted(item["name"] for item in self.azure.paginate(self.models_url, params))
        if search:
            terms = to_foundry_name(search).replace("--", " ").split()
            names = [name for name in names if all(term in name for term in terms)]
        return names

    def model(self, model_id: str, version: str | None = None) -> Model:
        name = to_foundry_name(model_id)
        params = {"api-version": MODELS_API_VERSION}
        try:
            latest = self.azure.request("GET", f"{self.models_url}/{name}", params=params)
        except NotFoundError as error:
            raise NotFoundError(
                f"Model '{model_id}' is not available in the Hugging Face collection on Microsoft Foundry, "
                "find the available ones with `hf msft-foundry models --search <query>`"
            ) from error
        version = version or latest["properties"]["latestVersion"]
        try:
            properties = self.azure.request("GET", f"{self.models_url}/{name}/versions/{version}", params=params)[
                "properties"
            ]
        except NotFoundError as error:
            raise NotFoundError(
                f"Version {version} of '{model_id}' not found, the latest is {latest['properties']['latestVersion']}"
            ) from error
        tags = properties.get("tags", {})
        default = (properties.get("defaultDeploymentTemplate") or {}).get("assetId")
        return Model(
            name=name,
            version=version,
            hub_id=tags.get("huggingface_model_id"),
            task=tags.get("task"),
            input_modalities=tags.get("inputModalities"),
            context_window=tags.get("textContextWindow"),
            default_template=template_name(default) if default else None,
            templates=[template_name(t["assetId"]) for t in properties.get("allowedDeploymentTemplates") or []],
        )

    def template(self, name: str) -> Template:
        url = f"{REGISTRY_HOST}/genericasset/v2.0{self.path}/deploymenttemplates"
        params = {"api-version": TEMPLATES_API_VERSION, "listViewType": "ActiveOnly", "name": name}
        versions = [item["properties"] for item in self.azure.request("GET", url, params=params).get("value", [])]
        latest = next((v for v in versions if "latest" in v.get("labels", [])), None)
        if latest is None:
            raise NotFoundError(f"Deployment template '{name}' has no version labelled as latest")
        accelerator = (latest.get("acceleratorMaps") or [{}])[0]
        env = latest.get("environmentVariables") or {}
        return Template(
            name=name,
            version=latest["version"],
            description=latest.get("displayName"),
            accelerator_type=accelerator.get("acceleratorType", ""),
            accelerator_count=accelerator.get("numberOfAcceleratorsPerModelInstance", 0),
            max_model_len=next((v for k, v in env.items() if k.endswith("MAX_MODEL_LEN")), None),
            environment=latest.get("environmentId", "").split("/environments/", 1)[-1].split("/", 1)[0],
        )

    def templates(self, model: Model, prices: Prices | None = None) -> list[Template]:
        templates = [self.template(name) for name in model.templates]
        for template in templates:
            template.default = template.name == model.default_template
            if prices:
                template.price_per_hour = prices.hourly(template.accelerator_type, template.accelerator_count)
        return templates

    def resolve_template(self, model: Model, value: str | None) -> Template:
        if value is None:
            if model.default_template is None:
                raise ValueError(f"{model.name} has no default deployment template, pass --deployment-template")
            return self.template(model.default_template)
        name = template_name(value) if "/deploymenttemplates/" in value else value.lower()
        matches = [t for t in model.templates if t == name or t == f"{model.name.replace('.', '-')}--{name}"]
        if not matches:
            matches = [t for t in model.templates if t.endswith(f"--{name}")]
        if len(matches) != 1:
            options = ", ".join(model.templates)
            raise ValueError(f"Deployment template '{value}' is not available for {model.name}, use one of: {options}")
        return self.template(matches[0])
