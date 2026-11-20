from functools import cached_property

import requests

PRICES_URL = "https://prices.azure.com/api/retail/prices"
PRICES_FILTER = (
    "serviceName eq 'Foundry Models' and productName eq 'Managed Compute' "
    "and armRegionName eq '{region}' and type eq 'Consumption'"
)


class Prices:
    def __init__(self, session: requests.Session, region: str = "eastus") -> None:
        self.session = session
        self.region = region

    @cached_property
    def per_accelerator_hour(self) -> dict[str, float]:
        prices: dict[str, float] = {}
        url: str | None = PRICES_URL
        params: dict[str, str] | None = {"$filter": PRICES_FILTER.format(region=self.region)}
        while url:
            response = self.session.get(url, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()
            for item in data.get("Items", []):
                sku = item.get("skuName", "")
                if sku.startswith("Mngd ") and sku.endswith(" Gl") and item.get("unitOfMeasure") == "1 Hour":
                    prices[sku.removeprefix("Mngd ").removesuffix(" Gl")] = item["retailPrice"]
            url, params = data.get("NextPageLink"), None
        return prices

    def hourly(self, accelerator_type: str, accelerator_count: int, instances: int = 1) -> float | None:
        try:
            price = self.per_accelerator_hour.get(accelerator_type)
        except requests.RequestException:
            return None
        return round(price * accelerator_count * instances, 2) if price is not None else None
