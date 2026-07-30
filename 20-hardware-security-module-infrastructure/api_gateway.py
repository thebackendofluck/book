#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
API Gateway for Remote YubiHSM Support
Provides secure REST API access to YubiHSM 2 operations with mTLS authentication
"""

import os
import logging
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
import uvicorn

# YubiHSM imports
from yubihsm import YubiHsm
from yubihsm.defs import ALGORITHM, CAPABILITY, OBJECT, TYPE
from yubihsm.objects import Opaque, SymmetricKey, AsymmetricKey

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/api_gateway.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
HSM_CONNECTOR_URL = os.getenv('HSM_CONNECTOR_URL', 'http://localhost:12345')
HSM_AUTH_KEY_ID = int(os.getenv('HSM_AUTH_KEY_ID', '2'))
HSM_PASSWORD = os.getenv('HSM_PASSWORD')  # Must be set via environment variable; no default
if not HSM_PASSWORD:
    raise RuntimeError("HSM_PASSWORD environment variable is required")
API_PORT = int(os.getenv('API_PORT', '8443'))
API_HOST = os.getenv('API_HOST', '0.0.0.0')
SSL_CERT_FILE = os.getenv('SSL_CERT_FILE', '/etc/ssl/certs/api_gateway.crt')
SSL_KEY_FILE = os.getenv('SSL_KEY_FILE', '/etc/ssl/private/api_gateway.key')
SSL_CA_CERTS = os.getenv('SSL_CA_CERTS', '/etc/ssl/certs/ca.crt')

# Rate limiting (simple in-memory)
request_counts = {}

# HSM Session Manager
class HSMSessionManager:
    def __init__(self):
        self.hsm = None
        self.session = None

    def connect(self):
        """Establish connection to YubiHSM"""
        try:
            self.hsm = YubiHsm.connect(HSM_CONNECTOR_URL)
            self.session = self.hsm.create_session_derived(HSM_AUTH_KEY_ID, HSM_PASSWORD)
            logger.info("Connected to YubiHSM")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to YubiHSM: {e}")
            return False

    def disconnect(self):
        """Close HSM connection"""
        if self.session:
            self.session.close()
        if self.hsm:
            self.hsm.disconnect()
        logger.info("Disconnected from YubiHSM")

    def get_session(self):
        """Get current session"""
        return self.session

# Global HSM manager
hsm_manager = HSMSessionManager()

# Pydantic models
class PasswordRequest(BaseModel):
    name: str = Field(..., description="Password name/label")
    password: str = Field(..., description="Password value")
    domains: int = Field(1, description="HSM domains")

class CertificateRequest(BaseModel):
    name: str = Field(..., description="Certificate name/label")
    certificate_path: str = Field(..., description="Path to certificate file")
    domains: int = Field(1, description="HSM domains")

class APIKeyRequest(BaseModel):
    name: str = Field(..., description="API key name/label")
    api_key: str = Field(..., description="API key value")
    domains: int = Field(1, description="HSM domains")

class KeyGenerationRequest(BaseModel):
    name: str = Field(..., description="Key name/label")
    algorithm: str = Field(..., description="Algorithm (RSA, ECC, AES)")
    key_size: Optional[int] = Field(None, description="Key size for RSA/AES")
    curve: Optional[str] = Field(None, description="Curve for ECC")
    domains: int = Field(1, description="HSM domains")

# Security dependencies
security = HTTPBearer()

def get_client_cert_info(request: Request) -> Dict[str, Any]:
    """Extract client certificate information"""
    client_cert = request.scope.get('client_cert')
    if not client_cert:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Client certificate required"
        )

    # Extract CN from certificate
    subject = client_cert.get('subject', [])
    cn = None
    for item in subject:
        if item[0][0] == 'commonName':
            cn = item[0][1]
            break

    if not cn:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client certificate"
        )

    return {
        'cn': cn,
        'issuer': client_cert.get('issuer'),
        'serial': client_cert.get('serial'),
        'not_before': client_cert.get('not_before'),
        'not_after': client_cert.get('not_after')
    }

def check_rate_limit(client_cn: str, max_requests: int = 100) -> bool:
    """Simple in-memory rate limiting per client (per minute window)"""
    # Use minute-level granularity so limits reset each minute
    current_window = datetime.now().strftime("%Y-%m-%d %H:%M")
    client_key = f"{client_cn}_{current_window}"

    if client_key not in request_counts:
        request_counts[client_key] = 0

    request_counts[client_key] += 1

    if request_counts[client_key] > max_requests:
        return False

    return True

def audit_log(operation: str, client_info: Dict, details: Optional[Dict] = None):
    """Log audit events"""
    log_entry = {
        'timestamp': datetime.utcnow().isoformat(),
        'operation': operation,
        'client_cn': client_info.get('cn'),
        'client_serial': client_info.get('serial'),
        'details': details or {}
    }
    logger.info(f"AUDIT: {json.dumps(log_entry)}")

# FastAPI app
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    if hsm_manager.connect():
        logger.info("API Gateway started successfully")
    else:
        logger.error("Failed to connect to YubiHSM on startup")
        raise RuntimeError("HSM connection failed")

    yield

    # Shutdown
    hsm_manager.disconnect()
    logger.info("API Gateway shut down")

app = FastAPI(
    title="YubiHSM API Gateway",
    description="Secure REST API for remote YubiHSM operations",
    version="1.0.0",
    lifespan=lifespan
)

# Add trusted host middleware - restrict to specific hosts in production
_allowed_hosts_env = os.getenv('ALLOWED_HOSTS', '')
_allowed_hosts = [h.strip() for h in _allowed_hosts_env.split(',') if h.strip()] or ["localhost", "127.0.0.1"]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)  # ty:ignore[invalid-argument-type]

# Routes
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/objects")
async def list_objects(request: Request):
    """List all objects in HSM"""
    client_info = get_client_cert_info(request)

    if not check_rate_limit(client_info['cn']):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    try:
        session = hsm_manager.get_session()
        objects = session.list_objects()

        object_list = []
        for obj in objects:
            object_list.append({
                'id': obj.id,
                'type': obj.type.name,
                'algorithm': obj.algorithm.name if hasattr(obj, 'algorithm') else None,
                'label': obj.label.decode('utf-8') if obj.label else None,
                'capabilities': [cap.name for cap in obj.capabilities] if hasattr(obj, 'capabilities') else None
            })

        audit_log('list_objects', client_info, {'count': len(object_list)})
        return {"objects": object_list}

    except Exception as e:
        logger.error(f"Error listing objects: {e}")
        audit_log('list_objects', client_info, {'error': str(e)})
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/objects/password")
async def store_password(password_req: PasswordRequest, request: Request):
    """Store password in HSM"""
    client_info = get_client_cert_info(request)

    if not check_rate_limit(client_info['cn']):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    try:
        session = hsm_manager.get_session()

        # Find next available ID (simple approach)
        existing_ids = [obj.id for obj in session.list_objects()]
        object_id = max(existing_ids) + 1 if existing_ids else 1000

        # Store password as opaque object
        opaque = Opaque.put(
            session=session,
            object_id=object_id,
            label=f'password-{password_req.name}',
            domains=password_req.domains,
            capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
            algorithm=ALGORITHM.OPAQUE_DATA,
            data=password_req.password.encode('utf-8')
        )

        audit_log('store_password', client_info, {
            'object_id': object_id,
            'name': password_req.name
        })

        return {
            "message": "Password stored successfully",
            "object_id": object_id,
            "name": password_req.name
        }

    except Exception as e:
        logger.error(f"Error storing password: {e}")
        audit_log('store_password', client_info, {
            'name': password_req.name,
            'error': str(e)
        })
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/objects/{object_id}")
async def get_object(object_id: int, request: Request):
    """Retrieve object from HSM"""
    client_info = get_client_cert_info(request)

    if not check_rate_limit(client_info['cn']):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    try:
        session = hsm_manager.get_session()
        obj = session.get_object(object_id, OBJECT.OPAQUE)

        data = obj.get()
        label = obj.label.decode('utf-8') if obj.label else None

        audit_log('get_object', client_info, {'object_id': object_id})
        return {
            "object_id": object_id,
            "label": label,
            "data": data.hex() if isinstance(data, bytes) else str(data)
        }

    except Exception as e:
        logger.error(f"Error retrieving object {object_id}: {e}")
        audit_log('get_object', client_info, {
            'object_id': object_id,
            'error': str(e)
        })
        raise HTTPException(status_code=404, detail="Object not found")

@app.delete("/objects/{object_id}")
async def delete_object(object_id: int, request: Request):
    """Delete object from HSM"""
    client_info = get_client_cert_info(request)

    if not check_rate_limit(client_info['cn']):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    try:
        session = hsm_manager.get_session()
        session.delete_object(object_id, TYPE.OPAQUE)

        audit_log('delete_object', client_info, {'object_id': object_id})
        return {"message": f"Object {object_id} deleted successfully"}

    except Exception as e:
        logger.error(f"Error deleting object {object_id}: {e}")
        audit_log('delete_object', client_info, {
            'object_id': object_id,
            'error': str(e)
        })
        raise HTTPException(status_code=404, detail="Object not found")

@app.post("/keys/generate")
async def generate_key(key_req: KeyGenerationRequest, request: Request):
    """Generate cryptographic key in HSM"""
    client_info = get_client_cert_info(request)

    if not check_rate_limit(client_info['cn']):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    try:
        session = hsm_manager.get_session()

        # Find next available ID
        existing_ids = [obj.id for obj in session.list_objects()]
        object_id = max(existing_ids) + 1 if existing_ids else 3000

        if key_req.algorithm.upper() == 'RSA':
            key = AsymmetricKey.generate_rsa(
                session=session,
                object_id=object_id,
                label=f'rsa-{key_req.name}',
                domains=key_req.domains,
                capabilities=CAPABILITY.SIGN_PKCS | CAPABILITY.SIGN_PSS | CAPABILITY.DECRYPT_PKCS,
                key_size=key_req.key_size or 2048
            )
        elif key_req.algorithm.upper() == 'ECC':
            curve_map = {
                'P-256': ALGORITHM.EC_P256,
                'P-384': ALGORITHM.EC_P384,
                'P-521': ALGORITHM.EC_P521
            }
            algorithm = curve_map.get(key_req.curve or 'P-256', ALGORITHM.EC_P256)

            key = AsymmetricKey.generate_ec(
                session=session,
                object_id=object_id,
                label=f'ecc-{key_req.name}',
                domains=key_req.domains,
                capabilities=CAPABILITY.SIGN_ECDSA | CAPABILITY.SIGN_EDDSA,
                algorithm=algorithm
            )
        elif key_req.algorithm.upper() == 'AES':
            key = SymmetricKey.generate(
                session=session,
                object_id=object_id,
                label=f'aes-{key_req.name}',
                domains=key_req.domains,
                capabilities=CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC,
                algorithm=ALGORITHM.AES256 if (key_req.key_size or 256) >= 256 else ALGORITHM.AES128
            )
        else:
            raise HTTPException(status_code=400, detail="Unsupported algorithm")

        audit_log('generate_key', client_info, {
            'object_id': object_id,
            'algorithm': key_req.algorithm,
            'name': key_req.name
        })

        return {
            "message": "Key generated successfully",
            "object_id": object_id,
            "algorithm": key_req.algorithm,
            "name": key_req.name
        }

    except Exception as e:
        logger.error(f"Error generating key: {e}")
        audit_log('generate_key', client_info, {
            'algorithm': key_req.algorithm,
            'name': key_req.name,
            'error': str(e)
        })
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/stats")
async def get_stats(request: Request):
    """Get HSM statistics"""
    client_info = get_client_cert_info(request)

    if not check_rate_limit(client_info['cn']):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    try:
        session = hsm_manager.get_session()
        objects = session.list_objects()

        stats: dict[str, Any] = {
            'total_objects': len(objects),
            'object_types': {},
            'used_space': len(objects),
            'free_space': 256 - len(objects)
        }

        for obj in objects:
            obj_type = obj.type.name
            stats['object_types'][obj_type] = stats['object_types'].get(obj_type, 0) + 1

        audit_log('get_stats', client_info)
        return stats

    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        audit_log('get_stats', client_info, {'error': str(e)})
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    uvicorn.run(
        "api_gateway:app",
        host=API_HOST,
        port=API_PORT,
        ssl_certfile=SSL_CERT_FILE,
        ssl_keyfile=SSL_KEY_FILE,
        ssl_ca_certs=SSL_CA_CERTS,
        ssl_cert_reqs=2,  # ssl.CERT_REQUIRED
        reload=False,
        log_level="info"
    )

# ─── Chapter 20 supplement: HSM-as-a-Service pattern ─────────────────────────
#
# This gateway uses mTLS for authentication — appropriate for server-to-server
# calls within the internal LAN (Ansible, monitoring, admin scripts).
#
# For Cloudflare Workers calling the YubiHSM 2 remotely, a different pattern
# is needed:
#
#   Workers cannot use mTLS client certificates from an internal CA in the
#   same way (Workers do support mTLS via Cloudflare Access Service Tokens,
#   but the internal PKI is not trusted by Cloudflare by default).
#
#   Solution: a dedicated HSM Proxy API with API-key authentication, proxied
#   through OpenBao Transit, protected by nginx IP allowlist (Cloudflare edge
#   IPs only). See chapter-44/remote-hsm/ for the complete implementation.
#
# Architecture comparison:
#
#   This file (api_gateway.py):
#     - mTLS client certificates
#     - Direct YubiHSM session via python-yubihsm
#     - Used by: internal servers, Ansible, monitoring
#     - Port: 8443 (external TLS)
#
#   chapter-44/remote-hsm/hsm-proxy-api.py:
#     - API key authentication (X-API-Key header)
#     - OpenBao Transit backend (PKCS#11 → YubiHSM 2)
#     - Used by: Cloudflare Workers
#     - Port: 8000 (localhost only, nginx handles TLS)
#
# Both APIs coexist on ops-host. The HSM proxy API (port 8000) does NOT
# accept external connections — nginx's IP allowlist restricts it to
# Cloudflare edge IPs.
#
# Key difference: this gateway manages HSM objects (store/retrieve opaque
# data, generate keys). The HSM proxy API performs crypto operations only
# (encrypt, decrypt, sign, verify, random) and never exposes key material.
#
# For PCI DSS Req 3.5.1: both APIs are valid "remote HSM" patterns.
# The key material never leaves the YubiHSM 2 hardware in either case.
# The QSA will ask: "Is key material ever exported?" Answer: No.
# The YubiHSM 2 non-exportable capability flag is set at key generation time.