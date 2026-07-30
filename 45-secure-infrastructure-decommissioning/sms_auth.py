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
SMS Authentication System for Secure Data Destruction System
Handles SMS-based trigger authentication with multi-factor verification
"""

import sys
import os
import json
import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

try:
    from twilio.rest import Client  # type: ignore[unresolved-import]
    from twilio.base.exceptions import TwilioException  # type: ignore[unresolved-import]
except ImportError:
    print("Error: twilio library not found. Install with: pip install twilio")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/sms_auth.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class SMSAuthenticator:
    """SMS-based authentication system for destruction triggers"""

    def __init__(self, config_file: str = 'config/sms_auth.json'):
        self.config_file = config_file
        self.config = self._load_config()
        self.twilio_client = None
        self.auth_sessions = {}
        self.audit_log = []

        # Initialize Twilio client
        if self.config.get('twilio', {}).get('account_sid'):
            self.twilio_client = Client(
                self.config['twilio']['account_sid'],
                self.config['twilio']['auth_token']
            )

    def _load_config(self) -> Dict:
        """Load SMS authentication configuration"""
        try:
            if Path(self.config_file).exists():
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            else:
                logger.warning(f"Config file not found: {self.config_file}, using defaults")
                return self._get_default_config()
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return self._get_default_config()

    def _get_default_config(self) -> Dict:
        """Get default configuration"""
        return {
            'twilio': {
                'account_sid': os.getenv('TWILIO_ACCOUNT_SID', ''),
                'auth_token': os.getenv('TWILIO_AUTH_TOKEN', ''),
                'from_number': os.getenv('TWILIO_FROM_NUMBER', ''),
                'service_sid': os.getenv('TWILIO_SERVICE_SID', '')
            },
            'authorized_numbers': [],
            'challenge_timeout': 300,  # 5 minutes
            'max_attempts': 3,
            'require_pin': True,
            'pin_length': 6,
            'hmac_secret': os.getenv('SMS_HMAC_SECRET', 'default-secret-change-me')
        }

    def add_authorized_number(self, phone_number: str, name: Optional[str] = None) -> bool:
        """Add an authorized phone number"""
        try:
            if phone_number not in [num['number'] for num in self.config.get('authorized_numbers', [])]:
                authorized_entry = {
                    'number': phone_number,
                    'name': name or 'Unknown',
                    'added_at': datetime.now().isoformat(),
                    'enabled': True
                }
                self.config.setdefault('authorized_numbers', []).append(authorized_entry)
                self._save_config()
                logger.info(f"Added authorized number: {phone_number}")
                return True
            else:
                logger.warning(f"Number already authorized: {phone_number}")
                return False
        except Exception as e:
            logger.error(f"Failed to add authorized number: {e}")
            return False

    def remove_authorized_number(self, phone_number: str) -> bool:
        """Remove an authorized phone number"""
        try:
            authorized_numbers = self.config.get('authorized_numbers', [])
            original_count = len(authorized_numbers)

            self.config['authorized_numbers'] = [
                num for num in authorized_numbers
                if num['number'] != phone_number
            ]

            if len(self.config['authorized_numbers']) < original_count:
                self._save_config()
                logger.info(f"Removed authorized number: {phone_number}")
                return True
            else:
                logger.warning(f"Number not found: {phone_number}")
                return False
        except Exception as e:
            logger.error(f"Failed to remove authorized number: {e}")
            return False

    def is_authorized(self, phone_number: str) -> bool:
        """Check if a phone number is authorized"""
        authorized_numbers = self.config.get('authorized_numbers', [])
        return any(
            num['number'] == phone_number and num.get('enabled', True)
            for num in authorized_numbers
        )

    def send_challenge(self, phone_number: str) -> Optional[str]:
        """Send authentication challenge via SMS"""
        try:
            if not self.is_authorized(phone_number):
                logger.warning(f"Unauthorized number attempted challenge: {phone_number}")
                return None

            if not self.twilio_client:
                logger.error("Twilio client not configured")
                return None

            # Generate challenge code
            challenge_code = self._generate_challenge_code()
            session_id = self._create_auth_session(phone_number, challenge_code)

            # Send SMS
            message_body = self._get_challenge_message(challenge_code)

            try:
                message = self.twilio_client.messages.create(
                    body=message_body,
                    from_=self.config['twilio']['from_number'],
                    to=phone_number
                )
                logger.info(f"Challenge sent to {phone_number}, SID: {message.sid}")
                self._audit_event("CHALLENGE_SENT", f"Phone: {phone_number}, Session: {session_id}")
                return session_id

            except TwilioException as e:
                logger.error(f"Failed to send SMS to {phone_number}: {e}")
                return None

        except Exception as e:
            logger.error(f"Failed to send challenge: {e}")
            return None

    def _generate_challenge_code(self) -> str:
        """Generate a secure challenge code"""
        if self.config.get('require_pin', True):
            # Generate numeric PIN
            pin_length = self.config.get('pin_length', 6)
            import secrets
            return ''.join(secrets.choice('0123456789') for _ in range(pin_length))
        else:
            # Generate alphanumeric code
            import secrets
            return secrets.token_hex(4).upper()

    def _create_auth_session(self, phone_number: str, challenge_code: str) -> str:
        """Create authentication session"""
        import uuid
        session_id = str(uuid.uuid4())

        self.auth_sessions[session_id] = {
            'phone_number': phone_number,
            'challenge_code': challenge_code,
            'created_at': datetime.now(),
            'attempts': 0,
            'verified': False
        }

        # Clean up expired sessions
        self._cleanup_expired_sessions()

        return session_id

    def _cleanup_expired_sessions(self):
        """Clean up expired authentication sessions"""
        timeout = timedelta(seconds=self.config.get('challenge_timeout', 300))
        now = datetime.now()

        expired_sessions = [
            session_id for session_id, session in self.auth_sessions.items()
            if now - session['created_at'] > timeout
        ]

        for session_id in expired_sessions:
            del self.auth_sessions[session_id]
            logger.info(f"Cleaned up expired session: {session_id}")

    def _get_challenge_message(self, challenge_code: str) -> str:
        """Get the challenge message text"""
        return f"""SECURE DESTRUCTION SYSTEM

Your authentication code is: {challenge_code}

This code will expire in {self.config.get('challenge_timeout', 300) // 60} minutes.

If you did not request this, please ignore this message.
"""

    def verify_challenge(self, session_id: str, provided_code: str) -> bool:
        """Verify the challenge response"""
        try:
            if session_id not in self.auth_sessions:
                logger.warning(f"Invalid session ID: {session_id}")
                return False

            session = self.auth_sessions[session_id]
            max_attempts = self.config.get('max_attempts', 3)

            # Check attempts
            if session['attempts'] >= max_attempts:
                logger.warning(f"Max attempts exceeded for session {session_id}")
                self._audit_event("VERIFICATION_FAILED", f"Session: {session_id}, Reason: Max attempts exceeded")
                return False

            session['attempts'] += 1

            # Verify code
            if provided_code == session['challenge_code']:
                session['verified'] = True
                session['verified_at'] = datetime.now()
                logger.info(f"Challenge verified for session {session_id}")
                self._audit_event("VERIFICATION_SUCCESS", f"Session: {session_id}")
                return True
            else:
                logger.warning(f"Invalid challenge code for session {session_id}")
                self._audit_event("VERIFICATION_FAILED", f"Session: {session_id}, Reason: Invalid code")
                return False

        except Exception as e:
            logger.error(f"Challenge verification failed: {e}")
            return False

    def authenticate_destruction_trigger(self, phone_number: str, provided_code: str) -> Tuple[bool, Optional[str]]:
        """Complete authentication flow for destruction trigger"""
        try:
            # Find active session for this phone number
            active_session = None
            for session_id, session in self.auth_sessions.items():
                if (session['phone_number'] == phone_number and
                    not session.get('verified', False)):
                    active_session = session_id
                    break

            if not active_session:
                logger.warning(f"No active session found for {phone_number}")
                return False, "No active authentication session"

            # Verify the challenge
            if self.verify_challenge(active_session, provided_code):
                # Generate destruction token
                destruction_token = self._generate_destruction_token(phone_number, active_session)
                self._audit_event("DESTRUCTION_AUTHORIZED", f"Phone: {phone_number}, Token: {destruction_token[:8]}...")
                return True, destruction_token
            else:
                return False, "Authentication failed"

        except Exception as e:
            logger.error(f"Destruction trigger authentication failed: {e}")
            return False, "Authentication error"

    def _generate_destruction_token(self, phone_number: str, session_id: str) -> str:
        """Generate a secure destruction authorization token"""
        import secrets

        # Create token payload
        payload = {
            'phone_number': phone_number,
            'session_id': session_id,
            'timestamp': datetime.now().isoformat(),
            'random': secrets.token_hex(16)
        }

        # Create HMAC signature
        secret = self.config.get('hmac_secret', 'default-secret')
        message = json.dumps(payload, sort_keys=True)
        signature = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        # Combine payload and signature
        token_data = {
            'payload': payload,
            'signature': signature
        }

        return json.dumps(token_data)

    def verify_destruction_token(self, token: str) -> bool:
        """Verify a destruction authorization token"""
        try:
            token_data = json.loads(token)

            # Verify signature
            secret = self.config.get('hmac_secret', 'default-secret')
            payload_str = json.dumps(token_data['payload'], sort_keys=True)
            expected_signature = hmac.new(
                secret.encode(),
                payload_str.encode(),
                hashlib.sha256
            ).hexdigest()

            if token_data['signature'] != expected_signature:
                logger.warning("Invalid token signature")
                return False

            # Check timestamp (tokens expire after 1 hour)
            payload = token_data['payload']
            token_time = datetime.fromisoformat(payload['timestamp'])
            if datetime.now() - token_time > timedelta(hours=1):
                logger.warning("Token expired")
                return False

            logger.info("Destruction token verified successfully")
            return True

        except Exception as e:
            logger.error(f"Token verification failed: {e}")
            return False

    def _save_config(self):
        """Save configuration to file"""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def _audit_event(self, action: str, details: str):
        """Log audit event"""
        timestamp = datetime.now().isoformat()
        event = {
            'timestamp': timestamp,
            'action': action,
            'details': details,
            'component': 'SMS_Auth'
        }
        self.audit_log.append(event)
        logger.info(f"AUDIT: {action} - {details}")

    def get_audit_log(self) -> List[Dict]:
        """Get the audit log"""
        return self.audit_log.copy()

    def get_status(self) -> Dict:
        """Get authentication system status"""
        return {
            'twilio_configured': bool(self.twilio_client),
            'authorized_numbers': len(self.config.get('authorized_numbers', [])),
            'active_sessions': len(self.auth_sessions),
            'challenge_timeout': self.config.get('challenge_timeout', 300),
            'max_attempts': self.config.get('max_attempts', 3)
        }

def main():
    """CLI interface for SMS authentication system"""
    import argparse

    parser = argparse.ArgumentParser(description='SMS Authentication System for SDDS')
    parser.add_argument('--config', default='config/sms_auth.json', help='Configuration file')
    parser.add_argument('--add-number', help='Add authorized phone number')
    parser.add_argument('--remove-number', help='Remove authorized phone number')
    parser.add_argument('--send-challenge', help='Send challenge to phone number')
    parser.add_argument('--verify-challenge', nargs=2, metavar=('SESSION_ID', 'CODE'),
                       help='Verify challenge response')
    parser.add_argument('--authenticate', nargs=2, metavar=('PHONE', 'CODE'),
                       help='Complete authentication for destruction trigger')
    parser.add_argument('--verify-token', help='Verify destruction token')
    parser.add_argument('--status', action='store_true', help='Show system status')

    args = parser.parse_args()

    try:
        auth = SMSAuthenticator(args.config)

        if args.add_number:
            name = input("Enter name for this number (optional): ").strip() or None
            if auth.add_authorized_number(args.add_number, name):
                print(f"✓ Added authorized number: {args.add_number}")
            else:
                print(f"✗ Failed to add number: {args.add_number}")
                sys.exit(1)

        elif args.remove_number:
            if auth.remove_authorized_number(args.remove_number):
                print(f"✓ Removed authorized number: {args.remove_number}")
            else:
                print(f"✗ Failed to remove number: {args.remove_number}")
                sys.exit(1)

        elif args.send_challenge:
            session_id = auth.send_challenge(args.send_challenge)
            if session_id:
                print(f"✓ Challenge sent. Session ID: {session_id}")
            else:
                print("✗ Failed to send challenge")
                sys.exit(1)

        elif args.verify_challenge:
            session_id, code = args.verify_challenge
            if auth.verify_challenge(session_id, code):
                print("✓ Challenge verified successfully")
            else:
                print("✗ Challenge verification failed")
                sys.exit(1)

        elif args.authenticate:
            phone, code = args.authenticate
            success, result = auth.authenticate_destruction_trigger(phone, code)
            if success:
                print("✓ Authentication successful")
                print(f"Destruction token: {result}")
            else:
                print(f"✗ Authentication failed: {result}")
                sys.exit(1)

        elif args.verify_token:
            if auth.verify_destruction_token(args.verify_token):
                print("✓ Token verified successfully")
            else:
                print("✗ Token verification failed")
                sys.exit(1)

        elif args.status:
            status = auth.get_status()
            print("=== SMS Authentication Status ===")
            print(f"Twilio Configured: {status['twilio_configured']}")
            print(f"Authorized Numbers: {status['authorized_numbers']}")
            print(f"Active Sessions: {status['active_sessions']}")
            print(f"Challenge Timeout: {status['challenge_timeout']}s")
            print(f"Max Attempts: {status['max_attempts']}")

        # Print recent audit events
        audit_log = auth.get_audit_log()
        if audit_log:
            print("\n=== Recent Audit Events ===")
            for event in audit_log[-5:]:
                print(f"{event['timestamp']} | {event['action']} | {event['details']}")

    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()