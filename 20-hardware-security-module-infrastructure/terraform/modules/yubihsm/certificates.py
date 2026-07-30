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
YubiHSM Certificate Management Lambda Function
Handles certificate lifecycle, rotation, and Let's Encrypt integration
"""

import os
import json
import boto3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from yubihsm import YubiHsm
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class YubiHSMCertificateManager:
    """Manages certificates in YubiHSM"""

    def __init__(self):
        self.hsm_connector = os.environ.get('YUBIHSM_CONNECTOR_HOST', 'localhost:12345')
        self.auth_key_id = int(os.environ.get('YUBIHSM_AUTH_KEY_ID', '1'))
        # fail-fast: set YUBIHSM_AUTH_PASSWORD in environment before running this script
        self.auth_password = os.environ['YUBIHSM_AUTH_PASSWORD']  # raises KeyError if unset
        self.lets_encrypt_enabled = os.environ.get('LETS_ENCRYPT_ENABLED', 'false').lower() == 'true'
        self.lets_encrypt_email = os.environ.get('LETS_ENCRYPT_EMAIL', '')
        self.cert_validity_days = int(os.environ.get('CERT_VALIDITY_DAYS', '365'))

        # AWS clients
        self.acm_client = boto3.client('acm')
        self.route53_client = boto3.client('route53')

        self.hsm = None
        self.session = None

    def connect_hsm(self) -> bool:
        """Connect to YubiHSM"""
        try:
            self.hsm = YubiHsm.connect(f'http://{self.hsm_connector}')
            self.session = self.hsm.create_session_derived(self.auth_key_id, self.auth_password)
            logger.info("Connected to YubiHSM")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to YubiHSM: {e}")
            return False

    def disconnect_hsm(self):
        """Disconnect from YubiHSM"""
        if self.session:
            self.session.close()
        if self.hsm:
            self.hsm.close()

    def list_certificates(self) -> List[Dict]:
        """List all certificates in HSM"""
        try:
            certificates = []
            hsm_objects = self.session.list_objects(object_type=1)  # Certificates

            for obj in hsm_objects:
                cert_data = obj.get()
                cert_info = self._parse_certificate(cert_data)
                if cert_info:
                    certificates.append({
                        'id': obj.id,
                        'label': obj.label.decode('utf-8') if obj.label else '',
                        'subject': cert_info.get('subject', ''),
                        'issuer': cert_info.get('issuer', ''),
                        'not_before': cert_info.get('not_before', ''),
                        'not_after': cert_info.get('not_after', ''),
                        'is_expired': cert_info.get('is_expired', False),
                        'days_until_expiry': cert_info.get('days_until_expiry', 0)
                    })

            return certificates
        except Exception as e:
            logger.error(f"Failed to list certificates: {e}")
            return []

    def check_expiring_certificates(self, days: int = 30) -> List[Dict]:
        """Find certificates expiring within specified days"""
        certificates = self.list_certificates()
        expiring = []

        for cert in certificates:
            if cert['days_until_expiry'] <= days and not cert['is_expired']:
                expiring.append(cert)

        return expiring

    def rotate_certificate(self, cert_id: int, domain: str) -> bool:
        """Rotate a certificate"""
        try:
            logger.info(f"Rotating certificate {cert_id} for domain {domain}")

            # Generate new certificate
            new_cert_data = self._generate_certificate(domain)

            if not new_cert_data:
                logger.error("Failed to generate new certificate")
                return False

            # Store new certificate with new ID
            new_cert_id = self._find_available_cert_id()
            cert_obj = self.session.get_object(cert_id, 1)
            cert_obj.put(
                session=self.session,
                object_id=new_cert_id,
                label=f"{domain}-rotated-{datetime.now().strftime('%Y%m%d')}",
                domains=1,
                capabilities=cert_obj.capabilities,
                data=new_cert_data
            )

            # Delete old certificate
            self.session.delete_object(cert_id, 1)

            logger.info(f"Certificate rotated: {cert_id} -> {new_cert_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to rotate certificate {cert_id}: {e}")
            return False

    def request_lets_encrypt_certificate(self, domain: str) -> Optional[bytes]:
        """Request certificate from Let's Encrypt"""
        if not self.lets_encrypt_enabled:
            logger.info("Let's Encrypt not enabled")
            return None

        try:
            # This is a simplified implementation
            # Real implementation would use certbot or acme library
            logger.info(f"Requesting Let's Encrypt certificate for {domain}")

            # Placeholder - would implement ACME protocol
            # For now, return None to indicate not implemented
            logger.warning("Let's Encrypt integration not fully implemented")
            return None

        except Exception as e:
            logger.error(f"Failed to request Let's Encrypt certificate: {e}")
            return None

    def store_certificate(self, cert_data: bytes, label: str) -> Optional[int]:
        """Store certificate in HSM"""
        try:
            cert_id = self._find_available_cert_id()

            from yubihsm.objects import Opaque
            from yubihsm.defs import CAPABILITY

            opaque = Opaque.put(
                session=self.session,
                object_id=cert_id,
                label=label,
                domains=1,
                capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
                algorithm=1,  # Opaque data
                data=cert_data
            )

            logger.info(f"Certificate stored: {label} (ID: {cert_id})")
            return cert_id

        except Exception as e:
            logger.error(f"Failed to store certificate {label}: {e}")
            return None

    def _parse_certificate(self, cert_data: bytes) -> Optional[Dict]:
        """Parse X.509 certificate"""
        try:
            cert = x509.load_der_x509_certificate(cert_data, default_backend())

            subject = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            subject_cn = subject[0].value if subject else ""

            issuer = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
            issuer_cn = issuer[0].value if issuer else ""

            now = datetime.now()
            is_expired = cert.not_valid_after < now
            days_until_expiry = (cert.not_valid_after - now).days if not is_expired else 0

            return {
                'subject': subject_cn,
                'issuer': issuer_cn,
                'not_before': cert.not_valid_before.isoformat(),
                'not_after': cert.not_valid_after.isoformat(),
                'is_expired': is_expired,
                'days_until_expiry': days_until_expiry
            }

        except Exception as e:
            logger.warning(f"Failed to parse certificate: {e}")
            return None

    def _generate_certificate(self, domain: str) -> Optional[bytes]:
        """Generate self-signed certificate (for testing)"""
        try:
            # Generate private key
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )

            # Create certificate
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, domain),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "YubiHSM Test"),
                x509.NameAttribute(NameOID.COUNTRY_NAME, "US")
            ])

            cert = x509.CertificateBuilder().subject_name(
                subject
            ).issuer_name(
                issuer
            ).public_key(
                private_key.public_key()
            ).serial_number(
                x509.random_serial_number()
            ).not_valid_before(
                datetime.utcnow()
            ).not_valid_after(
                datetime.utcnow() + timedelta(days=self.cert_validity_days)
            ).add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName(domain),
                    x509.DNSName(f"*.{domain}")
                ]),
                critical=False
            ).sign(private_key, hashes.SHA256(), default_backend())

            return cert.public_bytes(serialization.Encoding.DER)

        except Exception as e:
            logger.error(f"Failed to generate certificate: {e}")
            return None

    def _find_available_cert_id(self) -> int:
        """Find next available certificate ID"""
        # Certificate IDs start at 2000
        base_id = 2000
        while True:
            try:
                self.session.get_object(base_id, 1)
                base_id += 1
            except Exception: 
                return base_id

def lambda_handler(event, context):
    """Main Lambda handler"""
    logger.info("Starting YubiHSM certificate management")

    manager = YubiHSMCertificateManager()

    try:
        if not manager.connect_hsm():
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Failed to connect to YubiHSM'})
            }

        # Check for expiring certificates
        expiring_certs = manager.check_expiring_certificates(days=30)

        # Rotate expiring certificates
        rotated_count = 0
        for cert in expiring_certs:
            if manager.rotate_certificate(cert['id'], cert['subject']):
                rotated_count += 1

        # Get certificate inventory
        certificates = manager.list_certificates()

        result = {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Certificate management completed',
                'certificates_total': len(certificates),
                'certificates_expiring': len(expiring_certs),
                'certificates_rotated': rotated_count,
                'timestamp': datetime.now().isoformat()
            })
        }

        logger.info(f"Certificate management completed: {result['body']}")
        return result

    except Exception as e:
        logger.error(f"Certificate management failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            })
        }
    finally:
        manager.disconnect_hsm()