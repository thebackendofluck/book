# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Application configuration loaded from environment variables.
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    DATABASE_URL: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            os.getenv("DATABASE_URL", "postgresql://casino:change-me-in-production@localhost:5432/acmetocasino"),
        )
    )
    REDIS_URL: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    JWT_SECRET: str = field(
        default_factory=lambda: os.getenv("JWT_SECRET", "change-me-in-production")
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    CORS_ORIGINS: list[str] = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,https://new.acmetocasino.com"
        ).split(",")
    )
    APP_NAME: str = "AcmeToCasino Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = field(
        default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true"
    )
    HOST: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    PORT: int = field(
        default_factory=lambda: int(os.getenv("PORT", "8090"))
    )


settings = Settings()
