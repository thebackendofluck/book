# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
High-Performance Redis Caching for RTC Operations

This module provides a Redis-based caching layer optimized for
Real-Time Clock operations in online gambling platforms.

Features:
- Timestamp caching with TTL management
- Consensus state storage with atomic updates
- Drift history tracking using circular buffers
- Distributed locking for consensus operations
- Pub/Sub for anomaly notifications
- Health report caching and time-series storage

Performance Characteristics:
- Binary serialization with msgpack for minimal overhead
- Connection pooling for high concurrency
- Pipeline batching for burst operations
- Lua scripting for atomic operations
"""

import hashlib
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, cast

# External dependencies (install with: pip install redis msgpack)
try:
    import redis
    import msgpack
except ImportError:
    redis = None  # type: ignore
    msgpack = None  # type: ignore


class RTCCache:
    """
    High-performance Redis cache for RTC operations.

    This class provides caching capabilities specifically designed
    for Real-Time Clock systems in iGaming environments where
    microsecond-level timing accuracy is critical.

    Example:
        ```python
        config = {
            'host': 'localhost',
            'port': 6379,
            'password': 'secret',
            'db': 0
        }
        cache = RTCCache(config)

        # Cache a timestamp
        await cache.set_timestamp('req-123', {
            'unix': 1638360000,
            'nano': 123456789,
            'signature': 'abc123...'
        })

        # Get cached timestamp
        ts = await cache.get_timestamp('req-123')
        ```
    """

    def __init__(self, redis_config: Dict[str, Any]):
        """
        Initialize RTC Cache with Redis configuration.

        Args:
            redis_config: Dictionary containing:
                - host: Redis server hostname
                - port: Redis server port
                - db: Redis database number (default: 0)
                - password: Optional authentication password
        """
        if redis is None:
            raise ImportError("redis package required: pip install redis")
        if msgpack is None:
            raise ImportError("msgpack package required: pip install msgpack")

        self.redis_client = redis.Redis(
            host=redis_config["host"],
            port=redis_config["port"],
            db=redis_config.get("db", 0),
            password=redis_config.get("password"),
            decode_responses=False,  # Binary mode for performance
            socket_keepalive=True,
            socket_keepalive_options={
                1: 1,  # TCP_KEEPIDLE
                2: 1,  # TCP_KEEPINTVL
                3: 3,  # TCP_KEEPCNT
            },
        )

        # Create connection pool for high concurrency
        self.pool = redis.ConnectionPool(
            host=redis_config["host"],
            port=redis_config["port"],
            max_connections=100,
            socket_keepalive=True,
        )

        # Pipeline for batch operations
        self.pipeline = self.redis_client.pipeline(transaction=False)

    def _generate_key(self, prefix: str, identifier: str) -> str:
        """
        Generate cache key with namespace.

        Args:
            prefix: Key prefix (e.g., 'timestamp', 'drift_history')
            identifier: Unique identifier for the cached item

        Returns:
            Namespaced cache key
        """
        return f"rtc:{prefix}:{identifier}"

    def _serialize(self, data: Any) -> bytes:
        """
        Serialize data using msgpack for performance.

        msgpack provides 2-3x faster serialization than JSON
        with smaller payload sizes.

        Args:
            data: Data to serialize

        Returns:
            Serialized bytes
        """
        return msgpack.packb(data, use_bin_type=True)

    def _deserialize(self, data: bytes) -> Any:
        """
        Deserialize msgpack data.

        Args:
            data: Serialized bytes

        Returns:
            Deserialized data
        """
        return msgpack.unpackb(data, raw=False)

    async def get_timestamp(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached timestamp by request ID.

        Args:
            request_id: Unique request identifier

        Returns:
            Cached timestamp dictionary or None if not found
        """
        key = self._generate_key("timestamp", request_id)
        data = self.redis_client.get(key)

        if data:
            return self._deserialize(cast(bytes, data))
        return None

    async def set_timestamp(
        self, request_id: str, timestamp: Dict[str, Any], ttl: int = 60
    ) -> None:
        """
        Cache timestamp with TTL.

        Args:
            request_id: Unique request identifier
            timestamp: Timestamp data dictionary
            ttl: Time-to-live in seconds (default: 60)
        """
        key = self._generate_key("timestamp", request_id)
        data = self._serialize(timestamp)
        self.redis_client.setex(key, ttl, data)

    async def get_consensus_state(self) -> Optional[Dict[str, Any]]:
        """
        Get current consensus state from cache.

        The consensus state contains information about the current
        agreement between multiple RTC modules.

        Returns:
            Consensus state dictionary or None
        """
        key = "rtc:consensus:state"
        data = self.redis_client.get(key)

        if data:
            return self._deserialize(cast(bytes, data))
        return None

    async def update_consensus_state(self, state: Dict[str, Any]) -> None:
        """
        Update consensus state with atomic operation.

        Uses Lua script to ensure atomicity of the update
        operation, preventing race conditions in distributed
        consensus scenarios.

        Args:
            state: New consensus state dictionary
        """
        key = "rtc:consensus:state"
        data = self._serialize(state)

        # Use Lua script for atomic update
        lua_script = """
        local key = KEYS[1]
        local new_data = ARGV[1]
        local ttl = ARGV[2]

        redis.call('SET', key, new_data)
        redis.call('EXPIRE', key, ttl)

        return redis.call('GET', key)
        """

        # Pass keys and args as separate lists for Redis eval
        self.redis_client.eval(lua_script, 1, key, data, 300)

    async def add_to_drift_history(self, rtc_id: str, drift: float) -> None:
        """
        Add drift value to circular buffer.

        Maintains a rolling history of drift measurements for
        trend analysis and anomaly detection.

        Args:
            rtc_id: RTC module identifier
            drift: Drift value in milliseconds
        """
        key = self._generate_key("drift_history", rtc_id)

        # Use Redis list as circular buffer
        self.pipeline.lpush(key, self._serialize(drift))
        self.pipeline.ltrim(key, 0, 999)  # Keep last 1000 values
        self.pipeline.expire(key, 86400)  # 24 hour TTL
        self.pipeline.execute()

    async def get_drift_history(self, rtc_id: str, count: int = 100) -> List[float]:
        """
        Get drift history from cache.

        Args:
            rtc_id: RTC module identifier
            count: Number of historical values to retrieve

        Returns:
            List of drift values (most recent first)
        """
        key = self._generate_key("drift_history", rtc_id)
        data = cast(List[bytes], self.redis_client.lrange(key, 0, count - 1))

        return [self._deserialize(d) for d in data]

    async def acquire_lock(self, resource: str, timeout: int = 10) -> bool:
        """
        Acquire distributed lock for consensus operations.

        Uses Redis SET with NX (not exists) and EX (expire)
        to implement distributed locking.

        Args:
            resource: Resource name to lock
            timeout: Lock timeout in seconds

        Returns:
            True if lock acquired, False otherwise
        """
        key = self._generate_key("lock", resource)
        identifier = hashlib.sha256(f"{resource}{datetime.now()}".encode()).hexdigest()

        # Try to acquire lock with NX (not exists) and EX (expire)
        acquired = self.redis_client.set(key, identifier, nx=True, ex=timeout)

        return bool(acquired)

    async def release_lock(self, resource: str) -> None:
        """
        Release distributed lock.

        Args:
            resource: Resource name to unlock
        """
        key = self._generate_key("lock", resource)
        self.redis_client.delete(key)

    async def publish_anomaly(self, anomaly: Dict[str, Any]) -> None:
        """
        Publish anomaly to Redis pub/sub channel.

        Used for real-time alerting when RTC anomalies
        are detected (drift, consensus failures, etc.).

        Args:
            anomaly: Anomaly data dictionary
        """
        channel = "rtc:anomalies"
        data = self._serialize(anomaly)
        self.redis_client.publish(channel, data)

    async def subscribe_anomalies(self, callback: Callable) -> None:
        """
        Subscribe to anomaly notifications.

        Args:
            callback: Async function to call when anomaly is received
        """
        pubsub = self.redis_client.pubsub()
        pubsub.subscribe("rtc:anomalies")

        for message in pubsub.listen():
            if message["type"] == "message":
                anomaly = self._deserialize(message["data"])
                await callback(anomaly)

    async def cache_health_report(self, report: Dict[str, Any]) -> None:
        """
        Cache health report with automatic expiration.

        Stores both the latest report and a time-series history
        for trend analysis.

        Args:
            report: Health report dictionary
        """
        key = "rtc:health:latest"
        data = self._serialize(report)
        self.redis_client.setex(key, 3600, data)  # 1 hour TTL

        # Also store in time series
        ts_key = f"rtc:health:history:{datetime.now().strftime('%Y%m%d')}"
        score = datetime.now().timestamp()
        self.redis_client.zadd(ts_key, {data: score})
        self.redis_client.expire(ts_key, 2592000)  # 30 days TTL

    async def get_latest_health_report(self) -> Optional[Dict[str, Any]]:
        """
        Get latest health report from cache.

        Returns:
            Latest health report dictionary or None
        """
        key = "rtc:health:latest"
        data = self.redis_client.get(key)

        if data:
            return self._deserialize(cast(bytes, data))
        return None

    async def increment_counter(self, metric: str, value: int = 1) -> None:
        """
        Increment metric counter.

        Args:
            metric: Metric name
            value: Increment value (default: 1)
        """
        key = self._generate_key("metric", metric)
        self.redis_client.incrby(key, value)

    async def get_counter(self, metric: str) -> int:
        """
        Get metric counter value.

        Args:
            metric: Metric name

        Returns:
            Counter value
        """
        key = self._generate_key("metric", metric)
        value = self.redis_client.get(key)
        return int(cast(bytes, value)) if value else 0
