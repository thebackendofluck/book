#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Hardware Data Collector for ML Training - Chapter 23: Operational Playbooks

Collects hardware metrics over time for training predictive failure models.
Monitors servers, NVMe drives, and traditional disks using psutil and smartctl.
Tracks component age, CPU/memory/disk usage, temperature, wear levels, and error
counts. Labels data with failure events for supervised learning (failed_in_30_days).

Usage:
    python data_collector.py --interval 300 --duration 24
    python data_collector.py --add-failure server main 2024-01-15T10:30:00

Part of the iGaming Platform Engineering book.
"""

import csv
import json
import time
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
import psutil
import pandas as pd


class HardwareDataCollector:
    def __init__(self, collection_interval: int = 300, max_age_days: int = 365):
        """
        Initialize data collector
        :param collection_interval: Seconds between collections
        :param max_age_days: Maximum age to consider for failure prediction
        """
        self.collection_interval = collection_interval
        self.max_age_days = max_age_days
        self.data_file = f"hardware_training_data_{datetime.now().strftime('%Y%m%d')}.csv"
        self.failure_events_file = "failure_events.json"

        # Initialize CSV with headers
        with open(self.data_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'component_type', 'component_id', 'manufacture_date',
                'age_days', 'cpu_usage', 'memory_usage', 'disk_usage', 'network_errors',
                'temperature', 'wear_level', 'error_count', 'reallocated_sectors',
                'failed_in_30_days'
            ])

        # Load existing failure events
        self.failure_events = self._load_failure_events()

    def _load_failure_events(self) -> Dict[str, List[datetime]]:
        """Load historical failure events for labeling"""
        try:
            with open(self.failure_events_file, 'r') as f:
                data = json.load(f)
                # Convert string dates back to datetime
                return {k: [datetime.fromisoformat(d) for d in v] for k, v in data.items()}
        except FileNotFoundError:
            return {}

    def _save_failure_events(self):
        """Save failure events to disk"""
        data = {k: [d.isoformat() for d in v] for k, v in self.failure_events.items()}
        with open(self.failure_events_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _get_manufacture_date(self, component_type: str, component_id: str) -> str:
        """Get manufacture date for component"""
        try:
            if component_type == 'server':
                result = subprocess.run(['dmidecode', '-t', 'bios'],
                                      capture_output=True, text=True, timeout=10)
                for line in result.stdout.split('\n'):
                    if 'Release Date' in line:
                        return line.split(':')[1].strip()
            elif component_type in ['nvme', 'disk']:
                result = subprocess.run(['smartctl', '-i', f'/dev/{component_id}'],
                                      capture_output=True, text=True, timeout=10)
                for line in result.stdout.split('\n'):
                    if 'Date' in line and 'First' in line:
                        return line.split(':')[1].strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass
        return 'unknown'

    def _calculate_age_days(self, manufacture_date: str) -> int:
        """Calculate age in days from manufacture date"""
        if manufacture_date == 'unknown':
            return -1
        try:
            mfg_date = datetime.strptime(manufacture_date, '%m/%d/%Y')
            return (datetime.now() - mfg_date).days
        except ValueError:
            return -1

    def _collect_server_metrics(self) -> List[Dict[str, Any]]:
        """Collect server-level metrics"""
        metrics = []

        # CPU and memory
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        # Temperature (if available)
        temperature = 0
        try:
            temps = psutil.sensors_temperatures()  # ty:ignore[possibly-missing-attribute]
            if temps:
                for sensor_name, sensor_readings in temps.items():
                    if sensor_readings:
                        temperature = max(temperature, sensor_readings[0].current)
        except Exception:
            temperature = 0

        manufacture_date = self._get_manufacture_date('server', 'main')
        age_days = self._calculate_age_days(manufacture_date)

        # Check if this component failed in next 30 days
        component_key = f"server:main"
        failed_in_30_days = self._will_fail_in_window(component_key, 30)

        metrics.append({
            'timestamp': datetime.now().isoformat(),
            'component_type': 'server',
            'component_id': 'main',
            'manufacture_date': manufacture_date,
            'age_days': age_days,
            'cpu_usage': cpu_percent,
            'memory_usage': memory.percent,
            'disk_usage': disk.percent,
            'network_errors': 0,  # Would need more complex network monitoring
            'temperature': temperature,
            'wear_level': 0,  # Not applicable for servers
            'error_count': 0,  # Would aggregate from logs
            'reallocated_sectors': 0,  # Not applicable
            'failed_in_30_days': failed_in_30_days
        })

        return metrics

    def _collect_disk_metrics(self) -> List[Dict[str, Any]]:
        """Collect disk metrics for NVMe and traditional drives"""
        metrics = []

        # Get all disk devices
        try:
            result = subprocess.run(['lsblk', '-d', '-n', '-o', 'NAME'],
                                  capture_output=True, text=True, timeout=10)
            disks = result.stdout.strip().split('\n')
        except Exception:
            disks = []

        for disk in disks:
            if not disk.startswith(('sd', 'nvme')):
                continue

            device_path = f'/dev/{disk}'
            manufacture_date = self._get_manufacture_date('disk', disk)
            age_days = self._calculate_age_days(manufacture_date)

            # Get SMART data
            wear_level = 0
            error_count = 0
            reallocated_sectors = 0

            try:
                if disk.startswith('nvme'):
                    # NVMe specific
                    result = subprocess.run(['nvme', 'smart-log', device_path],
                                          capture_output=True, text=True, timeout=10)
                    for line in result.stdout.split('\n'):
                        if 'percentage_used' in line:
                            wear_level = int(line.split(':')[1].strip().split()[0])
                        elif 'num_err_log_entries' in line:
                            error_count = int(line.split(':')[1].strip())
                else:
                    # Traditional disk
                    result = subprocess.run(['smartctl', '-A', device_path],
                                          capture_output=True, text=True, timeout=10)
                    for line in result.stdout.split('\n'):
                        if 'Reallocated_Sector_Ct' in line:
                            reallocated_sectors = int(line.split()[-1])
                        elif 'Uncorrectable_Error_Ct' in line:
                            error_count = int(line.split()[-1])
            except Exception:
                pass

            component_key = f"disk:{disk}"
            failed_in_30_days = self._will_fail_in_window(component_key, 30)

            metrics.append({
                'timestamp': datetime.now().isoformat(),
                'component_type': 'disk',
                'component_id': disk,
                'manufacture_date': manufacture_date,
                'age_days': age_days,
                'cpu_usage': 0,
                'memory_usage': 0,
                'disk_usage': 0,
                'network_errors': 0,
                'temperature': 0,  # Would need additional sensors
                'wear_level': wear_level,
                'error_count': error_count,
                'reallocated_sectors': reallocated_sectors,
                'failed_in_30_days': failed_in_30_days
            })

        return metrics

    def _will_fail_in_window(self, component_key: str, days: int) -> int:
        """Check if component will fail within specified days"""
        if component_key not in self.failure_events:
            return 0

        now = datetime.now()
        future_window = now + timedelta(days=days)

        for failure_date in self.failure_events[component_key]:
            if now <= failure_date <= future_window:
                return 1
        return 0

    def collect_data_point(self):
        """Collect one round of metrics from all components"""
        all_metrics = []

        # Collect metrics from different component types
        all_metrics.extend(self._collect_server_metrics())
        all_metrics.extend(self._collect_disk_metrics())
        # Add network and firewall metrics as needed

        # Write to CSV
        with open(self.data_file, 'a', newline='') as f:
            writer = csv.writer(f)
            for metric in all_metrics:
                writer.writerow([
                    metric['timestamp'],
                    metric['component_type'],
                    metric['component_id'],
                    metric['manufacture_date'],
                    metric['age_days'],
                    metric['cpu_usage'],
                    metric['memory_usage'],
                    metric['disk_usage'],
                    metric['network_errors'],
                    metric['temperature'],
                    metric['wear_level'],
                    metric['error_count'],
                    metric['reallocated_sectors'],
                    metric['failed_in_30_days']
                ])

        print(f"Collected {len(all_metrics)} metrics at {datetime.now()}")

    def run_continuous_collection(self, duration_hours: int = 24):
        """Run continuous data collection for specified duration"""
        end_time = datetime.now() + timedelta(hours=duration_hours)
        collection_count = 0

        print(f"Starting continuous data collection for {duration_hours} hours...")
        print(f"Collection interval: {self.collection_interval} seconds")
        print(f"Data will be saved to: {self.data_file}")

        while datetime.now() < end_time:
            try:
                self.collect_data_point()
                collection_count += 1
                time.sleep(self.collection_interval)
            except KeyboardInterrupt:
                print("\nCollection interrupted by user")
                break
            except Exception as e:
                print(f"Error during collection: {e}")
                time.sleep(self.collection_interval)

        print(f"Collection completed. Total data points: {collection_count}")
        self._save_failure_events()

    def add_failure_event(self, component_type: str, component_id: str, failure_date: Optional[str] = None):
        """Add a failure event for labeling training data"""
        if failure_date is None:
            parsed_date = datetime.now()
        else:
            parsed_date = datetime.fromisoformat(failure_date)

        component_key = f"{component_type}:{component_id}"
        if component_key not in self.failure_events:
            self.failure_events[component_key] = []

        self.failure_events[component_key].append(parsed_date)
        self.failure_events[component_key].sort()
        self._save_failure_events()
        print(f"Added failure event for {component_key} on {failure_date}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Hardware Data Collector for ML Training')
    parser.add_argument('--interval', type=int, default=300,
                       help='Collection interval in seconds (default: 300)')
    parser.add_argument('--duration', type=int, default=24,
                       help='Collection duration in hours (default: 24)')
    parser.add_argument('--add-failure', nargs=3,
                       metavar=('TYPE', 'ID', 'DATE'),
                       help='Add failure event: TYPE ID DATE(ISO format)')

    args = parser.parse_args()

    collector = HardwareDataCollector(collection_interval=args.interval)

    if args.add_failure:
        component_type, component_id, failure_date = args.add_failure
        collector.add_failure_event(component_type, component_id, failure_date)
    else:
        collector.run_continuous_collection(duration_hours=args.duration)


if __name__ == '__main__':
    main()
