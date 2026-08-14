import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta, timezone

from renovai.safety.cache import RenovAICache

logger = logging.getLogger(__name__)

# Default TTL for cached advisory reports (7 days).
_DEFAULT_REPORT_TTL_SECONDS = 7 * 24 * 3600


class ApartmentProfileHasher:
    """Profile-based caching for advisory reports and ML estimates.

    Generates a deterministic SHA-256 hash from the apartment's profile
    parameters: district, building_era, area_sqm (rounded), scope_flags
    (sorted). If a matching request arrives within the TTL (7 days),
    the cached report is served to minimize LLM costs and latency.

    Tenant isolation: every hash is prefixed with the tenant_id so cached
    data from one session never leaks into another user's context.

    Usage:
        hasher = ApartmentProfileHasher(tenant_id="user_abc", cache=cache)
        profile = {
            "district": 7,
            "building_era": 1960,
            "area_sqm": 55.0,
            "scope_flags": {"plumbing": True, "electrical": True},
        }
        existing = await hasher.get_cached_report(profile)
        if existing is None:
            report = await generate_report(profile)
            await hasher.cache_report(profile, report)
    """

    def __init__(
        self,
        tenant_id: str = "default",
        cache: RenovAICache | None = None,
        report_ttl_seconds: int = _DEFAULT_REPORT_TTL_SECONDS,
    ):
        self._tenant_id = tenant_id
        self._cache = cache or RenovAICache(tenant_id=tenant_id)
        self._report_ttl = report_ttl_seconds

    def build_hash_key(self, profile: dict[str, Any]) -> str:
        """Generate a deterministic hash key from an apartment profile.

        The hash is based on:
        - district (int)
        - building_era (int or null)
        - area_sqm rounded to nearest 5 (for fuzzy matching)
        - scope_flags sorted by key (for deterministic ordering)

        This ensures that two requests for the same apartment with minor
        area differences (e.g. 54 vs 55 sqm) resolve to the same cache key.
        """
        district = profile.get("district", 0)
        building_era = profile.get("building_era", "unknown")
        area_sqm = profile.get("area_sqm", 0.0)
        area_bucket = int(round(area_sqm / 5.0)) * 5  # Round to nearest 5
        scope = profile.get("scope_flags", {})

        # Sort scope flags for deterministic ordering
        sorted_scope = dict(sorted(scope.items()))

        canonical = json.dumps(
            {
                "district": district,
                "building_era": building_era,
                "area_bucket": area_bucket,
                "scope_flags": sorted_scope,
            },
            sort_keys=True,
            ensure_ascii=False,
        )

        raw_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"profile:{raw_hash}"

    async def get_cached_report(self, profile: dict[str, Any]) -> dict[str, Any] | None:
        """Retrieve a cached report for the given apartment profile.

        Returns None if no cached report exists or the cached entry has expired.
        """
        cache_key = self.build_hash_key(profile)
        cached = await self._cache.get(cache_key)
        if cached is not None:
            logger.info(
                "[tenant=%s] Profile cache HIT: %s",
                self._tenant_id, cache_key,
            )
        else:
            logger.info(
                "[tenant=%s] Profile cache MISS: %s",
                self._tenant_id, cache_key,
            )
        return cached

    async def cache_report(self, profile: dict[str, Any], report: dict[str, Any]) -> None:
        """Cache a report for the given apartment profile."""
        cache_key = self.build_hash_key(profile)
        await self._cache.set(cache_key, report, ttl_seconds=self._report_ttl)
        logger.info(
            "[tenant=%s] Profile cache SET: %s (TTL=%ds)",
            self._tenant_id, cache_key, self._report_ttl,
        )

    async def invalidate(self, profile: dict[str, Any]) -> None:
        """Remove a cached report for the given profile."""
        cache_key = self.build_hash_key(profile)
        await self._cache.invalidate(cache_key)
        logger.info(
            "[tenant=%s] Profile cache INVALIDATED: %s",
            self._tenant_id, cache_key,
        )
