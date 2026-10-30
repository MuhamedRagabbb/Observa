import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

for port in (8000, 8001, 8002, 8003, 8090):
    with urlopen(f"http://localhost:{port}/health", timeout=3) as response:
        assert response.status == 200
for _ in range(10):
    request = Request(
        "http://localhost:8000/api/orders",
        method="POST",
        data=json.dumps({"product_id": "sku-001", "quantity": 2, "customer_id": "smoke-user"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            print(
                json.dumps(
                    {"order": json.load(response), "trace_id": response.headers["x-trace-id"]}, indent=2
                )
            )
        break
    except HTTPError as exc:
        if exc.code != 402:
            raise
else:
    raise SystemExit("No successful payment")
