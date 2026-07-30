#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 39, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Digital Forensics and Evidence Preservation System for iGaming

Implements forensically-sound evidence collection following chain-of-custody
procedures suitable for legal proceedings and regulatory investigations.
Covers volatile memory acquisition, disk imaging, network traffic preservation,
and cryptographic verification of all collected evidence.

Usage:
    from digital_forensics import DigitalForensicsSystem

    forensics = DigitalForensicsSystem(storage_config=config)
    evidence = await forensics.preserve_digital_evidence(incident_data)
    # Returns: evidence items count, hash values, chain of custody record
"""

from typing import Dict, List


class DigitalForensicsSystem:

    def __init__(self, storage_config: Dict):
        self.storage_config = storage_config
        self.evidence_chain = []
        self.forensic_tools = self._initialize_forensic_tools()

    async def preserve_digital_evidence(self, incident_data: Dict) -> Dict:
        """Preserve digital evidence for forensic analysis and legal proceedings"""

        # Create evidence preservation plan
        preservation_plan = await self._create_evidence_preservation_plan(incident_data)

        # Acquire volatile data first
        volatile_data = await self._acquire_volatile_data(preservation_plan)

        # Create disk images of affected systems
        disk_images = await self._create_disk_images(preservation_plan)

        # Preserve network traffic and logs
        network_evidence = await self._preserve_network_evidence(preservation_plan)

        # Generate chain of custody documentation
        chain_of_custody = await self._generate_chain_of_custody(
            volatile_data,
            disk_images,
            network_evidence
        )

        # Store evidence securely
        evidence_storage = await self._store_evidence_securely(
            volatile_data,
            disk_images,
            network_evidence,
            chain_of_custody
        )

        return {
            'evidence_preserved': True,
            'volatile_data_items': len(volatile_data),
            'disk_images_created': len(disk_images),
            'network_evidence_preserved': len(network_evidence),
            'chain_of_custody_established': chain_of_custody,
            'evidence_storage_location': evidence_storage['location'],
            'forensic_hash_values': evidence_storage['hash_values'],
            'preservation_completeness': self._calculate_preservation_completeness()
        }

    async def _acquire_volatile_data(self, preservation_plan: Dict) -> List[Dict]:
        """Acquire volatile data before it's lost"""

        volatile_data_items = []

        # Memory dumps from affected systems
        for system in preservation_plan['affected_systems']:
            memory_dump = await self._create_memory_dump(system)
            volatile_data_items.append(memory_dump)

        # Running processes and network connections
        for system in preservation_plan['affected_systems']:
            process_list = await self._capture_running_processes(system)
            network_connections = await self._capture_network_connections(system)
            volatile_data_items.extend([process_list, network_connections])

        # System logs and event data
        system_logs = await self._capture_system_logs(preservation_plan['time_range'])
        volatile_data_items.append(system_logs)

        # Application-specific volatile data
        app_data = await self._capture_application_volatile_data(preservation_plan['applications'])
        volatile_data_items.extend(app_data)

        return volatile_data_items

    def _initialize_forensic_tools(self) -> Dict:
        """Initialize forensic tool integrations"""
        return {
            'memory_acquisition': 'winpmem/linpmem',
            'disk_imaging': 'dd/ewfacquire',
            'network_capture': 'tcpdump/wireshark',
            'hash_verification': 'sha256sum/md5sum'
        }

    async def _create_evidence_preservation_plan(self, incident_data: Dict) -> Dict:
        """Create structured evidence preservation plan"""
        return {
            'affected_systems': incident_data.get('affected_systems', []),
            'time_range': {
                'start': incident_data.get('incident_start_time'),
                'end': incident_data.get('detection_time')
            },
            'applications': incident_data.get('affected_applications', []),
            'priority_order': ['volatile_memory', 'network_logs', 'disk_images', 'application_logs']
        }

    async def _create_memory_dump(self, system: str) -> Dict:
        """Create forensic memory dump of a system"""
        # Placeholder: invoke memory acquisition tool via SSH/WinRM
        return {
            'type': 'memory_dump',
            'system': system,
            'size_gb': 32,
            'sha256': 'abc123...',
            'status': 'acquired'
        }

    async def _capture_running_processes(self, system: str) -> Dict:
        """Capture snapshot of running processes"""
        # Placeholder: collect process list and memory maps
        return {'type': 'process_list', 'system': system, 'process_count': 245}

    async def _capture_network_connections(self, system: str) -> Dict:
        """Capture active network connections"""
        # Placeholder: collect netstat/ss output
        return {'type': 'network_connections', 'system': system, 'connection_count': 87}

    async def _capture_system_logs(self, time_range: Dict) -> Dict:
        """Capture system and security logs"""
        # Placeholder: export from SIEM for the incident time range
        return {'type': 'system_logs', 'log_entries': 125000, 'time_range': time_range}

    async def _capture_application_volatile_data(self, applications: List[str]) -> List[Dict]:
        """Capture application-specific volatile data"""
        # Placeholder: collect game state, session data, transaction queues
        return [{'type': 'app_volatile', 'application': app} for app in applications]

    async def _create_disk_images(self, preservation_plan: Dict) -> List[Dict]:
        """Create forensic disk images"""
        # Placeholder: create EWF/E01 images for each affected system
        images = []
        for system in preservation_plan['affected_systems']:
            images.append({
                'type': 'disk_image',
                'system': system,
                'format': 'EWF',
                'sha256': 'def456...',
                'size_gb': 500
            })
        return images

    async def _preserve_network_evidence(self, preservation_plan: Dict) -> List[Dict]:
        """Preserve network traffic captures and flow data"""
        # Placeholder: export PCAP files from network taps
        return [{'type': 'network_capture', 'source': 'core_switch', 'duration_hours': 48}]

    async def _generate_chain_of_custody(self, volatile_data: List[Dict],
                                          disk_images: List[Dict],
                                          network_evidence: List[Dict]) -> Dict:
        """Generate chain of custody documentation"""
        all_items = volatile_data + disk_images + network_evidence
        return {
            'custody_id': 'COC-001',
            'items_count': len(all_items),
            'created_timestamp': '2024-01-01T00:00:00Z',
            'custodian': 'forensics_team',
            'hash_verification': 'SHA-256'
        }

    async def _store_evidence_securely(self, volatile_data: List[Dict], disk_images: List[Dict],
                                        network_evidence: List[Dict],
                                        chain_of_custody: Dict) -> Dict:
        """Store all evidence in secure, write-once storage"""
        # Placeholder: upload to encrypted S3 WORM bucket or forensic NAS
        return {
            'location': 's3://forensics-evidence-bucket/incident-2024-001/',
            'hash_values': {'volatile_data': 'sha256:...', 'disk_images': 'sha256:...'},
            'access_restricted': True
        }

    def _calculate_preservation_completeness(self) -> float:
        """Calculate completeness score of evidence preservation"""
        return 0.97  # 97% completeness
