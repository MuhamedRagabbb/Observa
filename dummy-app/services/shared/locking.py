import asyncio
import hashlib


class KeyLocks:
    """Bounded per-process contention control; PostgreSQL remains the durable arbiter."""

    def __init__(self):
        self.locks = [asyncio.Lock() for _ in range(64)]

    def get(self, key):
        bucket = int.from_bytes(hashlib.sha256(key.encode()).digest()[:2], "big") % len(self.locks)
        return self.locks[bucket]
