from typing import Any

from app.tools.gateway import Gateway


def register(gateway: Gateway) -> None:
    def get_customer_policies(customer_id: str) -> dict[str, Any]:
        policies = gateway.store.get_customer_policies(customer_id)
        return {"policies": [p.model_dump(mode="json") for p in policies]}

    gateway.register("get_customer_policies", get_customer_policies)
