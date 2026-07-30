# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Configuration settings for the Fraud Detection Data Ingestion Service
"""

import os
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict  # ty:ignore[unresolved-import]
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    # Application settings
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    service_name: str = Field(default="fraud-detection-ingestion")

    # Server settings
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080)

    # Kafka settings
    kafka_bootstrap_servers: str = Field(default="localhost:9092")
    kafka_security_protocol: str = Field(default="PLAINTEXT")
    kafka_sasl_mechanism: Optional[str] = Field(default=None)
    kafka_sasl_username: Optional[str] = Field(default=None)
    kafka_sasl_password: Optional[str] = Field(default=None)

    # Schema Registry settings
    schema_registry_url: str = Field(default="http://localhost:8081")

    # Redis settings
    redis_url: str = Field(default="redis://localhost:6379")
    redis_max_connections: int = Field(default=20)

    # PostgreSQL settings
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="fraud_detection")
    postgres_user: str = Field(default="fraud_user")
    postgres_password: str = Field(default="fraud_password")
    postgres_ssl_mode: str = Field(default="prefer")

    # External API settings
    maxmind_api_key: Optional[str] = Field(default=None)
    fingerprintjs_api_key: Optional[str] = Field(default=None)

    # Security settings
    jwt_secret_key: str = Field(default="change-me-in-production")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiration_hours: int = Field(default=24)

    # Rate limiting
    rate_limit_requests: int = Field(default=1000)
    rate_limit_window_seconds: int = Field(default=60)

    # Data validation settings
    max_transaction_amount: float = Field(default=1000000.0)
    min_transaction_amount: float = Field(default=0.01)
    allowed_currencies: List[str] = Field(default=["USD", "EUR", "GBP", "CAD", "AUD"])

    # Monitoring settings
    metrics_enabled: bool = Field(default=True)
    tracing_enabled: bool = Field(default=True)
    health_check_interval: int = Field(default=30)

    # Feature flags
    enable_data_enrichment: bool = Field(default=True)
    enable_ip_geolocation: bool = Field(default=True)
    enable_device_fingerprinting: bool = Field(default=True)

    @property
    def postgres_url(self) -> str:
        """PostgreSQL connection URL"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            f"?sslmode={self.postgres_ssl_mode}"
        )

    @property
    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.environment.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment.lower() == "development"


# Global settings instance
settings = Settings()
