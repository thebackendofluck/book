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
Stream Manager for Live Casino

Manages WebRTC and HLS streaming connections:
- WebRTC with adaptive bitrate
- HLS fallback for compatibility
- Connection quality monitoring
- Automatic protocol switching
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class StreamProtocol(Enum):
    """Supported streaming protocols."""

    WEBRTC = "webrtc"
    HLS = "hls"
    LL_HLS = "ll_hls"  # Low-latency HLS
    WEBSOCKET_MSE = "websocket_mse"


class StreamQuality(Enum):
    """Stream quality levels."""

    AUTO = "auto"
    HIGH = "1080p"
    MEDIUM = "720p"
    LOW = "480p"
    MOBILE = "360p"


@dataclass
class StreamConfig:
    """Configuration for a streaming connection."""

    table_id: str
    protocol: StreamProtocol
    quality: StreamQuality
    max_latency_ms: int = 500
    audio_enabled: bool = True
    auto_reconnect: bool = True
    reconnect_attempts: int = 3


@dataclass
class StreamMetrics:
    """Metrics for stream quality monitoring."""

    latency_ms: float = 0.0
    bitrate_kbps: float = 0.0
    packet_loss: float = 0.0
    frame_rate: float = 0.0
    resolution: str = "unknown"
    buffer_health: float = 1.0
    quality_score: float = 100.0
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class StreamConnection:
    """Represents an active stream connection."""

    connection_id: str
    table_id: str
    player_id: str
    protocol: StreamProtocol
    stream_url: str
    connected_at: datetime
    metrics: StreamMetrics
    status: str = "connected"


class StreamManager:
    """
    Manages WebRTC and HLS streaming connections for live casino.

    Provides:
    - Connection management with failover
    - Quality monitoring and adaptive bitrate
    - Automatic protocol switching
    - Connection pooling

    Example:
        >>> manager = StreamManager(redis_client)
        >>> config = StreamConfig(
        ...     table_id="table_123",
        ...     protocol=StreamProtocol.WEBRTC,
        ...     quality=StreamQuality.AUTO
        ... )
        >>> connection = await manager.connect(player_id, config)
    """

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client
        self.logger = logging.getLogger(__name__)
        self.connections: Dict[str, StreamConnection] = {}
        self.metrics_history: Dict[str, List[StreamMetrics]] = {}

        # Quality thresholds
        self.quality_thresholds = {
            "latency_warning": 300,  # ms
            "latency_critical": 500,  # ms
            "packet_loss_warning": 0.02,  # 2%
            "packet_loss_critical": 0.05,  # 5%
            "bitrate_min": 1000,  # kbps for 720p
        }

    async def connect(
        self, player_id: str, config: StreamConfig
    ) -> Optional[StreamConnection]:
        """
        Establish a new streaming connection.

        Args:
            player_id: Unique player identifier
            config: Stream configuration

        Returns:
            StreamConnection if successful, None otherwise
        """
        connection_id = f"conn_{player_id}_{config.table_id}_{int(time.time())}"

        try:
            # Get stream URL based on protocol
            stream_url = await self._get_stream_url(config)
            if not stream_url:
                self.logger.error(f"Failed to get stream URL for {config.table_id}")
                return None

            # Create connection
            connection = StreamConnection(
                connection_id=connection_id,
                table_id=config.table_id,
                player_id=player_id,
                protocol=config.protocol,
                stream_url=stream_url,
                connected_at=datetime.now(timezone.utc),
                metrics=StreamMetrics(),
                status="connecting",
            )

            # Store connection
            self.connections[connection_id] = connection

            # Initialize connection based on protocol
            if config.protocol == StreamProtocol.WEBRTC:
                success = await self._init_webrtc_connection(connection, config)
            elif config.protocol in [StreamProtocol.HLS, StreamProtocol.LL_HLS]:
                success = await self._init_hls_connection(connection, config)
            else:
                success = await self._init_websocket_connection(connection, config)

            if success:
                connection.status = "connected"
                await self._store_connection_state(connection)
                asyncio.create_task(self._monitor_connection(connection_id))
                return connection
            else:
                del self.connections[connection_id]
                return None

        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            if connection_id in self.connections:
                del self.connections[connection_id]
            return None

    async def disconnect(self, connection_id: str) -> bool:
        """Disconnect a streaming connection."""
        connection = self.connections.get(connection_id)
        if not connection:
            return False

        try:
            # Cleanup based on protocol
            if connection.protocol == StreamProtocol.WEBRTC:
                await self._cleanup_webrtc(connection)
            elif connection.protocol in [StreamProtocol.HLS, StreamProtocol.LL_HLS]:
                await self._cleanup_hls(connection)

            connection.status = "disconnected"
            await self._remove_connection_state(connection)
            del self.connections[connection_id]

            return True

        except Exception as e:
            self.logger.error(f"Disconnect error: {e}")
            return False

    async def get_metrics(self, connection_id: str) -> Optional[StreamMetrics]:
        """Get current metrics for a connection."""
        connection = self.connections.get(connection_id)
        if connection:
            return connection.metrics
        return None

    async def switch_quality(
        self, connection_id: str, quality: StreamQuality
    ) -> bool:
        """Switch stream quality."""
        connection = self.connections.get(connection_id)
        if not connection:
            return False

        try:
            # Update quality based on protocol
            if connection.protocol == StreamProtocol.WEBRTC:
                await self._switch_webrtc_quality(connection, quality)
            elif connection.protocol in [StreamProtocol.HLS, StreamProtocol.LL_HLS]:
                await self._switch_hls_quality(connection, quality)

            self.logger.info(f"Switched {connection_id} to {quality.value}")
            return True

        except Exception as e:
            self.logger.error(f"Quality switch failed: {e}")
            return False

    async def switch_protocol(
        self, connection_id: str, new_protocol: StreamProtocol
    ) -> Optional[StreamConnection]:
        """Switch streaming protocol (e.g., WebRTC to HLS)."""
        connection = self.connections.get(connection_id)
        if not connection:
            return None

        try:
            # Store old connection info
            player_id = connection.player_id
            table_id = connection.table_id

            # Disconnect old connection
            await self.disconnect(connection_id)

            # Create new connection with new protocol
            new_config = StreamConfig(
                table_id=table_id,
                protocol=new_protocol,
                quality=StreamQuality.AUTO,
            )

            return await self.connect(player_id, new_config)

        except Exception as e:
            self.logger.error(f"Protocol switch failed: {e}")
            return None

    async def _monitor_connection(self, connection_id: str) -> None:
        """Monitor connection quality continuously."""
        while connection_id in self.connections:
            connection = self.connections.get(connection_id)
            if not connection or connection.status != "connected":
                break

            try:
                # Collect metrics
                metrics = await self._collect_metrics(connection)
                connection.metrics = metrics

                # Store in history
                if connection_id not in self.metrics_history:
                    self.metrics_history[connection_id] = []
                self.metrics_history[connection_id].append(metrics)

                # Keep only last 60 samples (1 minute at 1/sec)
                if len(self.metrics_history[connection_id]) > 60:
                    self.metrics_history[connection_id] = self.metrics_history[
                        connection_id
                    ][-60:]

                # Check for quality issues
                await self._check_quality_issues(connection, metrics)

            except Exception as e:
                self.logger.warning(f"Metrics collection error: {e}")

            await asyncio.sleep(1)

    async def _check_quality_issues(
        self, connection: StreamConnection, metrics: StreamMetrics
    ) -> None:
        """Check for quality issues and take action."""
        thresholds = self.quality_thresholds

        # Check latency
        if metrics.latency_ms > thresholds["latency_critical"]:
            self.logger.warning(
                f"Critical latency for {connection.connection_id}: "
                f"{metrics.latency_ms}ms"
            )
            # Consider switching protocol
            if connection.protocol == StreamProtocol.WEBRTC:
                await self._consider_protocol_switch(connection)

        elif metrics.latency_ms > thresholds["latency_warning"]:
            self.logger.info(
                f"High latency for {connection.connection_id}: {metrics.latency_ms}ms"
            )

        # Check packet loss
        if metrics.packet_loss > thresholds["packet_loss_critical"]:
            self.logger.warning(
                f"Critical packet loss: {metrics.packet_loss * 100:.1f}%"
            )

    async def _consider_protocol_switch(
        self, connection: StreamConnection
    ) -> None:
        """Consider switching protocol due to quality issues."""
        # Check if issues persist
        history = self.metrics_history.get(connection.connection_id, [])
        if len(history) < 5:
            return

        recent = history[-5:]
        avg_latency = sum(m.latency_ms for m in recent) / len(recent)

        if avg_latency > self.quality_thresholds["latency_critical"]:
            self.logger.info(f"Switching {connection.connection_id} to HLS")
            # Would trigger protocol switch in production

    async def _get_stream_url(self, config: StreamConfig) -> Optional[str]:
        """Get stream URL from provider."""
        # In production, would fetch from studio provider
        return f"https://stream.example.com/{config.table_id}/{config.protocol.value}"

    async def _init_webrtc_connection(
        self, connection: StreamConnection, config: StreamConfig
    ) -> bool:
        """Initialize WebRTC connection."""
        # In production, would set up WebRTC peer connection
        return True

    async def _init_hls_connection(
        self, connection: StreamConnection, config: StreamConfig
    ) -> bool:
        """Initialize HLS connection."""
        # In production, would set up HLS player
        return True

    async def _init_websocket_connection(
        self, connection: StreamConnection, config: StreamConfig
    ) -> bool:
        """Initialize WebSocket+MSE connection."""
        # In production, would set up WebSocket connection
        return True

    async def _cleanup_webrtc(self, connection: StreamConnection) -> None:
        """Cleanup WebRTC resources."""
        pass

    async def _cleanup_hls(self, connection: StreamConnection) -> None:
        """Cleanup HLS resources."""
        pass

    async def _switch_webrtc_quality(
        self, connection: StreamConnection, quality: StreamQuality
    ) -> None:
        """Switch WebRTC quality level."""
        pass

    async def _switch_hls_quality(
        self, connection: StreamConnection, quality: StreamQuality
    ) -> None:
        """Switch HLS quality level."""
        pass

    async def _collect_metrics(
        self, connection: StreamConnection
    ) -> StreamMetrics:
        """Collect current metrics for connection."""
        # In production, would collect real metrics
        return StreamMetrics(
            latency_ms=150.0,
            bitrate_kbps=3500.0,
            packet_loss=0.001,
            frame_rate=30.0,
            resolution="1080p",
            buffer_health=0.95,
            quality_score=95.0,
        )

    async def _store_connection_state(
        self, connection: StreamConnection
    ) -> None:
        """Store connection state in Redis."""
        key = f"stream_connection:{connection.connection_id}"
        await self.redis.hset(
            key,
            mapping={
                "table_id": connection.table_id,
                "player_id": connection.player_id,
                "protocol": connection.protocol.value,
                "status": connection.status,
            },
        )
        await self.redis.expire(key, 3600)  # 1 hour TTL

    async def _remove_connection_state(
        self, connection: StreamConnection
    ) -> None:
        """Remove connection state from Redis."""
        key = f"stream_connection:{connection.connection_id}"
        await self.redis.delete(key)

    def get_active_connections(self) -> List[Dict[str, Any]]:
        """Get list of all active connections."""
        return [
            {
                "connection_id": c.connection_id,
                "table_id": c.table_id,
                "player_id": c.player_id,
                "protocol": c.protocol.value,
                "status": c.status,
                "latency_ms": c.metrics.latency_ms,
            }
            for c in self.connections.values()
        ]
