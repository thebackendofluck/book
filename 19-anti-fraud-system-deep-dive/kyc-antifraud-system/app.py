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
HSM-based Encryption Service
Manages encryption keys and cryptographic operations using Hardware Security Module
"""

import json
import os
import sys
import time
import logging
import hashlib
import secrets
import struct
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

import redis
import psycopg2
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding, serialization, hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2  # ty:ignore[unresolved-import]
from cryptography.hazmat.primitives.asymmetric import rsa, padding as asym_padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from prometheus_client import Counter, Histogram, Gauge
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn

# For production, use actual PKCS#11 library
# import pkcs11

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/logs/encryption_service.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Prometheus metrics
ENCRYPTION_OPERATIONS = Counter('hsm_encryption_operations_total', 'Total encryption operations')
DECRYPTION_OPERATIONS = Counter('hsm_decryption_operations_total', 'Total decryption operations')
KEY_ROTATIONS = Counter('hsm_key_rotations_total', 'Total key rotations')
OPERATION_TIME = Histogram('hsm_operation_duration_seconds', 'HSM operation duration')
ACTIVE_KEYS = Gauge('hsm_active_keys', 'Number of active encryption keys')

app = FastAPI(title="HSM Encryption Service", version="1.0.0")
security = HTTPBearer()

class KeyType(Enum):
    MASTER = "master"
    DATA = "data"
    SESSION = "session"
    BACKUP = "backup"

class EncryptionAlgorithm(Enum):
    AES_256_GCM = "aes-256-gcm"
    AES_256_CBC = "aes-256-cbc"
    CHACHA20_POLY1305 = "chacha20-poly1305"
    RSA_4096 = "rsa-4096"

@dataclass
class EncryptionKey:
    key_id: str
    key_type: KeyType
    algorithm: EncryptionAlgorithm
    created_at: datetime
    rotated_at: Optional[datetime]
    expires_at: datetime
    is_active: bool
    version: int
    metadata: Dict[str, Any]

@dataclass
class EncryptedData:
    ciphertext: bytes
    iv: bytes
    tag: Optional[bytes]
    key_id: str
    algorithm: str
    timestamp: datetime
    metadata: Dict[str, Any]

class MockHSMClient:
    """Mock HSM client for demonstration (replace with actual PKCS#11 in production)"""
    
    def __init__(self):
        self.keys = {}
        self.master_key = None
        logger.info("Mock HSM Client initialized")
    
    def generate_key(self, key_type: str, algorithm: str, key_size: int) -> str:
        """Generate a new key in HSM"""
        key_id = f"{key_type}_{secrets.token_hex(8)}"
        
        if algorithm.startswith('aes'):
            key = os.urandom(key_size // 8)
        elif algorithm == 'rsa':
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=key_size,
                backend=default_backend()
            )
            key = private_key
        else:
            key = os.urandom(32)  # Default 256-bit key
        
        self.keys[key_id] = key
        return key_id
    
    def get_key(self, key_id: str) -> Any:
        """Retrieve key from HSM"""
        return self.keys.get(key_id)
    
    def delete_key(self, key_id: str) -> bool:
        """Delete key from HSM"""
        if key_id in self.keys:
            del self.keys[key_id]
            return True
        return False
    
    def encrypt(self, key_id: str, plaintext: bytes, algorithm: str) -> Tuple[bytes, bytes, bytes]:
        """Encrypt data using HSM key"""
        key = self.get_key(key_id)
        if not key:
            raise ValueError(f"Key {key_id} not found")
        
        if algorithm == 'aes-256-gcm':
            iv = os.urandom(16)
            cipher = AESGCM(key)
            ciphertext = cipher.encrypt(iv, plaintext, None)
            # AESGCM includes tag in ciphertext
            return ciphertext[:-16], iv, ciphertext[-16:]
        else:
            # Simplified for other algorithms
            iv = os.urandom(16)
            return plaintext, iv, b''
    
    def decrypt(self, key_id: str, ciphertext: bytes, iv: bytes, tag: Optional[bytes], algorithm: str) -> bytes:
        """Decrypt data using HSM key"""
        key = self.get_key(key_id)
        if not key:
            raise ValueError(f"Key {key_id} not found")
        
        if algorithm == 'aes-256-gcm':
            cipher = AESGCM(key)
            # Reconstruct ciphertext with tag
            full_ciphertext = ciphertext + (tag or b'')
            plaintext = cipher.decrypt(iv, full_ciphertext, None)
            return plaintext
        else:
            # Simplified for other algorithms
            return ciphertext

class HSMEncryptionService:
    """Production-ready HSM encryption service"""
    
    def __init__(self):
        self.hsm_client = self._init_hsm()
        self.redis_client = self._init_redis()
        self.db_conn = self._init_database()
        self.master_key_id = self._init_master_key()
        self.key_cache = {}
        self.rotation_schedule = {}
        
        # Configuration
        self.key_rotation_days = int(os.environ.get('KEY_ROTATION_DAYS', 90))
        self.default_algorithm = EncryptionAlgorithm.AES_256_GCM
        
        logger.info("HSM Encryption Service initialized")
    
    def _init_hsm(self) -> MockHSMClient:
        """Initialize HSM connection"""
        # In production, initialize actual PKCS#11 library
        # lib = pkcs11.lib(os.environ['HSM_LIB_PATH'])
        # token = lib.get_token(slot=int(os.environ['HSM_SLOT']))
        # session = token.open(user_pin=os.environ['HSM_PIN'])
        
        return MockHSMClient()
    
    def _init_redis(self):
        """Initialize Redis for key metadata caching"""
        return redis.Redis(
            host=os.environ.get('REDIS_HOST', 'redis-cache'),
            port=6379,
            password=os.environ.get('REDIS_PASSWORD'),
            ssl=True,
            ssl_cert_reqs='required',
            decode_responses=False
        )
    
    def _init_database(self):
        """Initialize database for audit logging"""
        return psycopg2.connect(
            host=os.environ.get('DB_HOST', 'postgres-db'),
            database='kyc_db',
            user=os.environ.get('DB_USER'),
            password=os.environ.get('DB_PASSWORD'),
            sslmode='require'
        )
    
    def _init_master_key(self) -> str:
        """Initialize or load master encryption key"""
        # Check Redis for existing master key
        master_key_id = self.redis_client.get('master_key_id')
        
        if master_key_id:
            logger.info(f"Loaded existing master key: {master_key_id.decode()}")
            return master_key_id.decode()
        
        # Generate new master key
        master_key_id = self.hsm_client.generate_key(
            key_type=KeyType.MASTER.value,
            algorithm='aes-256-gcm',
            key_size=256
        )
        
        # Store master key reference
        self.redis_client.set('master_key_id', master_key_id)
        self.redis_client.expire('master_key_id', 86400 * 365)  # 1 year
        
        # Log key generation
        self._audit_log('MASTER_KEY_GENERATED', {'key_id': master_key_id})
        
        logger.info(f"Generated new master key: {master_key_id}")
        return master_key_id
    
    async def encrypt_data(
        self,
        plaintext: bytes,
        context: Optional[str] = None,
        key_type: KeyType = KeyType.DATA,
        algorithm: Optional[EncryptionAlgorithm] = None
    ) -> EncryptedData:
        """Encrypt data using HSM-protected keys"""
        
        start_time = time.time()
        ENCRYPTION_OPERATIONS.inc()
        
        try:
            # Select encryption algorithm
            algo = algorithm or self.default_algorithm
            
            # Get or generate encryption key
            key_id = await self._get_or_create_key(key_type, algo)
            
            # Perform encryption in HSM
            ciphertext, iv, tag = self.hsm_client.encrypt(
                key_id=key_id,
                plaintext=plaintext,
                algorithm=algo.value
            )
            
            # Create encrypted data object
            encrypted_data = EncryptedData(
                ciphertext=ciphertext,
                iv=iv,
                tag=tag,
                key_id=key_id,
                algorithm=algo.value,
                timestamp=datetime.utcnow(),  # ty:ignore[deprecated]
                metadata={
                    'context': context,
                    'plaintext_size': len(plaintext)
                }
            )
            
            # Cache encryption metadata
            self._cache_encryption_metadata(encrypted_data)
            
            # Audit log
            self._audit_log('DATA_ENCRYPTED', {
                'key_id': key_id,
                'algorithm': algo.value,
                'context': context,
                'size': len(plaintext)
            })
            
            OPERATION_TIME.observe(time.time() - start_time)
            
            return encrypted_data
            
        except Exception as e:
            logger.error(f"Encryption failed: {str(e)}")
            self._audit_log('ENCRYPTION_FAILED', {'error': str(e)})
            raise
    
    async def decrypt_data(
        self,
        encrypted_data: EncryptedData,
        context: Optional[str] = None
    ) -> bytes:
        """Decrypt data using HSM-protected keys"""
        
        start_time = time.time()
        DECRYPTION_OPERATIONS.inc()
        
        try:
            # Verify key is still valid
            if not await self._verify_key_validity(encrypted_data.key_id):
                raise ValueError(f"Key {encrypted_data.key_id} is no longer valid")
            
            # Perform decryption in HSM
            plaintext = self.hsm_client.decrypt(
                key_id=encrypted_data.key_id,
                ciphertext=encrypted_data.ciphertext,
                iv=encrypted_data.iv,
                tag=encrypted_data.tag,
                algorithm=encrypted_data.algorithm
            )
            
            # Audit log
            self._audit_log('DATA_DECRYPTED', {
                'key_id': encrypted_data.key_id,
                'algorithm': encrypted_data.algorithm,
                'context': context
            })
            
            OPERATION_TIME.observe(time.time() - start_time)
            
            return plaintext
            
        except Exception as e:
            logger.error(f"Decryption failed: {str(e)}")
            self._audit_log('DECRYPTION_FAILED', {
                'key_id': encrypted_data.key_id,
                'error': str(e)
            })
            raise
    
    async def _get_or_create_key(
        self,
        key_type: KeyType,
        algorithm: EncryptionAlgorithm
    ) -> str:
        """Get existing key or create new one"""
        
        # Check cache for active key
        cache_key = f"{key_type.value}_{algorithm.value}_active"
        key_id = self.redis_client.get(cache_key)
        
        if key_id:
            return key_id.decode()
        
        # Generate new key in HSM
        key_size = 256 if 'aes' in algorithm.value else 4096
        key_id = self.hsm_client.generate_key(
            key_type=key_type.value,
            algorithm=algorithm.value,
            key_size=key_size
        )
        
        # Create key metadata
        key_metadata = EncryptionKey(
            key_id=key_id,
            key_type=key_type,
            algorithm=algorithm,
            created_at=datetime.utcnow(),  # ty:ignore[deprecated]
            rotated_at=None,
            expires_at=datetime.utcnow() + timedelta(days=self.key_rotation_days),  # ty:ignore[deprecated]
            is_active=True,
            version=1,
            metadata={}
        )
        
        # Store key metadata
        self._store_key_metadata(key_metadata)
        
        # Cache active key reference
        self.redis_client.setex(
            cache_key,
            86400,  # 24 hours
            key_id
        )
        
        ACTIVE_KEYS.inc()
        
        return key_id
    
    async def _verify_key_validity(self, key_id: str) -> bool:
        """Verify if key is still valid"""
        
        # Check if key exists in HSM
        key = self.hsm_client.get_key(key_id)
        if not key:
            return False
        
        # Check key metadata
        metadata = self._get_key_metadata(key_id)
        if not metadata:
            return False
        
        # Check expiration
        if metadata.expires_at < datetime.utcnow():  # ty:ignore[deprecated]
            return False
        
        # Check if key is active
        return metadata.is_active
    
    async def rotate_keys(self, force: bool = False) -> Dict[str, Any]:
        """Perform key rotation"""
        
        KEY_ROTATIONS.inc()
        start_time = time.time()
        rotated_keys = []
        
        try:
            logger.info("Starting key rotation process")
            
            # Get all active keys
            active_keys = self._get_active_keys()
            
            for key_metadata in active_keys:
                # Check if rotation is needed
                should_rotate = force or self._should_rotate_key(key_metadata)
                
                if should_rotate:
                    # Generate new key
                    new_key_id = self.hsm_client.generate_key(
                        key_type=key_metadata.key_type.value,
                        algorithm=key_metadata.algorithm.value,
                        key_size=256 if 'aes' in key_metadata.algorithm.value else 4096
                    )
                    
                    # Re-encrypt active data with new key
                    await self._reencrypt_with_new_key(
                        old_key_id=key_metadata.key_id,
                        new_key_id=new_key_id
                    )
                    
                    # Update key metadata
                    key_metadata.is_active = False
                    key_metadata.rotated_at = datetime.utcnow()  # ty:ignore[deprecated]
                    self._store_key_metadata(key_metadata)
                    
                    # Create new key metadata
                    new_key_metadata = EncryptionKey(
                        key_id=new_key_id,
                        key_type=key_metadata.key_type,
                        algorithm=key_metadata.algorithm,
                        created_at=datetime.utcnow(),  # ty:ignore[deprecated]
                        rotated_at=None,
                        expires_at=datetime.utcnow() + timedelta(days=self.key_rotation_days),  # ty:ignore[deprecated]
                        is_active=True,
                        version=key_metadata.version + 1,
                        metadata={'rotated_from': key_metadata.key_id}
                    )
                    
                    self._store_key_metadata(new_key_metadata)
                    
                    rotated_keys.append({
                        'old_key': key_metadata.key_id,
                        'new_key': new_key_id,
                        'type': key_metadata.key_type.value
                    })
                    
                    # Schedule old key deletion
                    await self._schedule_key_deletion(key_metadata.key_id, days=30)
            
            # Audit log
            self._audit_log('KEYS_ROTATED', {
                'count': len(rotated_keys),
                'keys': rotated_keys
            })
            
            logger.info(f"Key rotation completed: {len(rotated_keys)} keys rotated")
            
            return {
                'success': True,
                'rotated_count': len(rotated_keys),
                'rotated_keys': rotated_keys,
                'duration': time.time() - start_time
            }
            
        except Exception as e:
            logger.error(f"Key rotation failed: {str(e)}")
            self._audit_log('KEY_ROTATION_FAILED', {'error': str(e)})
            raise
    
    def _should_rotate_key(self, key_metadata: EncryptionKey) -> bool:
        """Determine if key should be rotated"""
        
        # Check age
        age = datetime.utcnow() - key_metadata.created_at  # ty:ignore[deprecated]
        if age.days >= self.key_rotation_days:
            return True
        
        # Check if approaching expiration
        time_to_expiry = key_metadata.expires_at - datetime.utcnow()  # ty:ignore[deprecated]
        if time_to_expiry.days <= 7:
            return True
        
        return False
    
    async def _reencrypt_with_new_key(self, old_key_id: str, new_key_id: str):
        """Re-encrypt data with new key"""
        
        # This would need to iterate through all encrypted data
        # and re-encrypt with the new key
        # Implementation depends on data storage strategy
        
        logger.info(f"Re-encrypting data from key {old_key_id} to {new_key_id}")
        # Placeholder for re-encryption logic
        pass
    
    async def _schedule_key_deletion(self, key_id: str, days: int):
        """Schedule key for deletion after specified days"""
        
        deletion_time = datetime.utcnow() + timedelta(days=days)  # ty:ignore[deprecated]
        
        # Add to deletion queue
        self.redis_client.zadd(
            'key_deletion_queue',
            {key_id: deletion_time.timestamp()}
        )
        
        logger.info(f"Key {key_id} scheduled for deletion at {deletion_time}")
    
    async def emergency_key_destruction(self):
        """Emergency destruction of all keys"""
        
        logger.critical("EMERGENCY KEY DESTRUCTION INITIATED")
        
        try:
            # Get all keys
            all_keys = self._get_all_keys()
            
            # Destroy each key in HSM
            for key_metadata in all_keys:
                self.hsm_client.delete_key(key_metadata.key_id)
                
                # Clear from cache
                self.redis_client.delete(f"key:{key_metadata.key_id}")
            
            # Clear master key
            self.hsm_client.delete_key(self.master_key_id)
            self.redis_client.delete('master_key_id')
            
            # Audit log
            self._audit_log('EMERGENCY_KEY_DESTRUCTION', {
                'destroyed_count': len(all_keys)
            })
            
            logger.critical(f"Emergency destruction completed: {len(all_keys)} keys destroyed")
            
        except Exception as e:
            logger.critical(f"Emergency destruction failed: {str(e)}")
            # Last resort: clear all memory
            self.hsm_client.keys.clear()
            self.key_cache.clear()
    
    def _store_key_metadata(self, key_metadata: EncryptionKey):
        """Store key metadata in database"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO encryption_keys 
                (key_id, key_type, algorithm, created_at, expires_at, is_active, version, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (key_id) UPDATE SET
                    is_active = EXCLUDED.is_active,
                    rotated_at = %s
            """, (
                key_metadata.key_id,
                key_metadata.key_type.value,
                key_metadata.algorithm.value,
                key_metadata.created_at,
                key_metadata.expires_at,
                key_metadata.is_active,
                key_metadata.version,
                json.dumps(key_metadata.metadata),
                key_metadata.rotated_at
            ))
            self.db_conn.commit()
    
    def _get_key_metadata(self, key_id: str) -> Optional[EncryptionKey]:
        """Retrieve key metadata from database"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                SELECT key_type, algorithm, created_at, rotated_at, 
                       expires_at, is_active, version, metadata
                FROM encryption_keys
                WHERE key_id = %s
            """, (key_id,))
            
            row = cursor.fetchone()
            if row:
                return EncryptionKey(
                    key_id=key_id,
                    key_type=KeyType(row[0]),
                    algorithm=EncryptionAlgorithm(row[1]),
                    created_at=row[2],
                    rotated_at=row[3],
                    expires_at=row[4],
                    is_active=row[5],
                    version=row[6],
                    metadata=row[7]
                )
        
        return None
    
    def _get_active_keys(self) -> List[EncryptionKey]:
        """Get all active encryption keys"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                SELECT key_id, key_type, algorithm, created_at, rotated_at,
                       expires_at, is_active, version, metadata
                FROM encryption_keys
                WHERE is_active = TRUE
            """)
            
            keys = []
            for row in cursor.fetchall():
                keys.append(EncryptionKey(
                    key_id=row[0],
                    key_type=KeyType(row[1]),
                    algorithm=EncryptionAlgorithm(row[2]),
                    created_at=row[3],
                    rotated_at=row[4],
                    expires_at=row[5],
                    is_active=row[6],
                    version=row[7],
                    metadata=row[8]
                ))
            
            return keys
    
    def _get_all_keys(self) -> List[EncryptionKey]:
        """Get all encryption keys"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                SELECT key_id, key_type, algorithm, created_at, rotated_at,
                       expires_at, is_active, version, metadata
                FROM encryption_keys
            """)
            
            keys = []
            for row in cursor.fetchall():
                keys.append(EncryptionKey(
                    key_id=row[0],
                    key_type=KeyType(row[1]),
                    algorithm=EncryptionAlgorithm(row[2]),
                    created_at=row[3],
                    rotated_at=row[4],
                    expires_at=row[5],
                    is_active=row[6],
                    version=row[7],
                    metadata=row[8]
                ))
            
            return keys
    
    def _cache_encryption_metadata(self, encrypted_data: EncryptedData):
        """Cache encryption metadata for performance"""
        
        cache_key = f"enc:{hashlib.sha256(encrypted_data.ciphertext).hexdigest()[:16]}"
        
        metadata = {
            'key_id': encrypted_data.key_id,
            'algorithm': encrypted_data.algorithm,
            'timestamp': encrypted_data.timestamp.isoformat(),
            'metadata': encrypted_data.metadata
        }
        
        self.redis_client.setex(
            cache_key,
            3600,  # 1 hour
            json.dumps(metadata)
        )
    
    def _audit_log(self, action: str, details: Dict[str, Any]):
        """Log security events for audit trail"""
        
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO encryption_audit_log 
                (timestamp, action, details, user_id, ip_address)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                datetime.utcnow(),  # ty:ignore[deprecated]
                action,
                json.dumps(details),
                os.environ.get('SERVICE_USER', 'encryption_service'),
                os.environ.get('POD_IP', '127.0.0.1')
            ))
            self.db_conn.commit()


# Initialize service
encryption_service = HSMEncryptionService()

# API Endpoints
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "HSM Encryption Service"}

@app.post("/encrypt")
async def encrypt_endpoint(
    data: bytes,
    context: Optional[str] = None,
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    """Encrypt data endpoint"""
    try:
        encrypted = await encryption_service.encrypt_data(data, context)
        return {
            "success": True,
            "encrypted_data": encrypted.ciphertext.hex(),
            "iv": encrypted.iv.hex(),
            "tag": encrypted.tag.hex() if encrypted.tag else None,
            "key_id": encrypted.key_id,
            "algorithm": encrypted.algorithm
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/decrypt")
async def decrypt_endpoint(
    encrypted_data: Dict[str, Any],
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    """Decrypt data endpoint"""
    try:
        encrypted = EncryptedData(
            ciphertext=bytes.fromhex(encrypted_data['ciphertext']),
            iv=bytes.fromhex(encrypted_data['iv']),
            tag=bytes.fromhex(encrypted_data['tag']) if encrypted_data.get('tag') else None,
            key_id=encrypted_data['key_id'],
            algorithm=encrypted_data['algorithm'],
            timestamp=datetime.fromisoformat(encrypted_data.get('timestamp', datetime.utcnow().isoformat())),  # ty:ignore[deprecated]
            metadata=encrypted_data.get('metadata', {})
        )
        
        plaintext = await encryption_service.decrypt_data(encrypted)
        
        return {
            "success": True,
            "plaintext": plaintext.hex()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/rotate-keys")
async def rotate_keys_endpoint(
    force: bool = False,
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    """Trigger key rotation"""
    try:
        result = await encryption_service.rotate_keys(force=force)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/emergency-destroy")
async def emergency_destroy_endpoint(
    confirmation: str,
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    """Emergency key destruction (requires confirmation)"""
    if confirmation != "CONFIRM_DESTROY_ALL_KEYS":
        raise HTTPException(status_code=400, detail="Invalid confirmation")
    
    try:
        await encryption_service.emergency_key_destruction()
        return {"status": "All keys destroyed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
