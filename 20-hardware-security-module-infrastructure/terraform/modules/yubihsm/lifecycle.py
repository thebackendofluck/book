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
YubiHSM Lifecycle Management Lambda Function
Handles automated cleanup, rotation, and monitoring of YubiHSM objects
"""

import os
import json
import boto3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from yubihsm import YubiHsm
from yubihsm.exceptions import YubiHsmError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class YubiHSMLifecycleManager:
    """Manages YubiHSM object lifecycle operations"""

    def __init__(self):
        self.hsm_connector = os.environ.get('YUBIHSM_CONNECTOR_HOST', 'localhost:12345')
        self.auth_key_id = int(os.environ.get('YUBIHSM_AUTH_KEY_ID', '1'))
        # fail-fast: set YUBIHSM_AUTH_PASSWORD in environment before running this script
        self.auth_password = os.environ['YUBIHSM_AUTH_PASSWORD']  # raises KeyError if unset
        self.backup_bucket = os.environ.get('BACKUP_BUCKET', '')
        self.cleanup_expired = os.environ.get('CLEANUP_EXPIRED', 'true').lower() == 'true'
        self.cleanup_old_days = int(os.environ.get('CLEANUP_OLD_DAYS', '90'))

        # AWS clients
        self.s3_client = boto3.client('s3')
        self.cloudwatch = boto3.client('cloudwatch')

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

    def list_objects(self) -> List[Dict]:
        """List all objects in HSM"""
        try:
            objects = []
            # Get all object types
            for obj_type in [1, 2, 3, 4, 5, 6]:  # Certificate, Symmetric Key, etc.
                try:
                    hsm_objects = self.session.list_objects(object_type=obj_type)
                    for obj in hsm_objects:
                        objects.append({
                            'id': obj.id,
                            'type': obj_type,
                            'label': obj.label.decode('utf-8') if obj.label else '',
                            'capabilities': obj.capabilities,
                            'domains': obj.domains
                        })
                except Exception: 
                    continue
            return objects
        except Exception as e:
            logger.error(f"Failed to list objects: {e}")
            return []

    def cleanup_expired_certificates(self) -> int:
        """Remove expired certificates"""
        if not self.cleanup_expired:
            logger.info("Expired certificate cleanup disabled")
            return 0

        try:
            objects = self.list_objects()
            expired_count = 0

            for obj in objects:
                if obj['type'] == 1:  # Certificate
                    try:
                        cert_obj = self.session.get_object(obj['id'], 1)
                        cert_data = cert_obj.get()

                        # Parse certificate (simplified - would need proper cert parsing)
                        # For now, check if object is old based on label or metadata
                        if self._is_certificate_expired(cert_data):
                            self.session.delete_object(obj['id'], 1)
                            logger.info(f"Deleted expired certificate: {obj['id']}")
                            expired_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to check certificate {obj['id']}: {e}")

            return expired_count
        except Exception as e:
            logger.error(f"Failed to cleanup expired certificates: {e}")
            return 0

    def cleanup_old_objects(self) -> int:
        """Remove objects older than specified days"""
        try:
            objects = self.list_objects()
            old_count = 0
            cutoff_date = datetime.now() - timedelta(days=self.cleanup_old_days)

            for obj in objects:
                # Check if object has age metadata (simplified)
                if self._is_object_old(obj, cutoff_date):
                    try:
                        self.session.delete_object(obj['id'], obj['type'])
                        logger.info(f"Deleted old object: {obj['id']} (type: {obj['type']})")
                        old_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete object {obj['id']}: {e}")

            return old_count
        except Exception as e:
            logger.error(f"Failed to cleanup old objects: {e}")
            return 0

    def create_backup(self) -> bool:
        """Create backup of all HSM objects"""
        try:
            if not self.backup_bucket:
                logger.warning("No backup bucket configured")
                return False

            objects = self.list_objects()
            backup_data = {
                'timestamp': datetime.now().isoformat(),
                'objects': objects,
                'metadata': {
                    'hsm_connector': self.hsm_connector,
                    'total_objects': len(objects)
                }
            }

            backup_key = f"yubihsm-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

            self.s3_client.put_object(
                Bucket=self.backup_bucket,
                Key=backup_key,
                Body=json.dumps(backup_data, indent=2),
                ContentType='application/json'
            )

            logger.info(f"Created backup: {backup_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return False

    def send_metrics_to_cloudwatch(self, metrics: Dict):
        """Send metrics to CloudWatch"""
        try:
            metric_data = []
            for name, value in metrics.items():
                metric_data.append({
                    'MetricName': name,
                    'Value': value,
                    'Unit': 'Count',
                    'Timestamp': datetime.now()
                })

            self.cloudwatch.put_metric_data(
                Namespace='YubiHSM/Lifecycle',
                MetricData=metric_data
            )
        except Exception as e:
            logger.error(f"Failed to send metrics: {e}")

    def _is_certificate_expired(self, cert_data: bytes) -> bool:
        """Check if certificate is expired (simplified implementation)"""
        # This would need proper certificate parsing
        # For now, return False (no cleanup)
        return False

    def _is_object_old(self, obj: Dict, cutoff_date: datetime) -> bool:
        """Check if object is older than cutoff date (simplified)"""
        # This would need proper metadata checking
        # For now, check if label contains old date patterns
        label = obj.get('label', '')
        if 'old' in label.lower() or 'backup' in label.lower():
            return True
        return False

def lambda_handler(event, context):
    """Main Lambda handler"""
    logger.info("Starting YubiHSM lifecycle management")

    manager = YubiHSMLifecycleManager()

    try:
        if not manager.connect_hsm():
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Failed to connect to YubiHSM'})
            }

        # Perform cleanup operations
        expired_cleaned = manager.cleanup_expired_certificates()
        old_cleaned = manager.cleanup_old_objects()

        # Create backup
        backup_success = manager.create_backup()

        # Send metrics
        metrics = {
            'ExpiredCertificatesCleaned': expired_cleaned,
            'OldObjectsCleaned': old_cleaned,
            'BackupSuccess': 1 if backup_success else 0
        }
        manager.send_metrics_to_cloudwatch(metrics)

        result = {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Lifecycle management completed',
                'expired_certificates_cleaned': expired_cleaned,
                'old_objects_cleaned': old_cleaned,
                'backup_created': backup_success,
                'timestamp': datetime.now().isoformat()
            })
        }

        logger.info(f"Lifecycle management completed: {result['body']}")
        return result

    except Exception as e:
        logger.error(f"Lifecycle management failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            })
        }
    finally:
        manager.disconnect_hsm()