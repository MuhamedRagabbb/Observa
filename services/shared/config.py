import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "local"))
    otlp_endpoint: str = field(default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""))
    sample_ratio: float = field(default_factory=lambda: float(os.getenv("TRACE_SAMPLE_RATIO", "1")))
    dsn: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    pool_max: int = field(default_factory=lambda: int(os.getenv("DB_POOL_MAX", "4")))
    pool_timeout: float = field(default_factory=lambda: float(os.getenv("DB_POOL_TIMEOUT", "0.5")))
    query_timeout: float = field(default_factory=lambda: float(os.getenv("DB_QUERY_TIMEOUT", "3")))
    dependency_timeout: float = field(default_factory=lambda: float(os.getenv("DEPENDENCY_TIMEOUT", "4")))
    orders_url: str = field(default_factory=lambda: os.getenv("ORDERS_URL", "http://orders:8001"))
    inventory_url: str = field(default_factory=lambda: os.getenv("INVENTORY_URL", "http://inventory:8002"))
    payments_url: str = field(default_factory=lambda: os.getenv("PAYMENTS_URL", "http://payments:8003"))
    control_token: str = field(default_factory=lambda: os.getenv("CONTROL_TOKEN", "local-control-only"))
    baseline_decline_rate: float = field(
        default_factory=lambda: float(os.getenv("BASELINE_DECLINE_RATE", "0.02"))
    )

    def __post_init__(self):
        if not 1 <= self.pool_max <= 16 or not 0 < self.pool_timeout <= 10:
            raise ValueError("Invalid bounded database pool configuration")
        if not 0 <= self.sample_ratio <= 1 or not 0 <= self.baseline_decline_rate <= 1:
            raise ValueError("Rates must be between zero and one")
