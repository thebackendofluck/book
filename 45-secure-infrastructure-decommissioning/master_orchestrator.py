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
Master Orchestrator for Secure Data Destruction System
Coordinates all destruction modules with proper sequencing and verification
"""

import sys
import os
import json
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Callable
from enum import Enum
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/master_orchestrator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DestructionPhase(Enum):
    """Destruction phases in proper sequence"""
    DOCUMENTATION_ANALYSIS = "documentation_analysis"
    SECURITY_DEVICE_PREPARATION = "security_device_preparation"
    AUTHENTICATION_ISOLATION = "authentication_isolation"
    PARALLEL_INFRASTRUCTURE_DESTRUCTION = "parallel_infrastructure_destruction"
    NETWORK_DESTRUCTION = "network_destruction"
    VERIFICATION = "verification"
    SELF_DESTRUCT = "self_destruct"

class OrchestratorStatus(Enum):
    """Orchestrator status states"""
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

class MasterOrchestrator:
    """Master orchestrator for complete infrastructure destruction"""

    def __init__(self, config_file: str = 'config/master_config.json'):
        self.config_file = config_file
        self.config = self._load_config()
        self.status = OrchestratorStatus.IDLE
        self.current_phase = None
        self.audit_log = []
        self.phase_results = {}
        self.start_time = None
        self.end_time = None

        # Initialize components
        self.components = self._initialize_components()

        # Thread pool for parallel operations
        self.executor = ThreadPoolExecutor(max_workers=4)

        # Emergency stop flag
        self.emergency_stop = False

    def _load_config(self) -> Dict:
        """Load orchestrator configuration"""
        try:
            if Path(self.config_file).exists():
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            else:
                return self._get_default_config()
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return self._get_default_config()

    def _get_default_config(self) -> Dict:
        """Get default configuration"""
        return {
            'phases': {
                'documentation_analysis': {'timeout': 300, 'required': True},
                'security_device_preparation': {'timeout': 600, 'required': True},
                'authentication_isolation': {'timeout': 300, 'required': True},
                'parallel_infrastructure_destruction': {'timeout': 1800, 'required': True},
                'network_destruction': {'timeout': 900, 'required': True},
                'verification': {'timeout': 600, 'required': True},
                'self_destruct': {'timeout': 300, 'required': True}
            },
            'verification_required': True,
            'audit_log_file': '/var/log/sdds_audit.log',
            'emergency_stop_file': '/tmp/emergency_stop',
            'checkpoint_file': '/tmp/sdds_checkpoint.json'
        }

    def _initialize_components(self) -> Dict:
        """Initialize all destruction components"""
        components = {}

        try:
            # Import and initialize components
            from doc_parser import DocumentationParser
            components['doc_parser'] = DocumentationParser()

            from yubihsm_destroyer import YubiHSMDestroyer
            components['yubihsm_destroyer'] = YubiHSMDestroyer()

            from yubikey_revoker import YubiKeyRevoker
            components['yubikey_revoker'] = YubiKeyRevoker()

            from aws_nuke_enhanced import AWSNukeEnhanced
            components['aws_nuke'] = AWSNukeEnhanced()

            from ansible_destroyer import AnsibleDestroyer
            components['ansible_destroyer'] = AnsibleDestroyer()

            from meraki_eliminator import MerakiEliminator
            components['meraki_eliminator'] = MerakiEliminator()

            from mikrotik_zeroizer import MikroTikZeroizer
            components['mikrotik_zeroizer'] = MikroTikZeroizer()

            from sms_auth import SMSAuthenticator
            components['sms_auth'] = SMSAuthenticator()

            logger.info("All components initialized successfully")

        except ImportError as e:
            logger.error(f"Failed to import component: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")

        return components

    def start_destruction(self, sms_trigger: bool = False) -> bool:
        """Start the complete destruction sequence"""
        try:
            logger.warning("=== STARTING SECURE DATA DESTRUCTION SYSTEM ===")

            # This driver runs every leaf destruction tool in sequence and, below,
            # invokes terraform_obliterator.sh with --no-dry-run, defeating that
            # script's own DRY_RUN=true default. It must not run for real unless
            # deliberately unlocked, matching the leaf scripts.
            import os
            if (os.getenv("DRY_RUN", "true") != "false"
                    or os.getenv("I_HAVE_WRITTEN_AUTHORISATION") != "yes"):
                logger.warning("[SIMULATED] set DRY_RUN=false and "
                               "I_HAVE_WRITTEN_AUTHORISATION=yes to run for real")
                return True

            if sms_trigger and not self._verify_sms_auth():
                logger.error("SMS authentication failed")
                return False

            self.status = OrchestratorStatus.INITIALIZING
            self.start_time = datetime.now()

            # Check emergency stop
            if self._check_emergency_stop():
                logger.error("Emergency stop triggered")
                return False

            # Execute phases in sequence
            phases = [
                DestructionPhase.DOCUMENTATION_ANALYSIS,
                DestructionPhase.SECURITY_DEVICE_PREPARATION,
                DestructionPhase.AUTHENTICATION_ISOLATION,
                DestructionPhase.PARALLEL_INFRASTRUCTURE_DESTRUCTION,
                DestructionPhase.NETWORK_DESTRUCTION,
                DestructionPhase.VERIFICATION,
                DestructionPhase.SELF_DESTRUCT
            ]

            for phase in phases:
                if self.emergency_stop:
                    logger.error("Emergency stop triggered during execution")
                    self.status = OrchestratorStatus.ABORTED
                    return False

                if not self._execute_phase(phase):
                    logger.error(f"Phase {phase.value} failed")
                    self.status = OrchestratorStatus.FAILED
                    return False

                # Save checkpoint
                self._save_checkpoint()

            self.status = OrchestratorStatus.COMPLETED
            self.end_time = datetime.now()

            logger.warning("=== SECURE DATA DESTRUCTION COMPLETED SUCCESSFULLY ===")
            self._generate_final_report()
            return True

        except Exception as e:
            logger.error(f"Destruction sequence failed: {e}")
            self.status = OrchestratorStatus.FAILED
            return False

    def _verify_sms_auth(self) -> bool:
        """Verify SMS authentication for destruction trigger"""
        try:
            auth = self.components.get('sms_auth')
            if not auth:
                logger.error("SMS authentication component not available")
                return False

            # In a real implementation, this would handle the SMS flow
            # For now, assume authentication is successful
            logger.info("SMS authentication verified (simulated)")
            return True

        except Exception as e:
            logger.error(f"SMS authentication failed: {e}")
            return False

    def _execute_phase(self, phase: DestructionPhase) -> bool:
        """Execute a specific destruction phase"""
        try:
            self.current_phase = phase
            self.status = OrchestratorStatus.RUNNING

            logger.info(f"=== EXECUTING PHASE: {phase.value.upper()} ===")

            phase_config = self.config.get('phases', {}).get(phase.value, {})
            timeout = phase_config.get('timeout', 300)

            # Execute phase with timeout
            result = self._run_phase_with_timeout(phase, timeout)

            if result:
                logger.info(f"✓ Phase {phase.value} completed successfully")
                self.phase_results[phase.value] = {'status': 'success', 'timestamp': datetime.now().isoformat()}
            else:
                logger.error(f"✗ Phase {phase.value} failed")
                self.phase_results[phase.value] = {'status': 'failed', 'timestamp': datetime.now().isoformat()}

            return result

        except Exception as e:
            logger.error(f"Phase {phase.value} execution failed: {e}")
            return False

    def _run_phase_with_timeout(self, phase: DestructionPhase, timeout: int) -> bool:
        """Run a phase with timeout protection"""
        import signal

        def timeout_handler(signum, frame):
            logger.error(f"Phase {phase.value} timed out after {timeout} seconds")
            raise TimeoutError(f"Phase timeout: {phase.value}")

        # Set timeout alarm
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)

        try:
            # Execute the phase
            if phase == DestructionPhase.DOCUMENTATION_ANALYSIS:
                return self._phase_documentation_analysis()
            elif phase == DestructionPhase.SECURITY_DEVICE_PREPARATION:
                return self._phase_security_device_preparation()
            elif phase == DestructionPhase.AUTHENTICATION_ISOLATION:
                return self._phase_authentication_isolation()
            elif phase == DestructionPhase.PARALLEL_INFRASTRUCTURE_DESTRUCTION:
                return self._phase_parallel_infrastructure_destruction()
            elif phase == DestructionPhase.NETWORK_DESTRUCTION:
                return self._phase_network_destruction()
            elif phase == DestructionPhase.VERIFICATION:
                return self._phase_verification()
            elif phase == DestructionPhase.SELF_DESTRUCT:
                return self._phase_self_destruct()
            else:
                logger.error(f"Unknown phase: {phase}")
                return False

        finally:
            # Clear timeout
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

    def _phase_documentation_analysis(self) -> bool:
        """Phase 0: Documentation Analysis"""
        try:
            doc_parser = self.components.get('doc_parser')
            if not doc_parser:
                logger.error("Documentation parser not available")
                return False

            # Parse all documentation
            inventory = doc_parser.parse_all_documentation(
                '../infrastructure',
                '../meraki-dcs',
                '../yubihsm'
            )

            # Export inventory
            doc_parser.export_inventory('destruction_inventory.json')
            doc_parser.generate_report('documentation_analysis_report.md')

            logger.info("Documentation analysis completed")
            return True

        except Exception as e:
            logger.error(f"Documentation analysis failed: {e}")
            return False

    def _phase_security_device_preparation(self) -> bool:
        """Phase 1: Security Device Preparation"""
        try:
            # Export YubiHSM audit logs
            yubihsm = self.components.get('yubihsm_destroyer')
            if yubihsm:
                yubihsm.export_audit_logs('yubihsm_audit_backup.json')

            # Inventory YubiKeys
            yubikey = self.components.get('yubikey_revoker')
            if yubikey:
                yubikey.inventory_all_yubikeys()
                yubikey.export_inventory('yubikey_inventory.json')

            logger.info("Security device preparation completed")
            return True

        except Exception as e:
            logger.error(f"Security device preparation failed: {e}")
            return False

    def _phase_authentication_isolation(self) -> bool:
        """Phase 2: Authentication Isolation"""
        try:
            # This would disable all MFA/2FA systems
            # For now, just log the action
            logger.info("Authentication isolation completed (simulated)")
            return True

        except Exception as e:
            logger.error(f"Authentication isolation failed: {e}")
            return False

    def _phase_parallel_infrastructure_destruction(self) -> bool:
        """Phase 3: Parallel Infrastructure Destruction"""
        try:
            logger.info("Starting parallel infrastructure destruction")

            # Submit parallel tasks
            futures = []

            # YubiHSM/YubiKey destruction (sequential within this phase)
            futures.append(self.executor.submit(self._destroy_security_devices))

            # AWS Nuke
            aws_nuke = self.components.get('aws_nuke')
            if aws_nuke:
                futures.append(self.executor.submit(aws_nuke.run_destruction))

            # Ansible destruction
            ansible = self.components.get('ansible_destroyer')
            if ansible:
                futures.append(self.executor.submit(ansible.run_destruction))

            # Terraform obliteration (external script)
            futures.append(self.executor.submit(self._run_terraform_obliteration))

            # Wait for all to complete
            results = []
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=1800)  # 30 minute timeout
                    results.append(result)
                except Exception as e:
                    logger.error(f"Parallel task failed: {e}")
                    results.append(False)

            success = all(results)
            logger.info(f"Parallel infrastructure destruction completed: {sum(results)}/{len(results)} successful")
            return success

        except Exception as e:
            logger.error(f"Parallel infrastructure destruction failed: {e}")
            return False

    def _destroy_security_devices(self) -> bool:
        """Destroy YubiHSM and YubiKey devices"""
        try:
            # Destroy YubiHSM first
            yubihsm = self.components.get('yubihsm_destroyer')
            if yubihsm:
                yubihsm.destroy_all()

            # Then revoke YubiKeys
            yubikey = self.components.get('yubikey_revoker')
            if yubikey:
                yubikey.revoke_all_yubikeys()

            return True

        except Exception as e:
            logger.error(f"Security device destruction failed: {e}")
            return False

    def _run_terraform_obliteration(self) -> bool:
        """Run Terraform obliteration script"""
        try:
            import subprocess
            # honour the same gate as the rest of the sequence rather than forcing it
            tf_args = ['bash', 'terraform_obliterator.sh']
            if os.getenv("DRY_RUN", "true") == "false":
                tf_args.append('--no-dry-run')
            result = subprocess.run(tf_args,
                                  capture_output=True, text=True, timeout=1800)

            return result.returncode == 0

        except Exception as e:
            logger.error(f"Terraform obliteration failed: {e}")
            return False

    def _phase_network_destruction(self) -> bool:
        """Phase 4: Network Destruction"""
        try:
            logger.info("Starting network destruction")

            # Submit parallel network destruction tasks
            futures = []

            # Meraki network wipe
            meraki = self.components.get('meraki_eliminator')
            if meraki:
                futures.append(self.executor.submit(meraki.wipe_all_networks))

            # MikroTik device reset
            mikrotik = self.components.get('mikrotik_zeroizer')
            if mikrotik:
                futures.append(self.executor.submit(mikrotik.factory_reset_all))

            # Wait for completion
            results = []
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=900)  # 15 minute timeout
                    results.append(result)
                except Exception as e:
                    logger.error(f"Network destruction task failed: {e}")
                    results.append(False)

            success = all(results)
            logger.info(f"Network destruction completed: {sum(results)}/{len(results)} successful")
            return success

        except Exception as e:
            logger.error(f"Network destruction failed: {e}")
            return False

    def _phase_verification(self) -> bool:
        """Phase 5: Verification"""
        try:
            logger.info("Starting comprehensive verification")

            # Run verification for each component
            verifications = []

            # AWS verification
            aws_nuke = self.components.get('aws_nuke')
            if aws_nuke:
                verifications.append(aws_nuke.verify_destruction())

            # Ansible verification
            ansible = self.components.get('ansible_destroyer')
            if ansible:
                verifications.append(ansible.verify_destruction())

            # Meraki verification
            meraki = self.components.get('meraki_eliminator')
            if meraki:
                verifications.append(meraki.verify_destruction())

            # MikroTik verification
            mikrotik = self.components.get('mikrotik_zeroizer')
            if mikrotik:
                verifications.append(mikrotik.verify_reset())

            success = all(verifications)
            logger.info(f"Verification completed: {sum(verifications)}/{len(verifications)} passed")
            return success

        except Exception as e:
            logger.error(f"Verification failed: {e}")
            return False

    def _phase_self_destruct(self) -> bool:
        """Phase 6: Self-Destruct"""
        try:
            logger.warning("Initiating SEN500 self-destruction")

            # This would trigger SEN500 self-destruction
            # For now, just log the action
            logger.info("SEN500 self-destruction completed (simulated)")
            return True

        except Exception as e:
            logger.error(f"Self-destruction failed: {e}")
            return False

    def emergency_abort(self, reason: str = "user_request") -> bool:
        """Emergency abort of destruction sequence"""
        try:
            logger.warning(f"EMERGENCY ABORT TRIGGERED: {reason}")

            self.emergency_stop = True
            self.status = OrchestratorStatus.ABORTED

            # Create emergency stop file
            Path(self.config.get('emergency_stop_file', '/tmp/emergency_stop')).touch()

            # Log abort
            self._audit_event("EMERGENCY_ABORT", f"Reason: {reason}")

            logger.warning("Destruction sequence aborted")
            return True

        except Exception as e:
            logger.error(f"Emergency abort failed: {e}")
            return False

    def get_status(self) -> Dict:
        """Get current orchestrator status"""
        return {
            'status': self.status.value,
            'current_phase': self.current_phase.value if self.current_phase else None,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'phase_results': self.phase_results,
            'emergency_stop': self.emergency_stop
        }

    def _check_emergency_stop(self) -> bool:
        """Check for emergency stop signal"""
        emergency_file = Path(self.config.get('emergency_stop_file', '/tmp/emergency_stop'))
        return emergency_file.exists()

    def _save_checkpoint(self):
        """Save current progress checkpoint"""
        try:
            checkpoint = {
                'timestamp': datetime.now().isoformat(),
                'status': self.status.value,
                'current_phase': self.current_phase.value if self.current_phase else None,
                'phase_results': self.phase_results,
                'start_time': self.start_time.isoformat() if self.start_time else None
            }

            checkpoint_file = self.config.get('checkpoint_file', '/tmp/sdds_checkpoint.json')
            with open(checkpoint_file, 'w') as f:
                json.dump(checkpoint, f, indent=2)

        except Exception as e:
            logger.warning(f"Failed to save checkpoint: {e}")

    def _audit_event(self, action: str, details: str):
        """Log audit event"""
        timestamp = datetime.now().isoformat()
        event = {
            'timestamp': timestamp,
            'action': action,
            'details': details,
            'component': 'Orchestrator'
        }
        self.audit_log.append(event)
        logger.info(f"AUDIT: {action} - {details}")

    def _generate_final_report(self):
        """Generate final destruction report"""
        try:
            report = {
                'destruction_completed_at': datetime.now().isoformat(),
                'total_duration': str(self.end_time - self.start_time) if self.end_time and self.start_time else None,
                'final_status': self.status.value,
                'phase_results': self.phase_results,
                'audit_log': self.audit_log,
                'components_used': list(self.components.keys())
            }

            with open('final_destruction_report.json', 'w') as f:
                json.dump(report, f, indent=2)

            logger.info("Final destruction report generated")

        except Exception as e:
            logger.error(f"Failed to generate final report: {e}")

def main():
    """CLI interface for master orchestrator"""
    import argparse

    parser = argparse.ArgumentParser(description='Master Orchestrator for Secure Data Destruction System')
    parser.add_argument('--start', action='store_true', help='Start destruction sequence')
    parser.add_argument('--sms-trigger', action='store_true', help='Require SMS authentication')
    parser.add_argument('--status', action='store_true', help='Show current status')
    parser.add_argument('--abort', help='Emergency abort with reason')
    parser.add_argument('--checkpoint', help='Load checkpoint file')

    args = parser.parse_args()

    try:
        orchestrator = MasterOrchestrator()

        if args.start:
            success = orchestrator.start_destruction(sms_trigger=args.sms_trigger)
            sys.exit(0 if success else 1)

        elif args.status:
            status = orchestrator.get_status()
            print("=== SDDS Orchestrator Status ===")
            print(f"Status: {status['status']}")
            print(f"Current Phase: {status['current_phase']}")
            print(f"Start Time: {status['start_time']}")
            print(f"Emergency Stop: {status['emergency_stop']}")

            if status['phase_results']:
                print("\nPhase Results:")
                for phase, result in status['phase_results'].items():
                    print(f"  {phase}: {result['status']} ({result['timestamp']})")

        elif args.abort:
            success = orchestrator.emergency_abort(args.abort)
            sys.exit(0 if success else 1)

        elif args.checkpoint:
            # Load checkpoint (not implemented yet)
            print("Checkpoint loading not implemented")

    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()