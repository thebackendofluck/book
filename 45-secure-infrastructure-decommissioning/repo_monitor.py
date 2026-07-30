#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 45, Secure Infrastructure Decommissioning.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Repository File Monitor for SDDS
Monitors a GitHub repository file for the trigger word "cleaner" to activate destruction sequence
"""

import os
import sys
import time
import json
import requests
import hashlib
import hmac
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, Any
import urllib.parse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/repo_monitor.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class RepositoryMonitor:
    """Monitors a GitHub repository file for trigger words"""

    def __init__(self, config_file: str = 'config/repo_monitor.json'):
        self.config_file = config_file
        self.config = self.load_config()
        self.last_content_hash = None
        self.trigger_detected = False

        # Parse repository URL
        self.parse_repository_url()

    def load_config(self) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)

            # Validate required fields
            required_fields = ['repository_url', 'trigger_word']
            for field in required_fields:
                if field not in config:
                    raise ValueError(f"Missing required configuration field: {field}")

            # Set defaults
            config.setdefault('check_interval', 60)  # seconds
            config.setdefault('signature_verification', False)
            config.setdefault('authorized_keys', [])

            logger.info(f"Configuration loaded from {self.config_file}")
            return config

        except FileNotFoundError:
            logger.error(f"Configuration file not found: {self.config_file}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in configuration file: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            sys.exit(1)

    def parse_repository_url(self):
        """Parse GitHub repository URL to extract components"""
        url = self.config['repository_url']

        # Handle GitHub blob URLs
        if 'github.com' in url and '/blob/' in url:
            parts = url.split('/')
            if len(parts) >= 7:
                self.owner = parts[3]
                self.repo = parts[4]
                self.branch = parts[6]
                self.file_path = '/'.join(parts[7:])
                self.api_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/contents/{self.file_path}?ref={self.branch}"
                logger.info(f"Parsed GitHub URL: {self.owner}/{self.repo}/{self.branch}/{self.file_path}")
            else:
                raise ValueError("Invalid GitHub blob URL format")
        else:
            raise ValueError("Only GitHub repository URLs are currently supported")

    def fetch_file_content(self) -> Optional[str]:
        """Fetch file content from GitHub API"""
        try:
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'SDDS-Repository-Monitor/1.0'
            }

            # Add GitHub token if available
            token = os.getenv('GITHUB_TOKEN')
            if token:
                headers['Authorization'] = f'token {token}'

            response = requests.get(self.api_url, headers=headers, timeout=30)

            if response.status_code == 200:
                data = response.json()
                if 'content' in data:
                    import base64
                    content = base64.b64decode(data['content']).decode('utf-8')
                    return content
                else:
                    logger.error("No content found in GitHub API response")
                    return None
            elif response.status_code == 404:
                logger.warning(f"File not found: {self.api_url}")
                return None
            elif response.status_code == 403:
                logger.warning("Rate limited by GitHub API")
                return None
            else:
                logger.error(f"GitHub API error: {response.status_code} - {response.text}")
                return None

        except requests.RequestException as e:
            logger.error(f"Network error fetching file: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching file content: {e}")
            return None

    def verify_signature(self, content: str) -> bool:
        """Verify content signature if enabled"""
        if not self.config.get('signature_verification', False):
            return True

        # Extract signature from content (assuming it's embedded)
        lines = content.strip().split('\n')
        signature_line = None
        content_without_sig = []

        for line in lines:
            if line.startswith('signature:'):
                signature_line = line.split(':', 1)[1].strip()
            else:
                content_without_sig.append(line)

        if not signature_line:
            logger.warning("Signature verification enabled but no signature found in content")
            return False

        content_to_verify = '\n'.join(content_without_sig)

        # Verify against authorized keys
        for key in self.config.get('authorized_keys', []):
            try:
                expected_sig = hmac.new(
                    key.encode(),
                    content_to_verify.encode(),
                    hashlib.sha256
                ).hexdigest()

                if hmac.compare_digest(expected_sig, signature_line):
                    logger.info("Signature verification successful")
                    return True
            except Exception as e:
                logger.error(f"Error verifying signature: {e}")
                continue

        logger.warning("Signature verification failed")
        return False

    def check_for_trigger_word(self, content: str) -> bool:
        """Check if trigger word is present in content"""
        trigger_word = self.config['trigger_word'].lower()
        content_lower = content.lower()

        if trigger_word in content_lower:
            logger.warning(f"TRIGGER WORD DETECTED: '{trigger_word}' found in repository file")
            return True

        return False

    def calculate_content_hash(self, content: str) -> str:
        """Calculate SHA-256 hash of content"""
        return hashlib.sha256(content.encode()).hexdigest()

    def has_content_changed(self, content: str) -> bool:
        """Check if content has changed since last check"""
        current_hash = self.calculate_content_hash(content)

        if self.last_content_hash != current_hash:
            self.last_content_hash = current_hash
            return True

        return False

    def activate_destruction_sequence(self, content: str):
        """Activate the destruction sequence"""
        if self.trigger_detected:
            logger.info("Destruction sequence already activated")
            return

        logger.critical("=== DESTRUCTION SEQUENCE ACTIVATION TRIGGERED ===")
        logger.critical("Trigger source: Repository file monitoring")
        logger.critical(f"Repository: {self.owner}/{self.repo}")
        logger.critical(f"File: {self.file_path}")
        logger.critical(f"Trigger word: {self.config['trigger_word']}")

        # Log the triggering content for audit
        logger.critical("Triggering content:")
        for line in content.split('\n')[:10]:  # Log first 10 lines
            logger.critical(f"  {line}")

        self.trigger_detected = True

        # Import and activate master orchestrator
        try:
            from master_orchestrator import MasterOrchestrator

            orchestrator = MasterOrchestrator()
            orchestrator.start_destruction(repo_trigger=True, trigger_content=content)

            logger.critical("Destruction sequence started successfully")

        except ImportError:
            logger.error("Could not import MasterOrchestrator. Make sure SDDS is properly installed.")
        except Exception as e:
            logger.error(f"Failed to start destruction sequence: {e}")

    def monitor_loop(self):
        """Main monitoring loop"""
        logger.info("Starting repository file monitoring...")
        logger.info(f"Monitoring: {self.config['repository_url']}")
        logger.info(f"Trigger word: {self.config['trigger_word']}")
        logger.info(f"Check interval: {self.config['check_interval']} seconds")

        check_interval = self.config['check_interval']

        while not self.trigger_detected:
            try:
                # Fetch file content
                content = self.fetch_file_content()

                if content is None:
                    logger.debug("Could not fetch file content, will retry...")
                else:
                    # Check if content changed
                    if self.has_content_changed(content):
                        logger.info("File content changed, checking for trigger...")

                        # Verify signature if enabled
                        if self.verify_signature(content):
                            # Check for trigger word
                            if self.check_for_trigger_word(content):
                                self.activate_destruction_sequence(content)
                                break  # Exit monitoring loop
                        else:
                            logger.warning("Signature verification failed, ignoring content change")
                    else:
                        logger.debug("File content unchanged")

                # Wait before next check
                time.sleep(check_interval)

            except KeyboardInterrupt:
                logger.info("Monitoring interrupted by user")
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(check_interval)

    def test_connection(self) -> bool:
        """Test connection to repository"""
        logger.info("Testing repository connection...")

        content = self.fetch_file_content()
        if content is not None:
            logger.info("✓ Repository connection successful")
            logger.info(f"✓ File size: {len(content)} characters")
            logger.info(f"✓ First few lines:")
            for line in content.split('\n')[:3]:
                logger.info(f"    {line}")
            return True
        else:
            logger.error("✗ Repository connection failed")
            return False

def main():
    import argparse

    parser = argparse.ArgumentParser(description='SDDS Repository File Monitor')
    parser.add_argument('--config', default='config/repo_monitor.json',
                       help='Configuration file path')
    parser.add_argument('--watch', action='store_true',
                       help='Start monitoring mode')
    parser.add_argument('--test', action='store_true',
                       help='Test repository connection')
    parser.add_argument('--trigger-word', help='Override trigger word')

    args = parser.parse_args()

    # Create monitor instance
    monitor = RepositoryMonitor(args.config)

    # Override trigger word if specified
    if args.trigger_word:
        monitor.config['trigger_word'] = args.trigger_word

    if args.test:
        # Test mode
        success = monitor.test_connection()
        sys.exit(0 if success else 1)

    elif args.watch:
        # Monitoring mode
        monitor.monitor_loop()

    else:
        parser.print_help()

if __name__ == '__main__':
    main()