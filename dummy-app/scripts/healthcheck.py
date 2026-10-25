import os
from urllib.request import urlopen

urls = os.getenv("HEALTH_URLS", f"http://127.0.0.1:{os.getenv('PORT', '8000')}/health")
for url in urls.split(","):
    with urlopen(url, timeout=2) as response:
        if response.status != 200:
            raise SystemExit(1)
