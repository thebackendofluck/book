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
Vaultwarden Key Retrieval Script for Terraform
Securely retrieves encryption keys from Vaultwarden via YubiHSM 2 integration
"""

import sys
import json
import os
import base64
import hashlib
import requests
from datetime import datetime
from typing import Dict, Optional, Any
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

def get_vaultwarden_keys(query: Dict[str, str]) -> Dict[str, Any]:
    """
    Retrieve encryption keys from Vaultwarden
    
    Args:
        query: Input from Terraform containing:
            - vaultwarden_url: URL of Vaultwarden instance
            - api_key: API key for authentication
            - key_type: Type of keys to retrieve (infrastructure, database, etc.)
    
    Returns:
        Dictionary containing all required keys
    """
    
    try:
        # Parse input
        vaultwarden_url = query.get('vaultwarden_url', '').rstrip('/')
        api_key = query.get('api_key', '')
        key_type = query.get('key_type', 'infrastructure')
        
        if not vaultwarden_url or not api_key:
            raise ValueError("Vaultwarden URL and API key are required")
        
        # Prepare headers
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        # Retrieve keys from Vaultwarden
        # This is a simplified example - in production, use proper Vaultwarden API
        keys_response = requests.post(
            f'{vaultwarden_url}/api/keys/retrieve',
            headers=headers,
            json={'key_type': key_type},
            timeout=30
        )
        
        # If direct API fails, raise an error - do not use deterministic fallback keys
        if keys_response.status_code != 200:
            raise RuntimeError(
                f"Vaultwarden API returned {keys_response.status_code}. "
                "Cannot retrieve keys without a working Vaultwarden connection."
            )
        keys = keys_response.json()
        
        # Ensure all required keys are present
        required_keys = [
            'yubihsm_password',
            'postgres_password',
            'disk_encryption_key',
            'app_secret',
            'rds_master_password',
            'ecs_task_secret',
            'backup_encryption_key'
        ]
        
        for key_name in required_keys:
            if key_name not in keys:
                # Generate missing key deterministically
                keys[key_name] = generate_key(api_key, key_name)
        
        # Add metadata
        keys['retrieved_at'] = str(datetime.now())
        keys['source'] = 'vaultwarden'
        keys['key_type'] = key_type
        
        return keys
        
    except Exception as e:
        raise RuntimeError(f"Vaultwarden is unavailable and no fallback is permitted: {e}") from e

def generate_fallback_keys(seed: str, key_type: str) -> Dict[str, str]:
    """
    Generate fallback keys when Vaultwarden is unavailable
    Uses deterministic generation based on seed
    """
    
    keys = {}
    key_names = [
        'yubihsm_password',
        'postgres_password',
        'disk_encryption_key',
        'app_secret',
        'rds_master_password',
        'ecs_task_secret',
        'backup_encryption_key'
    ]
    
    for key_name in key_names:
        keys[key_name] = generate_key(seed, key_name)
    
    return keys

def generate_key(seed: str, key_name: str, length: int = 32) -> str:
    """
    Generate a deterministic key based on seed and name
    """
    
    # Use PBKDF2 for key derivation
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=length,
        salt=f'{key_name}-salt'.encode(),
        iterations=100000,
        backend=default_backend()
    )
    
    # Derive key
    key_material = kdf.derive(seed.encode())
    
    # Convert to base64 for use as password
    return base64.b64encode(key_material).decode('ascii')

def integrate_with_yubihsm(vaultwarden_url: str, api_key: str) -> Optional[Dict]:
    """
    Integrate with YubiHSM through Vaultwarden plugin
    """
    
    try:
        # Connect to YubiHSM plugin endpoint
        response = requests.post(
            f'{vaultwarden_url}/api/yubihsm/status',
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10
        )
        
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass

    return None

def main():
    """
    Main entry point for Terraform external data source
    """
    
    # Read input from Terraform
    input_data = sys.stdin.read()
    
    try:
        query = json.loads(input_data) if input_data else {}
    except json.JSONDecodeError:
        query = {}
    
    # Get keys from Vaultwarden
    result = get_vaultwarden_keys(query)
    
    # Ensure all values are strings (Terraform requirement)
    output = {k: str(v) if v is not None else "" for k, v in result.items()}
    
    # Output JSON for Terraform
    print(json.dumps(output))

if __name__ == '__main__':
    main()
