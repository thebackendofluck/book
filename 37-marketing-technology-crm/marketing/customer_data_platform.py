# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Customer Data Platform (CDP) for iGaming
=========================================
Chapter 9: Marketing Technology and CRM Systems

Real-time Customer Data Platform implementation providing:
- Event tracking and streaming via Kafka
- Customer profile management with PostgreSQL
- Identity resolution across devices and sessions
- Real-time customer segmentation
- GDPR-compliant data export

Dependencies:
    pip install aiokafka redis asyncpg
"""

# Real-time Customer Data Platform implementation
import asyncio
import aiokafka  # ty:ignore[unresolved-import]
import redis.asyncio as redis
import asyncpg  # ty:ignore[unresolved-import]
from typing import Dict, List, Optional, Any
import json
import uuid
from datetime import datetime, timedelta
import hashlib
from dataclasses import dataclass, asdict
import logging

@dataclass
class CustomerEvent:
    event_id: str
    customer_id: str
    event_type: str
    properties: Dict[str, Any]
    timestamp: datetime
    source: str
    session_id: str
    device_id: str
    ip_address: str
    user_agent: str

@dataclass
class CustomerProfile:
    customer_id: str
    email: str
    first_name: str
    last_name: str
    date_of_birth: Optional[datetime]
    country: str
    registration_date: datetime
    total_deposits: float
    total_withdrawals: float
    lifetime_value: float
    last_activity: datetime
    segments: List[str]
    preferences: Dict[str, Any]
    consent_status: Dict[str, bool]
    risk_profile: str
    vip_level: int

class CustomerDataPlatform:
    def __init__(self, config: Dict[str, str]):
        self.config = config
        self.redis_client: Optional[redis.Redis] = None
        self.db_pool: Optional[asyncpg.Pool] = None
        self.kafka_producer: Optional[aiokafka.AIOKafkaProducer] = None
        self.kafka_consumer: Optional[aiokafka.AIOKafkaConsumer] = None
        self.logger = logging.getLogger(__name__)

    async def initialize(self):
        """Initialize all connections and components"""
        # Redis for real-time caching
        self.redis_client = redis.from_url(
            self.config['redis_url'],
            decode_responses=True
        )

        # PostgreSQL for customer profiles
        self.db_pool = await asyncpg.create_pool(
            self.config['database_url'],
            min_size=10,
            max_size=20
        )

        # Kafka for event streaming
        self.kafka_producer = aiokafka.AIOKafkaProducer(
            bootstrap_servers=self.config['kafka_brokers'],
            value_serializer=lambda v: json.dumps(v, default=str).encode()
        )
        await self.kafka_producer.start()

        # Initialize database schema
        await self._initialize_database()

        self.logger.info("Customer Data Platform initialized successfully")

    async def _initialize_database(self):
        """Create database tables if they don't exist"""
        async with self.db_pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS customer_profiles (
                    customer_id VARCHAR(50) PRIMARY KEY,
                    email VARCHAR(255) UNIQUE,
                    first_name VARCHAR(100),
                    last_name VARCHAR(100),
                    date_of_birth TIMESTAMP,
                    country VARCHAR(2),
                    registration_date TIMESTAMP,
                    total_deposits DECIMAL(15,2) DEFAULT 0,
                    total_withdrawals DECIMAL(15,2) DEFAULT 0,
                    lifetime_value DECIMAL(15,2) DEFAULT 0,
                    last_activity TIMESTAMP,
                    segments JSONB,
                    preferences JSONB,
                    consent_status JSONB,
                    risk_profile VARCHAR(20),
                    vip_level INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS customer_events (
                    event_id VARCHAR(50) PRIMARY KEY,
                    customer_id VARCHAR(50),
                    event_type VARCHAR(50),
                    properties JSONB,
                    timestamp TIMESTAMP,
                    source VARCHAR(50),
                    session_id VARCHAR(100),
                    device_id VARCHAR(100),
                    ip_address INET,
                    user_agent TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_events_customer_id ON customer_events (customer_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_events_timestamp ON customer_events (timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_events_type ON customer_events (event_type)")

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS identity_graph (
                    customer_id VARCHAR(50),
                    identifier_type VARCHAR(50),
                    identifier_value VARCHAR(255),
                    confidence_score DECIMAL(3,2),
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    PRIMARY KEY (customer_id, identifier_type, identifier_value)
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_identity_graph_identifier ON identity_graph (identifier_type, identifier_value)")

    async def track_event(self, event: CustomerEvent) -> bool:
        """Track a customer event and update profile in real-time"""
        try:
            # Validate event
            if not self._validate_event(event):
                return False

            # Store raw event
            await self._store_event(event)

            # Update customer profile
            await self._update_customer_profile(event)

            # Perform identity resolution if needed
            await self._perform_identity_resolution(event)

            # Real-time segmentation
            await self._update_customer_segments(event)  # ty:ignore[unresolved-attribute]

            # Publish to Kafka for downstream consumers
            await self.kafka_producer.send_and_wait(  # ty:ignore[unresolved-attribute]
                'customer-events',
                asdict(event)
            )

            # Cache recent activity
            await self._cache_recent_activity(event)  # ty:ignore[unresolved-attribute]

            return True

        except Exception as e:
            self.logger.error(f"Failed to track event {event.event_id}: {e}")
            return False

    def _validate_event(self, event: CustomerEvent) -> bool:
        """Validate event data"""
        if not event.customer_id or not event.event_type:
            return False

        # Validate against known event types
        valid_events = {
            'registration', 'login', 'deposit', 'withdrawal', 'bet_placed',
            'bet_settled', 'game_started', 'game_ended', 'bonus_claimed',
            'deposit_failed', 'withdrawal_failed', 'self_exclusion',
            'deposit_limit_set', 'session_timeout', 'page_view'
        }

        return event.event_type in valid_events

    async def _store_event(self, event: CustomerEvent):
        """Store event in database"""
        async with self.db_pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            await conn.execute("""
                INSERT INTO customer_events (
                    event_id, customer_id, event_type, properties, timestamp,
                    source, session_id, device_id, ip_address, user_agent
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            event.event_id, event.customer_id, event.event_type,
            json.dumps(event.properties), event.timestamp,
            event.source, event.session_id, event.device_id,
            event.ip_address, event.user_agent
            )

    async def _update_customer_profile(self, event: CustomerEvent):
        """Update customer profile based on event"""
        profile = await self.get_customer_profile(event.customer_id)

        if not profile:
            # Create new profile if doesn't exist
            profile = await self._create_profile_from_event(event)  # ty:ignore[unresolved-attribute]

        # Update profile based on event type
        updated_fields = {}

        if event.event_type == 'deposit':
            amount = float(event.properties.get('amount', 0))
            profile.total_deposits += amount
            profile.lifetime_value += amount
            updated_fields['total_deposits'] = profile.total_deposits
            updated_fields['lifetime_value'] = profile.lifetime_value

        elif event.event_type == 'withdrawal':
            amount = float(event.properties.get('amount', 0))
            profile.total_withdrawals += amount
            updated_fields['total_withdrawals'] = profile.total_withdrawals

        elif event.event_type == 'registration':
            profile.registration_date = event.timestamp
            updated_fields['registration_date'] = profile.registration_date

        # Always update last activity
        profile.last_activity = event.timestamp
        updated_fields['last_activity'] = profile.last_activity

        # Update segments based on activity
        new_segments = await self._calculate_segments(profile)
        if new_segments != profile.segments:
            profile.segments = new_segments
            updated_fields['segments'] = json.dumps(new_segments)

        # Persist updates
        if updated_fields:
            await self._update_profile_fields(event.customer_id, updated_fields)  # ty:ignore[unresolved-attribute]

    async def get_customer_profile(self, customer_id: str) -> Optional[CustomerProfile]:
        """Retrieve customer profile from cache or database"""
        # Check Redis cache first
        cached = await self.redis_client.get(f"profile:{customer_id}")  # ty:ignore[unresolved-attribute]
        if cached:
            return CustomerProfile(**json.loads(cached))

        # Load from database
        async with self.db_pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            row = await conn.fetchrow("""
                SELECT * FROM customer_profiles WHERE customer_id = $1
            """, customer_id)

            if row:
                profile = CustomerProfile(**dict(row))
                # Cache for 5 minutes
                await self.redis_client.setex(  # ty:ignore[unresolved-attribute]
                    f"profile:{customer_id}",
                    300,
                    json.dumps(asdict(profile), default=str)
                )
                return profile

        return None

    async def _perform_identity_resolution(self, event: CustomerEvent):
        """Resolve customer identity across devices and sessions"""
        identifiers = []

        # Extract identifiers from event
        if event.device_id:
            identifiers.append(('device_id', event.device_id))

        if event.ip_address:
            identifiers.append(('ip_address', event.ip_address))

        if event.properties.get('email'):
            identifiers.append(('email', event.properties['email']))

        if event.properties.get('phone'):
            identifiers.append(('phone', event.properties['phone']))

        # Find matching customer IDs
        for id_type, id_value in identifiers:
            existing_customer = await self._find_customer_by_identifier(  # ty:ignore[unresolved-attribute]
                id_type, id_value
            )

            if existing_customer and existing_customer != event.customer_id:
                # Merge customer profiles
                await self._merge_customer_profiles(  # ty:ignore[unresolved-attribute]
                    existing_customer, event.customer_id
                )
                break

    async def _calculate_segments(self, profile: CustomerProfile) -> List[str]:
        """Calculate customer segments based on profile and behavior"""
        segments = []

        # VIP segments based on lifetime value
        if profile.lifetime_value >= 10000:
            segments.append('vip_diamond')
        elif profile.lifetime_value >= 5000:
            segments.append('vip_platinum')
        elif profile.lifetime_value >= 1000:
            segments.append('vip_gold')
        elif profile.lifetime_value >= 100:
            segments.append('vip_silver')

        # Activity segments
        days_since_last_activity = (datetime.now() - profile.last_activity).days

        if days_since_last_activity <= 1:
            segments.append('highly_active')
        elif days_since_last_activity <= 7:
            segments.append('active')
        elif days_since_last_activity <= 30:
            segments.append('moderately_active')
        else:
            segments.append('inactive')

        # Risk segments
        if profile.risk_profile:
            segments.append(f"risk_{profile.risk_profile}")

        # Geographic segments
        if profile.country:
            segments.append(f"country_{profile.country}")

        # Calculate behavioral segments
        if profile.total_deposits > 0:
            withdrawal_ratio = profile.total_withdrawals / profile.total_deposits

            if withdrawal_ratio > 0.8:
                segments.append('high_withdrawer')
            elif withdrawal_ratio < 0.2:
                segments.append('low_withdrawer')

        return segments

    async def get_customer_360_view(self, customer_id: str) -> Dict:
        """Get comprehensive 360-degree view of customer"""
        profile = await self.get_customer_profile(customer_id)
        if not profile:
            return {}

        # Get recent events
        async with self.db_pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            recent_events = await conn.fetch("""
                SELECT * FROM customer_events
                WHERE customer_id = $1
                ORDER BY timestamp DESC
                LIMIT 100
            """, customer_id)

            # Calculate additional metrics
            metrics = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_events,
                    COUNT(DISTINCT session_id) as total_sessions,
                    MIN(timestamp) as first_activity,
                    MAX(timestamp) as last_activity,
                    COUNT(CASE WHEN event_type = 'deposit' THEN 1 END) as deposit_count,
                    COUNT(CASE WHEN event_type = 'withdrawal' THEN 1 END) as withdrawal_count,
                    COUNT(CASE WHEN event_type = 'bet_placed' THEN 1 END) as bet_count
                FROM customer_events
                WHERE customer_id = $1
            """, customer_id)

        return {
            'profile': asdict(profile),
            'recent_events': [dict(event) for event in recent_events],
            'metrics': dict(metrics) if metrics else {},
            'calculated_metrics': {
                'avg_session_duration': await self._calculate_avg_session_duration(customer_id),  # ty:ignore[unresolved-attribute]
                'favorite_games': await self._get_favorite_games(customer_id),  # ty:ignore[unresolved-attribute]
                'peak_activity_hours': await self._get_peak_activity_hours(customer_id),  # ty:ignore[unresolved-attribute]
                'device_preferences': await self._get_device_preferences(customer_id)  # ty:ignore[unresolved-attribute]
            }
        }

    async def export_customer_data(self, customer_id: str) -> Dict:
        """Export all customer data for GDPR compliance"""
        profile = await self.get_customer_profile(customer_id)
        events = []

        async with self.db_pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            # Get all events
            event_rows = await conn.fetch("""
                SELECT * FROM customer_events WHERE customer_id = $1
                ORDER BY timestamp DESC
            """, customer_id)
            events = [dict(row) for row in event_rows]

            # Get identity graph
            identity_rows = await conn.fetch("""
                SELECT * FROM identity_graph WHERE customer_id = $1
            """, customer_id)
            identities = [dict(row) for row in identity_rows]

        return {
            'customer_id': customer_id,
            'profile': asdict(profile) if profile else None,
            'events': events,
            'identities': identities,
            'export_timestamp': datetime.now().isoformat(),
            'data_categories': ['profile', 'activity', 'identities', 'preferences']
        }
