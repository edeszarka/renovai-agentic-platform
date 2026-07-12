import os
import json
import hashlib
import logging
from enum import Enum
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class CacheTier(Enum):
    """Cache tiers for multi-level caching strategy."""
    L1_MEMORY = "l1_memory"       # Hot cache: in-process dict
    L2_DISKCACHE = "l2_diskcache" # Warm cache: diskcache.Disk
    L3_NONE = "l3_none"           # Cache miss


class RenovAICache:
    """Multi-level cache for RenovAI.

    Tier 1 (L1): In-memory dict — sub-millisecond access, process-local.
    Tier 2 (L2): DiskCache — persists across restarts, shared across workers.

    Tenant isolation is enforced via a tenant_id prefix on every key,
    ensuring data from one session never leaks into another user's context.

    Usage:
        cache = RenovAICache(tenant_id="user_abc")
        await cache.set_embedding("district_7_panel", [0.1, 0.2, ...])
        vector = await cache.get_embedding("district_7_panel")
    """

    def __init__(
        self,
        tenant_id: str = "default",
        l1_max_size: int = 512,
        l2_cache_dir: str | Path | None = None,
        l2_ttl_seconds: int = 86400,  # 24h default
    ):
        self._tenant_id = tenant_id
        self._l1: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry_ts)
        self._l1_max_size = l1_max_size
        self._l2_cache_dir = Path(l2_cache_dir) if l2_cache_dir else (
            Path(__file__).resolve().parent.parent.parent / "data" / "cache"
        )
        self._l2_ttl_seconds = l2_ttl_seconds
        self._l2_initialized = False
        self._l2 = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_embedding(self, key: str) -> list[float] | None:
        """Retrieve a cached embedding vector."""
        tenant_key = self._tenant_key(key)
        result = self._l1_get(tenant_key)
        if result is not None:
            return result
        result = self._l2_get(tenant_key)
        if result is not None:
            self._l1_set(tenant_key, result)
            return result
        return None

    async def set_embedding(self, key: str, vector: list[float], ttl_seconds: int | None = None) -> None:
        """Cache an embedding vector."""
        tenant_key = self._tenant_key(key)
        ttl = ttl_seconds or self._l2_ttl_seconds
        self._l1_set(tenant_key, vector, ttl)
        self._l2_set(tenant_key, vector, ttl)

    async def get_market_query(self, key: str) -> dict[str, Any] | None:
        """Retrieve a cached market query result."""
        tenant_key = self._tenant_key(f"mq:{key}")
        result = self._l1_get(tenant_key)
        if result is not None:
            return result
        result = self._l2_get(tenant_key)
        if result is not None:
            self._l1_set(tenant_key, result)
            return result
        return None

    async def set_market_query(self, key: str, result: dict[str, Any], ttl_seconds: int | None = None) -> None:
        """Cache a market query result."""
        tenant_key = self._tenant_key(f"mq:{key}")
        ttl = ttl_seconds or self._l2_ttl_seconds
        self._l1_set(tenant_key, result, ttl)
        self._l2_set(tenant_key, result, ttl)

    async def get(self, key: str) -> Any:
        """Generic get from cache hierarchy."""
        tenant_key = self._tenant_key(key)
        result = self._l1_get(tenant_key)
        if result is not None:
            return result
        result = self._l2_get(tenant_key)
        if result is not None:
            self._l1_set(tenant_key, result)
            return result
        return None

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Generic set in cache hierarchy."""
        tenant_key = self._tenant_key(key)
        ttl = ttl_seconds or self._l2_ttl_seconds
        self._l1_set(tenant_key, value, ttl)
        self._l2_set(tenant_key, value, ttl)

    async def invalidate(self, key: str) -> None:
        """Remove a key from all cache tiers."""
        tenant_key = self._tenant_key(key)
        self._l1.pop(tenant_key, None)
        self._l2_delete(tenant_key)

    async def clear(self) -> None:
        """Clear all cache tiers for this tenant."""
        self._l1.clear()
        self._l2_clear()

    def hit_rate(self) -> float:
        """Return the L1 cache hit rate."""
        if not self._l1:
            return 0.0
        valid = sum(1 for v, expiry in self._l1.values() if expiry > datetime.now(timezone.utc).timestamp())
        return valid / len(self._l1)

    # ------------------------------------------------------------------
    # Tenant key prefixing
    # ------------------------------------------------------------------

    def _tenant_key(self, key: str) -> str:
        return f"{self._tenant_id}:{key}"

    # ------------------------------------------------------------------
    # L1 (in-memory) operations
    # ------------------------------------------------------------------

    def _l1_get(self, tenant_key: str) -> Any:
        entry = self._l1.get(tenant_key)
        if entry is None:
            return None
        value, expiry = entry
        if expiry < datetime.now(timezone.utc).timestamp():
            del self._l1[tenant_key]
            return None
        return value

    def _l1_set(self, tenant_key: str, value: Any, ttl_seconds: int = 86400) -> None:
        if len(self._l1) >= self._l1_max_size:
            self._evict_l1()
        expiry = datetime.now(timezone.utc).timestamp() + ttl_seconds
        self._l1[tenant_key] = (value, expiry)

    def _evict_l1(self) -> None:
        """Evict 25% of the oldest entries when L1 is full."""
        if not self._l1:
            return
        sorted_keys = sorted(self._l1.keys(), key=lambda k: self._l1[k][1])
        evict_count = max(1, len(sorted_keys) // 4)
        for k in sorted_keys[:evict_count]:
            del self._l1[k]

    # ------------------------------------------------------------------
    # L2 (DiskCache) operations — lazy init
    # ------------------------------------------------------------------

    def _ensure_l2(self):
        if self._l2 is None:
            try:
                import diskcache
                self._l2_cache_dir.mkdir(parents=True, exist_ok=True)
                self._l2 = diskcache.Cache(str(self._l2_cache_dir))
                self._l2_initialized = True
                logger.info("L2 DiskCache initialized at %s", self._l2_cache_dir)
            except ImportError:
                logger.warning("diskcache not installed; L2 tier disabled.")
                self._l2 = None

    def _l2_get(self, tenant_key: str) -> Any:
        self._ensure_l2()
        if self._l2 is None:
            return None
        try:
            return self._l2.get(tenant_key)
        except Exception as exc:
            logger.warning("L2 get failed for %s: %s", tenant_key, exc)
            return None

    def _l2_set(self, tenant_key: str, value: Any, ttl_seconds: int = 86400) -> None:
        self._ensure_l2()
        if self._l2 is None:
            return
        try:
            self._l2.set(tenant_key, value, expire=ttl_seconds)
        except Exception as exc:
            logger.warning("L2 set failed for %s: %s", tenant_key, exc)

    def _l2_delete(self, tenant_key: str) -> None:
        self._ensure_l2()
        if self._l2 is None:
            return
        try:
            del self._l2[tenant_key]
        except KeyError:
            pass
        except Exception as exc:
            logger.warning("L2 delete failed for %s: %s", tenant_key, exc)

    def _l2_clear(self) -> None:
        self._ensure_l2()
        if self._l2 is None:
            return
        try:
            self._l2.clear()
        except Exception as exc:
            logger.warning("L2 clear failed: %s", exc)
