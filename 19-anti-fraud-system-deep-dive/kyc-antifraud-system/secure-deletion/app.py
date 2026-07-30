#!/usr/bin/env python3
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
Secure Document Deletion Service
Implements DoD 5220.22-M standard for secure data destruction
"""

import os
import sys
import time
import shutil
import subprocess
import hashlib
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any
import redis
import psycopg2
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # ty:ignore[unresolved-import]
from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)

# Metrics
DOCUMENTS_DELETED = Counter('documents_deleted_total', 'Total documents securely deleted')
DELETION_QUEUE_SIZE = Gauge('deletion_queue_size', 'Number of documents pending deletion')
EMERGENCY_DESTRUCTIONS = Counter('emergency_destructions_total', 'Emergency destructions triggered')

class SecureDeletionService:
    def __init__(self):
        self.redis_client = self._init_redis()
        self.db_conn = self._init_database()
        self.scheduler = AsyncIOScheduler()
        self.deletion_method = os.environ.get('DELETION_METHOD', 'DOD_5220_22_M')
        self.overwrite_passes = int(os.environ.get('OVERWRITE_PASSES', 7))
        self.verification_required = os.environ.get('VERIFICATION_REQUIRED', 'true').lower() == 'true'
        
        # Start scheduler
        self.scheduler.start()
        self._schedule_cleanup_tasks()
        
        logger.info(f"Secure Deletion Service initialized with {self.deletion_method} method")
    
    def _init_redis(self):
        return redis.Redis(
            host=os.environ.get('REDIS_HOST', 'redis-cache'),
            password=os.environ.get('REDIS_PASSWORD'),
            decode_responses=False
        )
    
    def _init_database(self):
        return psycopg2.connect(
            host=os.environ.get('DB_HOST', 'postgres-db'),
            database='kyc_db',
            user=os.environ.get('DB_USER'),
            password=os.environ.get('DB_PASSWORD'),
            sslmode='require'
        )
    
    def _schedule_cleanup_tasks(self):
        """Schedule regular cleanup tasks"""
        
        # Schedule hourly cleanup
        cleanup_hour = int(os.environ.get('SCHEDULED_CLEANUP_HOUR', 3))
        self.scheduler.add_job(
            self.process_deletion_queue,
            'cron',
            hour=cleanup_hour,
            minute=0
        )
        
        # Schedule immediate processing every 5 minutes for urgent deletions
        self.scheduler.add_job(
            self.process_urgent_deletions,
            'interval',
            minutes=5
        )
    
    async def secure_delete_file(self, file_path: str, reason: str = "scheduled") -> bool:
        """Securely delete a file using DoD 5220.22-M standard"""
        
        if not os.path.exists(file_path):
            logger.warning(f"File not found for deletion: {file_path}")
            return False
        
        try:
            file_size = os.path.getsize(file_path)
            
            # Log deletion start
            self._log_deletion_start(file_path, reason, file_size)
            
            # Perform secure overwrite
            if self.deletion_method == 'DOD_5220_22_M':
                await self._dod_5220_22_m_overwrite(file_path)
            elif self.deletion_method == 'GUTMANN':
                await self._gutmann_overwrite(file_path)
            else:
                await self._basic_overwrite(file_path)
            
            # Verify deletion if required
            if self.verification_required:
                if not self._verify_deletion(file_path):
                    raise Exception("Deletion verification failed")
            
            # Remove file
            os.remove(file_path)
            
            # Update metrics
            DOCUMENTS_DELETED.inc()
            
            # Log successful deletion
            self._log_deletion_complete(file_path, reason)
            
            return True
            
        except Exception as e:
            logger.error(f"Secure deletion failed for {file_path}: {str(e)}")
            # Attempt emergency destruction
            await self.emergency_destruction(file_path)
            return False
    
    async def _dod_5220_22_m_overwrite(self, file_path: str):
        """DoD 5220.22-M compliant overwrite (7 passes)"""
        
        file_size = os.path.getsize(file_path)
        
        with open(file_path, 'rb+') as f:
            # Pass 1: Overwrite with zeros
            f.seek(0)
            f.write(b'\x00' * file_size)
            f.flush()
            os.fsync(f.fileno())
            
            # Pass 2: Overwrite with ones
            f.seek(0)
            f.write(b'\xFF' * file_size)
            f.flush()
            os.fsync(f.fileno())
            
            # Pass 3: Overwrite with random data
            f.seek(0)
            f.write(os.urandom(file_size))
            f.flush()
            os.fsync(f.fileno())
            
            # Pass 4: Overwrite with zeros
            f.seek(0)
            f.write(b'\x00' * file_size)
            f.flush()
            os.fsync(f.fileno())
            
            # Pass 5: Overwrite with ones
            f.seek(0)
            f.write(b'\xFF' * file_size)
            f.flush()
            os.fsync(f.fileno())
            
            # Pass 6: Overwrite with random data
            f.seek(0)
            f.write(os.urandom(file_size))
            f.flush()
            os.fsync(f.fileno())
            
            # Pass 7: Overwrite with random data
            f.seek(0)
            f.write(os.urandom(file_size))
            f.flush()
            os.fsync(f.fileno())
    
    async def _gutmann_overwrite(self, file_path: str):
        """Gutmann method overwrite (35 passes)"""
        
        file_size = os.path.getsize(file_path)
        
        # Gutmann patterns
        patterns = [
            b'\x55\x55\x55',  # 01010101 01010101 01010101
            b'\xAA\xAA\xAA',  # 10101010 10101010 10101010
            b'\x92\x49\x24',  # 10010010 01001001 00100100
            b'\x49\x24\x92',  # 01001001 00100100 10010010
            b'\x24\x92\x49',  # 00100100 10010010 01001001
            b'\x00\x00\x00',  # 00000000 00000000 00000000
            b'\x11\x11\x11',  # 00010001 00010001 00010001
            b'\x22\x22\x22',  # 00100010 00100010 00100010
            b'\x33\x33\x33',  # 00110011 00110011 00110011
            b'\x44\x44\x44',  # 01000100 01000100 01000100
            b'\x55\x55\x55',  # 01010101 01010101 01010101
            b'\x66\x66\x66',  # 01100110 01100110 01100110
            b'\x77\x77\x77',  # 01110111 01110111 01110111
            b'\x88\x88\x88',  # 10001000 10001000 10001000
            b'\x99\x99\x99',  # 10011001 10011001 10011001
            b'\xAA\xAA\xAA',  # 10101010 10101010 10101010
            b'\xBB\xBB\xBB',  # 10111011 10111011 10111011
            b'\xCC\xCC\xCC',  # 11001100 11001100 11001100
            b'\xDD\xDD\xDD',  # 11011101 11011101 11011101
            b'\xEE\xEE\xEE',  # 11101110 11101110 11101110
            b'\xFF\xFF\xFF',  # 11111111 11111111 11111111
        ]
        
        with open(file_path, 'rb+') as f:
            # First 4 passes: Random data
            for _ in range(4):
                f.seek(0)
                f.write(os.urandom(file_size))
                f.flush()
                os.fsync(f.fileno())
            
            # Patterns passes 5-31
            for pattern in patterns:
                f.seek(0)
                pattern_data = (pattern * ((file_size // 3) + 1))[:file_size]
                f.write(pattern_data)
                f.flush()
                os.fsync(f.fileno())
            
            # Additional pattern passes
            for _ in range(6):
                f.seek(0)
                f.write(os.urandom(file_size))
                f.flush()
                os.fsync(f.fileno())
            
            # Last 4 passes: Random data
            for _ in range(4):
                f.seek(0)
                f.write(os.urandom(file_size))
                f.flush()
                os.fsync(f.fileno())
    
    async def _basic_overwrite(self, file_path: str):
        """Basic overwrite with specified number of passes"""
        
        file_size = os.path.getsize(file_path)
        
        with open(file_path, 'rb+') as f:
            for pass_num in range(self.overwrite_passes):
                f.seek(0)
                
                if pass_num % 3 == 0:
                    # Write zeros
                    f.write(b'\x00' * file_size)
                elif pass_num % 3 == 1:
                    # Write ones
                    f.write(b'\xFF' * file_size)
                else:
                    # Write random data
                    f.write(os.urandom(file_size))
                
                f.flush()
                os.fsync(f.fileno())
    
    def _verify_deletion(self, file_path: str) -> bool:
        """Verify file has been properly overwritten"""
        
        try:
            with open(file_path, 'rb') as f:
                # Read first and last blocks
                first_block = f.read(4096)
                f.seek(-4096, os.SEEK_END)
                last_block = f.read(4096)
                
                # Check if data appears random (high entropy)
                entropy_first = self._calculate_entropy(first_block)
                entropy_last = self._calculate_entropy(last_block)
                
                # High entropy indicates successful overwrite with random data
                return entropy_first > 7.0 or entropy_last > 7.0
                
        except Exception as e:
            logger.error(f"Verification failed: {str(e)}")
            return False
    
    def _calculate_entropy(self, data: bytes) -> float:
        """Calculate Shannon entropy of data"""
        
        if not data:
            return 0.0
        
        frequency = {}
        for byte in data:
            frequency[byte] = frequency.get(byte, 0) + 1
        
        entropy = 0.0
        data_len = len(data)
        
        for count in frequency.values():
            probability = count / data_len
            if probability > 0:
                entropy -= probability * (probability and probability.bit_length())
        
        return entropy
    
    async def process_deletion_queue(self):
        """Process scheduled deletions from queue"""
        
        logger.info("Processing deletion queue")
        
        # Get documents scheduled for deletion
        current_time = datetime.utcnow().timestamp()  # ty:ignore[deprecated]
        
        # Get items from Redis sorted set
        items = self.redis_client.zrangebyscore(
            'deletion_queue',
            0,
            current_time,
            withscores=True
        )
        
        for doc_id, score in items:
            doc_id = doc_id.decode() if isinstance(doc_id, bytes) else doc_id
            
            try:
                # Get document path
                doc_path = self._get_document_path(doc_id)
                
                if doc_path:
                    # Perform secure deletion
                    success = await self.secure_delete_file(doc_path, "scheduled")
                    
                    if success:
                        # Remove from queue
                        self.redis_client.zrem('deletion_queue', doc_id)
                        
                        # Update database
                        self._mark_document_deleted(doc_id)
                
            except Exception as e:
                logger.error(f"Failed to process deletion for {doc_id}: {str(e)}")
        
        # Update metrics
        remaining = self.redis_client.zcard('deletion_queue')
        DELETION_QUEUE_SIZE.set(remaining)
    
    async def process_urgent_deletions(self):
        """Process urgent deletion requests"""
        
        # Get urgent deletion requests
        urgent_items = self.redis_client.smembers('urgent_deletions')
        
        for doc_id in urgent_items:
            doc_id = doc_id.decode() if isinstance(doc_id, bytes) else doc_id
            
            try:
                doc_path = self._get_document_path(doc_id)
                
                if doc_path:
                    success = await self.secure_delete_file(doc_path, "urgent")
                    
                    if success:
                        self.redis_client.srem('urgent_deletions', doc_id)
                        self._mark_document_deleted(doc_id)
                
            except Exception as e:
                logger.error(f"Failed urgent deletion for {doc_id}: {str(e)}")
    
    async def emergency_destruction(self, file_path: str):
        """Emergency destruction when normal deletion fails"""
        
        EMERGENCY_DESTRUCTIONS.inc()
        logger.critical(f"EMERGENCY DESTRUCTION: {file_path}")
        
        try:
            # Method 1: Truncate file
            with open(file_path, 'wb') as f:
                f.truncate(0)
            
            # Method 2: Use shred command if available. Pass the path as an
            # argv element (never a shell string) so a crafted path cannot
            # inject shell commands.
            if shutil.which('shred'):
                subprocess.run(["shred", "-vfz", "-n", "10", file_path], check=False)
            
            # Method 3: Remove file forcefully
            os.remove(file_path)
            
            # Method 4: If all else fails, trigger container restart
            if os.path.exists(file_path):
                logger.critical("All deletion methods failed, triggering container restart")
                os._exit(1)
                
        except Exception as e:
            logger.critical(f"Emergency destruction failed: {str(e)}")
            # Last resort: exit container
            os._exit(1)
    
    async def gdpr_deletion_request(self, user_id: str) -> Dict[str, Any]:
        """Handle GDPR deletion request"""
        
        logger.info(f"Processing GDPR deletion request for user {user_id}")
        
        # Get all documents for user
        documents = self._get_user_documents(user_id)
        
        deleted_count = 0
        failed_count = 0
        
        for doc in documents:
            doc_path = doc['path']
            
            if os.path.exists(doc_path):
                success = await self.secure_delete_file(doc_path, f"gdpr_request_{user_id}")
                
                if success:
                    deleted_count += 1
                else:
                    failed_count += 1
        
        # Clear user data from database
        self._delete_user_data(user_id)
        
        # Clear from cache
        self.redis_client.delete(f"user:{user_id}")
        
        return {
            'success': failed_count == 0,
            'deleted_documents': deleted_count,
            'failed_deletions': failed_count,
            'timestamp': datetime.utcnow().isoformat()  # ty:ignore[deprecated]
        }
    
    def _get_document_path(self, doc_id: str) -> Optional[str]:  # ty:ignore[unresolved-reference]
        """Get document file path from database"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                SELECT file_path FROM documents
                WHERE document_id = %s
            """, (doc_id,))
            
            result = cursor.fetchone()
            return result[0] if result else None
    
    def _mark_document_deleted(self, doc_id: str):
        """Mark document as deleted in database"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                UPDATE documents
                SET deleted_at = %s,
                    deletion_method = %s,
                    is_deleted = TRUE
                WHERE document_id = %s
            """, (datetime.utcnow(), self.deletion_method, doc_id))  # ty:ignore[deprecated]
            
            self.db_conn.commit()
    
    def _get_user_documents(self, user_id: str) -> List[Dict]:
        """Get all documents for a user"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                SELECT document_id, file_path
                FROM documents
                WHERE user_id = %s AND is_deleted = FALSE
            """, (user_id,))
            
            return [{'id': row[0], 'path': row[1]} for row in cursor.fetchall()]
    
    def _delete_user_data(self, user_id: str):
        """Delete user data from database"""
        
        with self.db_conn.cursor() as cursor:
            # Delete personal data
            cursor.execute("""
                UPDATE users
                SET personal_data = NULL,
                    deleted_at = %s,
                    is_deleted = TRUE
                WHERE user_id = %s
            """, (datetime.utcnow(), user_id))  # ty:ignore[deprecated]
            
            # Delete biometric data
            cursor.execute("""
                DELETE FROM biometric_data
                WHERE user_id = %s
            """, (user_id,))
            
            self.db_conn.commit()
    
    def _log_deletion_start(self, file_path: str, reason: str, file_size: int):
        """Log deletion start for audit trail"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO deletion_audit_log
                (timestamp, file_path, reason, file_size, status)
                VALUES (%s, %s, %s, %s, 'started')
            """, (datetime.utcnow(), file_path, reason, file_size))  # ty:ignore[deprecated]
            
            self.db_conn.commit()
    
    def _log_deletion_complete(self, file_path: str, reason: str):
        """Log successful deletion"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                UPDATE deletion_audit_log
                SET status = 'completed',
                    completed_at = %s
                WHERE file_path = %s AND reason = %s
                ORDER BY timestamp DESC
                LIMIT 1
            """, (datetime.utcnow(), file_path, reason))  # ty:ignore[deprecated]
            
            self.db_conn.commit()

# Initialize service
if __name__ == "__main__":
    deletion_service = SecureDeletionService()
    
    # Run async event loop
    loop = asyncio.get_event_loop()
    
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down Secure Deletion Service")
    finally:
        loop.close()
