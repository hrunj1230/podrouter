"""GPU types with prices and stock, read from the Runpod GraphQL API."""

from __future__ import annotations

_GPU_QUERY = """
query {
  gpuTypes {
    id
    displayName
    memoryInGb
    secureCloud
    communityCloud
    securePrice
    communityPrice
    secureStock: lowestPrice(input: {gpuCount: 1, secureCloud: true}) {
      uninterruptablePrice
      stockStatus
    }
    communityStock: lowestPrice(input: {gpuCount: 1, secureCloud: false}) {
      uninterruptablePrice
      stockStatus
    }
  }
}
"""


def _tier(gpu: dict, prefix: str) -> dict:
    stock = gpu.get(f"{prefix}Stock") or {}
    price = gpu.get(f"{prefix}Price")
    offer = stock.get("uninterruptablePrice")
    return {
        "offered": bool(gpu.get(f"{prefix}Cloud")),
        "price": price if isinstance(price, (int, float)) else None,
        "stock": stock.get("stockStatus"),
        "offer": offer if isinstance(offer, (int, float)) else None,
    }


def gpu_catalog(api_key: str) -> list[dict]:
    """Return one row per GPU type. Prices and stock only, no account secrets."""
    from runpod.api.graphql import run_graphql_query

    payload = run_graphql_query(_GPU_QUERY, api_key)
    rows = []
    for gpu in payload["data"]["gpuTypes"]:
        rows.append(
            {
                "id": gpu["id"],
                "name": gpu["displayName"],
                "memory_gb": gpu["memoryInGb"],
                "secure": _tier(gpu, "secure"),
                "community": _tier(gpu, "community"),
            }
        )
    rows.sort(key=lambda item: (item["name"] or "", item["id"]))
    return rows
