# Companion code for "The Backend of Luck" - Chapter 13, Live Casino Streaming Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Multi-Provider Studio Integration Manager

Provides failover and load balancing across multiple live casino providers:
- Evolution Gaming (priority 1)
- Pragmatic Play Live (priority 2)
- Ezugi (priority 3)

Features:
- Automatic failover on provider unavailability
- Jurisdiction-based routing
- Latency-optimized table selection
- Game type filtering
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp


@dataclass
class StudioConfig:
    """Configuration for a live casino studio provider."""

    provider: str
    api_endpoint: str
    auth_token: str
    max_concurrent_tables: int
    failover_priority: int


@dataclass
class TableInfo:
    """Information about an available live casino table."""

    studio: str
    table_id: str
    stream_url: str
    dealer_info: Dict[str, Any]
    latency_ms: int
    game_type: str
    jurisdiction: str


class StudioIntegrationManager:
    """
    Multi-provider studio integration manager with failover support.

    Manages connections to multiple live casino providers and handles
    automatic failover when a provider becomes unavailable.

    Example:
        >>> manager = StudioIntegrationManager(redis_client)
        >>> table = await manager.get_available_table("blackjack", "UK")
        >>> if table:
        ...     print(f"Table: {table.table_id} from {table.studio}")
    """

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client
        self.logger = logging.getLogger(__name__)

        # Studio configurations
        self.studios: Dict[str, StudioConfig] = {
            "evolution": StudioConfig(
                provider="evolution",
                api_endpoint="https://api.evolutiongaming.com/v2",
                auth_token="ev_token_placeholder",
                max_concurrent_tables=500,
                failover_priority=1,
            ),
            "pragmatic": StudioConfig(
                provider="pragmatic",
                api_endpoint="https://api.pragmaticplaylive.com/v1",
                auth_token="pp_token_placeholder",
                max_concurrent_tables=300,
                failover_priority=2,
            ),
            "ezugi": StudioConfig(
                provider="ezugi",
                api_endpoint="https://api.ezugi.com/v3",
                auth_token="ez_token_placeholder",
                max_concurrent_tables=200,
                failover_priority=3,
            ),
        }

        # Provider health status
        self.provider_health: Dict[str, bool] = {
            provider: True for provider in self.studios
        }

    async def get_available_table(
        self, game_type: str, jurisdiction: str
    ) -> Optional[TableInfo]:
        """
        Get available table with automatic failover support.

        Args:
            game_type: Type of game (blackjack, roulette, baccarat, etc.)
            jurisdiction: Player's jurisdiction (UK, Malta, NJ, etc.)

        Returns:
            TableInfo if available, None if no tables available
        """
        sorted_studios = sorted(
            self.studios.values(), key=lambda x: x.failover_priority
        )

        for studio in sorted_studios:
            if not self.provider_health.get(studio.provider, False):
                self.logger.debug(f"Skipping unhealthy provider: {studio.provider}")
                continue

            try:
                table = await self._check_studio_availability(
                    studio, game_type, jurisdiction
                )
                if table:
                    return table
            except Exception as e:
                self.logger.warning(f"Studio {studio.provider} unavailable: {e}")
                await self._mark_provider_unhealthy(studio.provider)
                continue

        return None

    async def _check_studio_availability(
        self, studio: StudioConfig, game_type: str, jurisdiction: str
    ) -> Optional[TableInfo]:
        """Check individual studio for available tables."""
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {studio.auth_token}",
                "Content-Type": "application/json",
            }

            params = {
                "game_type": game_type,
                "jurisdiction": jurisdiction,
                "max_latency_ms": 100,
            }

            try:
                async with session.get(
                    f"{studio.api_endpoint}/tables/available",
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        tables = data.get("tables", [])
                        if tables:
                            table = tables[0]
                            return TableInfo(
                                studio=studio.provider,
                                table_id=table.get("id", ""),
                                stream_url=table.get("stream_url", ""),
                                dealer_info=table.get("dealer_info", {}),
                                latency_ms=table.get("latency", 0),
                                game_type=game_type,
                                jurisdiction=jurisdiction,
                            )
                    return None
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout checking {studio.provider}")
                return None

    async def get_all_available_tables(
        self, game_type: str, jurisdiction: str
    ) -> List[TableInfo]:
        """Get all available tables across all providers."""
        tables: List[TableInfo] = []

        tasks = [
            self._check_studio_availability(studio, game_type, jurisdiction)
            for studio in self.studios.values()
            if self.provider_health.get(studio.provider, False)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, TableInfo):
                tables.append(result)
            elif isinstance(result, Exception):
                self.logger.warning(f"Error fetching tables: {result}")

        return sorted(tables, key=lambda t: t.latency_ms)

    async def _mark_provider_unhealthy(self, provider: str) -> None:
        """Mark provider as unhealthy and schedule recovery check."""
        self.provider_health[provider] = False

        # Store in Redis for distributed state
        await self.redis.setex(
            f"provider_health:{provider}",
            300,  # 5 minutes
            "unhealthy",
        )

        # Schedule recovery check
        asyncio.create_task(self._check_provider_recovery(provider))

    async def _check_provider_recovery(self, provider: str) -> None:
        """Check if provider has recovered after cooldown period."""
        await asyncio.sleep(60)  # Wait 60 seconds before retry

        studio = self.studios.get(provider)
        if not studio:
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{studio.api_endpoint}/health",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    if response.status == 200:
                        self.provider_health[provider] = True
                        await self.redis.delete(f"provider_health:{provider}")
                        self.logger.info(f"Provider {provider} recovered")
        except Exception as e:
            self.logger.warning(f"Provider {provider} still unhealthy: {e}")
            # Reschedule check
            asyncio.create_task(self._check_provider_recovery(provider))

    async def get_table_by_id(
        self, provider: str, table_id: str
    ) -> Optional[TableInfo]:
        """Get specific table by provider and ID."""
        studio = self.studios.get(provider)
        if not studio:
            return None

        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {studio.auth_token}",
                "Content-Type": "application/json",
            }

            try:
                async with session.get(
                    f"{studio.api_endpoint}/tables/{table_id}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    if response.status == 200:
                        table = await response.json()
                        return TableInfo(
                            studio=provider,
                            table_id=table.get("id", ""),
                            stream_url=table.get("stream_url", ""),
                            dealer_info=table.get("dealer_info", {}),
                            latency_ms=table.get("latency", 0),
                            game_type=table.get("game_type", ""),
                            jurisdiction=table.get("jurisdiction", ""),
                        )
                    return None
            except Exception as e:
                self.logger.error(f"Error fetching table {table_id}: {e}")
                return None

    def get_provider_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all providers."""
        status: Dict[str, Dict[str, Any]] = {}

        for provider, studio in self.studios.items():
            status[provider] = {
                "healthy": self.provider_health.get(provider, False),
                "max_tables": studio.max_concurrent_tables,
                "priority": studio.failover_priority,
                "endpoint": studio.api_endpoint,
            }

        return status
