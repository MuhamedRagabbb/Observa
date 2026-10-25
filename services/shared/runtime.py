import asyncio
import time
from uuid import uuid4

from services.shared.models import RuntimeUpdate


class Runtime:
    """A local expiring lease. Target recovery never depends on the controller being alive."""

    def __init__(self, telemetry):
        self.telemetry = telemetry
        self.release_version = "v1"
        self.deployment_id = f"deploy-{uuid4()}"
        self.settings: RuntimeUpdate | None = None
        self.released = asyncio.Event()
        self.released.set()
        self.expiry_task: asyncio.Task | None = None

    def attributes(self):
        return {"service.version": self.release_version, "deployment.id": self.deployment_id}

    async def update(self, settings: RuntimeUpdate | None):
        if self.expiry_task and self.expiry_task is not asyncio.current_task():
            self.expiry_task.cancel()
            await asyncio.gather(self.expiry_task, return_exceptions=True)
        self.released.set()
        self.released = asyncio.Event()
        self.settings = settings
        version = settings.release_version if settings else "v1"
        if version != self.release_version:
            self.release_version = version
            self.deployment_id = f"deploy-{uuid4()}"
            self.telemetry.log("INFO", "release_changed", "Orders release activated", **self.attributes())
        if settings:
            self.expiry_task = asyncio.create_task(self._expire(max(0, settings.expires_at - time.time())))
        else:
            self.released.set()
            self.expiry_task = None

    async def _expire(self, seconds):
        await asyncio.sleep(seconds)
        await self.update(None)

    async def hold(self):
        config, released = self.settings, self.released
        if config and config.db_hold_seconds:
            seconds = max(0, min(config.db_hold_seconds, config.expires_at - time.time()))
            try:
                await asyncio.wait_for(released.wait(), seconds)
            except TimeoutError:
                pass

    async def close(self):
        await self.update(None)
