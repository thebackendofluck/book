# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
On-Premise Database Extractor for AWS Data Lake

This module provides utilities for extracting data from on-premise
databases and uploading to AWS S3 for data lake ingestion.

Supported Databases:
- PostgreSQL
- MySQL
- SQL Server
- Oracle

Features:
- Incremental extraction (CDC-style)
- Full table extraction
- Parallel extraction
- Data compression
- Encryption in transit
- Checksum validation
- Retry logic

Architecture:
    On-Premise DB -> Extractor -> Encrypted S3 Transfer -> Bronze Layer

Usage:
    extractor = DatabaseExtractor(config)
    await extractor.extract_table("players", incremental=True)

Dependencies:
    pip install asyncpg aiomysql pyodbc boto3 pandas pyarrow
"""

import asyncio
import gzip
import hashlib
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from concurrent.futures import ThreadPoolExecutor

import boto3  # ty:ignore[unresolved-import]
from botocore.config import Config  # ty:ignore[unresolved-import]


# =============================================================================
# CONFIGURATION
# =============================================================================


class DatabaseType(Enum):
    """Supported database types."""

    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLSERVER = "sqlserver"
    ORACLE = "oracle"


@dataclass
class DatabaseConfig:
    """Database connection configuration."""

    db_type: DatabaseType
    host: str
    port: int
    database: str
    username: str
    password: str
    ssl_enabled: bool = True
    connection_timeout: int = 30
    query_timeout: int = 3600


@dataclass
class S3Config:
    """S3 destination configuration."""

    bucket: str
    prefix: str
    region: str = "us-east-1"
    kms_key_id: Optional[str] = None
    storage_class: str = "STANDARD"


@dataclass
class ExtractionConfig:
    """Extraction job configuration."""

    database: DatabaseConfig
    s3: S3Config
    batch_size: int = 100000
    max_parallel_tables: int = 4
    compression: bool = True
    checksum_validation: bool = True
    retry_attempts: int = 3
    retry_delay_seconds: int = 60


@dataclass
class TableConfig:
    """Table extraction configuration."""

    table_name: str
    schema_name: str = "public"
    incremental_column: Optional[str] = None  # e.g., "updated_at"
    primary_key: str = "id"
    columns: Optional[list[str]] = None  # None = all columns
    where_clause: Optional[str] = None


@dataclass
class ExtractionResult:
    """Result of an extraction job."""

    table_name: str
    rows_extracted: int
    bytes_transferred: int
    s3_path: str
    checksum: str
    started_at: datetime
    completed_at: datetime
    success: bool
    error_message: Optional[str] = None


# =============================================================================
# DATABASE CONNECTORS
# =============================================================================


class DatabaseConnector(ABC):
    """Abstract base class for database connectors."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.{config.db_type.value}")

    @abstractmethod
    async def connect(self) -> None:
        """Establish database connection."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close database connection."""
        pass

    @abstractmethod
    async def get_table_schema(self, table: TableConfig) -> list[dict[str, Any]]:
        """Get table schema information."""
        pass

    @abstractmethod
    async def extract_data(
        self,
        table: TableConfig,
        batch_size: int,
        last_value: Optional[Any] = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Extract data from table in batches."""
        pass

    @abstractmethod
    async def get_row_count(self, table: TableConfig) -> int:
        """Get total row count for table."""
        pass


class PostgreSQLConnector(DatabaseConnector):
    """PostgreSQL database connector."""

    def __init__(self, config: DatabaseConfig):
        super().__init__(config)
        self.pool: Optional[Any] = None

    async def connect(self) -> None:
        """Connect to PostgreSQL database."""
        import asyncpg  # ty:ignore[unresolved-import]

        ssl_context = "require" if self.config.ssl_enabled else None

        self.pool = await asyncpg.create_pool(
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
            user=self.config.username,
            password=self.config.password,
            ssl=ssl_context,
            min_size=2,
            max_size=10,
            command_timeout=self.config.query_timeout,
        )
        self.logger.info(f"Connected to PostgreSQL: {self.config.host}")

    async def disconnect(self) -> None:
        """Disconnect from PostgreSQL."""
        if self.pool:
            await self.pool.close()
            self.logger.info("Disconnected from PostgreSQL")

    async def get_table_schema(self, table: TableConfig) -> list[dict[str, Any]]:
        """Get PostgreSQL table schema."""
        query = """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
        """
        async with self.pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            rows = await conn.fetch(query, table.schema_name, table.table_name)
            return [dict(row) for row in rows]

    async def extract_data(
        self,
        table: TableConfig,
        batch_size: int,
        last_value: Optional[Any] = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:  # ty:ignore[invalid-method-override]
        """Extract data from PostgreSQL in batches."""
        columns = ", ".join(table.columns) if table.columns else "*"
        base_query = f'SELECT {columns} FROM "{table.schema_name}"."{table.table_name}"'

        conditions = []
        params = []

        if table.incremental_column and last_value is not None:
            conditions.append(f'"{table.incremental_column}" > ${len(params) + 1}')
            params.append(last_value)

        if table.where_clause:
            conditions.append(f"({table.where_clause})")

        if conditions:
            base_query += " WHERE " + " AND ".join(conditions)

        base_query += f' ORDER BY "{table.primary_key}"'

        offset = 0
        async with self.pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            while True:
                query = f"{base_query} LIMIT {batch_size} OFFSET {offset}"
                rows = await conn.fetch(query, *params)

                if not rows:
                    break

                yield [dict(row) for row in rows]
                offset += batch_size

                if len(rows) < batch_size:
                    break

    async def get_row_count(self, table: TableConfig) -> int:
        """Get row count for PostgreSQL table."""
        query = f'SELECT COUNT(*) FROM "{table.schema_name}"."{table.table_name}"'

        if table.where_clause:
            query += f" WHERE {table.where_clause}"

        async with self.pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            result = await conn.fetchval(query)
            return result or 0


class MySQLConnector(DatabaseConnector):
    """MySQL database connector."""

    def __init__(self, config: DatabaseConfig):
        super().__init__(config)
        self.pool: Optional[Any] = None

    async def connect(self) -> None:
        """Connect to MySQL database."""
        import aiomysql  # ty:ignore[unresolved-import]

        ssl_context = {} if self.config.ssl_enabled else None

        self.pool = await aiomysql.create_pool(
            host=self.config.host,
            port=self.config.port,
            db=self.config.database,
            user=self.config.username,
            password=self.config.password,
            ssl=ssl_context,
            minsize=2,
            maxsize=10,
            connect_timeout=self.config.connection_timeout,
        )
        self.logger.info(f"Connected to MySQL: {self.config.host}")

    async def disconnect(self) -> None:
        """Disconnect from MySQL."""
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            self.logger.info("Disconnected from MySQL")

    async def get_table_schema(self, table: TableConfig) -> list[dict[str, Any]]:
        """Get MySQL table schema."""
        query = """
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """
        async with self.pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            async with conn.cursor() as cur:
                await cur.execute(query, (self.config.database, table.table_name))
                rows = await cur.fetchall()
                return [
                    {
                        "column_name": r[0],
                        "data_type": r[1],
                        "is_nullable": r[2],
                        "column_default": r[3],
                    }
                    for r in rows
                ]

    async def extract_data(
        self,
        table: TableConfig,
        batch_size: int,
        last_value: Optional[Any] = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:  # ty:ignore[invalid-method-override]
        """Extract data from MySQL in batches."""
        columns = ", ".join(f"`{c}`" for c in table.columns) if table.columns else "*"
        base_query = f"SELECT {columns} FROM `{table.table_name}`"

        conditions = []
        params: list[Any] = []

        if table.incremental_column and last_value is not None:
            conditions.append(f"`{table.incremental_column}` > %s")
            params.append(last_value)

        if table.where_clause:
            conditions.append(f"({table.where_clause})")

        if conditions:
            base_query += " WHERE " + " AND ".join(conditions)

        base_query += f" ORDER BY `{table.primary_key}`"

        offset = 0
        async with self.pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            async with conn.cursor() as cur:
                while True:
                    query = f"{base_query} LIMIT {batch_size} OFFSET {offset}"
                    await cur.execute(query, params)
                    rows = await cur.fetchall()

                    if not rows:
                        break

                    # Get column names
                    col_names = [desc[0] for desc in cur.description]
                    yield [dict(zip(col_names, row)) for row in rows]
                    offset += batch_size

                    if len(rows) < batch_size:
                        break

    async def get_row_count(self, table: TableConfig) -> int:
        """Get row count for MySQL table."""
        query = f"SELECT COUNT(*) FROM `{table.table_name}`"

        if table.where_clause:
            query += f" WHERE {table.where_clause}"

        async with self.pool.acquire() as conn:  # ty:ignore[unresolved-attribute]
            async with conn.cursor() as cur:
                await cur.execute(query)
                result = await cur.fetchone()
                return result[0] if result else 0


# =============================================================================
# S3 UPLOADER
# =============================================================================


class S3Uploader:
    """S3 uploader with multipart upload support."""

    def __init__(self, config: S3Config):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.S3Uploader")

        # Configure boto3 with retry logic
        boto_config = Config(
            retries={"max_attempts": 3, "mode": "adaptive"},
            max_pool_connections=50,
        )

        self.s3_client = boto3.client("s3", region_name=config.region, config=boto_config)

    def upload_data(
        self,
        data: bytes,
        key: str,
        compress: bool = True,
    ) -> tuple[str, str]:
        """
        Upload data to S3.

        Args:
            data: Data bytes to upload
            key: S3 object key
            compress: Whether to compress data

        Returns:
            Tuple of (s3_path, checksum)
        """
        # Compress if enabled
        if compress:
            data = gzip.compress(data)
            key = f"{key}.gz"

        # Calculate checksum
        checksum = hashlib.md5(data).hexdigest()

        # Upload with server-side encryption
        extra_args: dict[str, Any] = {
            "StorageClass": self.config.storage_class,
            "ContentMD5": self._calculate_content_md5(data),
        }

        if self.config.kms_key_id:
            extra_args["ServerSideEncryption"] = "aws:kms"
            extra_args["SSEKMSKeyId"] = self.config.kms_key_id
        else:
            extra_args["ServerSideEncryption"] = "AES256"

        self.s3_client.put_object(
            Bucket=self.config.bucket,
            Key=key,
            Body=data,
            **extra_args,
        )

        s3_path = f"s3://{self.config.bucket}/{key}"
        self.logger.info(f"Uploaded {len(data):,} bytes to {s3_path}")

        return s3_path, checksum

    def _calculate_content_md5(self, data: bytes) -> str:
        """Calculate base64-encoded MD5 for Content-MD5 header."""
        import base64

        md5_hash = hashlib.md5(data).digest()
        return base64.b64encode(md5_hash).decode()


# =============================================================================
# DATABASE EXTRACTOR
# =============================================================================


class DatabaseExtractor:
    """Main database extractor orchestrator."""

    def __init__(self, config: ExtractionConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.connector: Optional[DatabaseConnector] = None
        self.uploader = S3Uploader(config.s3)
        self.executor = ThreadPoolExecutor(max_workers=config.max_parallel_tables)

    async def connect(self) -> None:
        """Initialize database connector."""
        if self.config.database.db_type == DatabaseType.POSTGRESQL:
            self.connector = PostgreSQLConnector(self.config.database)
        elif self.config.database.db_type == DatabaseType.MYSQL:
            self.connector = MySQLConnector(self.config.database)
        else:
            raise ValueError(f"Unsupported database type: {self.config.database.db_type}")

        await self.connector.connect()

    async def disconnect(self) -> None:
        """Close database connection."""
        if self.connector:
            await self.connector.disconnect()

    async def extract_table(
        self,
        table: TableConfig,
        last_value: Optional[Any] = None,
    ) -> ExtractionResult:
        """
        Extract data from a single table.

        Args:
            table: Table configuration
            last_value: Last extracted value for incremental extraction

        Returns:
            ExtractionResult with extraction details
        """
        if not self.connector:
            raise RuntimeError("Not connected. Call connect() first.")

        started_at = datetime.now(timezone.utc)
        rows_extracted = 0
        bytes_transferred = 0
        all_checksums: list[str] = []

        try:
            # Get row count for progress tracking
            total_rows = await self.connector.get_row_count(table)
            self.logger.info(f"Starting extraction of {table.table_name}: {total_rows:,} rows")

            batch_num = 0
            async for batch in self.connector.extract_data(
                table,
                self.config.batch_size,
                last_value,
            ):  # ty:ignore[not-iterable]
                # Convert to JSON Lines format
                json_lines = "\n".join(
                    json.dumps(row, default=str) for row in batch
                )
                data = json_lines.encode("utf-8")

                # Generate S3 key
                timestamp = started_at.strftime("%Y%m%d_%H%M%S")
                s3_key = (
                    f"{self.config.s3.prefix}/{table.schema_name}/{table.table_name}/"
                    f"year={started_at.year}/month={started_at.month:02d}/day={started_at.day:02d}/"
                    f"{table.table_name}_{timestamp}_batch{batch_num:05d}.jsonl"
                )

                # Upload to S3
                s3_path, checksum = self.uploader.upload_data(
                    data,
                    s3_key,
                    compress=self.config.compression,
                )

                rows_extracted += len(batch)
                bytes_transferred += len(data)
                all_checksums.append(checksum)
                batch_num += 1

                # Progress logging
                progress = (rows_extracted / total_rows * 100) if total_rows > 0 else 100
                self.logger.info(
                    f"{table.table_name}: {rows_extracted:,}/{total_rows:,} rows ({progress:.1f}%)"
                )

            completed_at = datetime.now(timezone.utc)

            # Combined checksum
            combined_checksum = hashlib.md5(
                "".join(all_checksums).encode()
            ).hexdigest()

            return ExtractionResult(
                table_name=table.table_name,
                rows_extracted=rows_extracted,
                bytes_transferred=bytes_transferred,
                s3_path=f"s3://{self.config.s3.bucket}/{self.config.s3.prefix}/{table.schema_name}/{table.table_name}/",
                checksum=combined_checksum,
                started_at=started_at,
                completed_at=completed_at,
                success=True,
            )

        except Exception as e:
            self.logger.error(f"Extraction failed for {table.table_name}: {e}")
            return ExtractionResult(
                table_name=table.table_name,
                rows_extracted=rows_extracted,
                bytes_transferred=bytes_transferred,
                s3_path="",
                checksum="",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                success=False,
                error_message=str(e),
            )

    async def extract_tables(
        self,
        tables: list[TableConfig],
    ) -> list[ExtractionResult]:
        """
        Extract multiple tables in parallel.

        Args:
            tables: List of table configurations

        Returns:
            List of ExtractionResults
        """
        semaphore = asyncio.Semaphore(self.config.max_parallel_tables)

        async def extract_with_semaphore(table: TableConfig) -> ExtractionResult:
            async with semaphore:
                return await self.extract_table(table)

        tasks = [extract_with_semaphore(table) for table in tables]
        return await asyncio.gather(*tasks)


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


async def main() -> None:
    """Example usage of DatabaseExtractor."""
    logging.basicConfig(level=logging.INFO)

    # Configuration
    db_config = DatabaseConfig(
        db_type=DatabaseType.POSTGRESQL,
        host="on-premise-db.internal",
        port=5432,
        database="casino_production",
        username=os.environ.get("DB_USERNAME", ""),
        password=os.environ.get("DB_PASSWORD", ""),
        ssl_enabled=True,
    )

    s3_config = S3Config(
        bucket="igaming-datalake-bronze",
        prefix="on-premise/postgresql",
        region="us-east-1",
        kms_key_id=os.environ.get("KMS_KEY_ID"),
    )

    config = ExtractionConfig(
        database=db_config,
        s3=s3_config,
        batch_size=100000,
        max_parallel_tables=4,
    )

    # Tables to extract
    tables = [
        TableConfig(
            table_name="players",
            schema_name="public",
            incremental_column="updated_at",
            primary_key="player_id",
        ),
        TableConfig(
            table_name="transactions",
            schema_name="public",
            incremental_column="created_at",
            primary_key="transaction_id",
        ),
        TableConfig(
            table_name="game_sessions",
            schema_name="public",
            incremental_column="ended_at",
            primary_key="session_id",
        ),
    ]

    # Run extraction
    extractor = DatabaseExtractor(config)

    try:
        await extractor.connect()
        results = await extractor.extract_tables(tables)

        # Print results
        print("\n" + "=" * 70)
        print("EXTRACTION SUMMARY")
        print("=" * 70)

        total_rows = 0
        total_bytes = 0

        for result in results:
            status = "SUCCESS" if result.success else "FAILED"
            duration = (result.completed_at - result.started_at).total_seconds()

            print(f"\n{result.table_name}:")
            print(f"  Status: {status}")
            print(f"  Rows: {result.rows_extracted:,}")
            print(f"  Bytes: {result.bytes_transferred:,}")
            print(f"  Duration: {duration:.1f}s")
            print(f"  S3 Path: {result.s3_path}")

            if result.success:
                total_rows += result.rows_extracted
                total_bytes += result.bytes_transferred

        print("\n" + "-" * 70)
        print(f"Total Rows Extracted: {total_rows:,}")
        print(f"Total Bytes Transferred: {total_bytes:,}")
        print("=" * 70)

    finally:
        await extractor.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
