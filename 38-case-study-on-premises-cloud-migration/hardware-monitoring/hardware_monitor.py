#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 38, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Hardware monitoring agent for data centre equipment.
Monitors servers, NVMe drives, disks, network interfaces, and firewalls.

This was deployed on every bare-metal server in the AcmetoCasino colocation
facility before cloud migration. It collected metrics that fed into
predictive maintenance models (see ml_predictor.py).

After migration to AWS, this entire system was replaced by CloudWatch,
EC2 instance status checks, and EBS volume monitoring -- illustrating
how cloud migration eliminates entire categories of operational tooling.
"""

import os
import re
import sys
import time
import json
import subprocess
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
import psutil
import requests
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class HardwareMonitor:
    def __init__(self):
        self.hostname = os.uname().nodename
        self.collection_interval = int(os.getenv('COLLECTION_INTERVAL', '300'))
        self.elasticsearch_url = os.getenv('ELASTICSEARCH_HOST', 'http://localhost:9200')
        self.opensearch_url = os.getenv('OPENSEARCH_ENDPOINT', self.elasticsearch_url)
        self.opensearch_username = os.getenv('OPENSEARCH_USERNAME')
        self.opensearch_password = os.getenv('OPENSEARCH_PASSWORD')
        self.influxdb_url = os.getenv('INFLUXDB_HOST', 'http://localhost:8086')
        self.prometheus_gateway = os.getenv('PROMETHEUS_PUSHGATEWAY', 'http://localhost:9091')
        self.data_dir = os.getenv('DATA_DIR', '/app/data')

        # Determine if using OpenSearch or Elasticsearch
        self.use_opensearch = bool(self.opensearch_username and self.opensearch_password)
        if self.use_opensearch:
            self.search_url = self.opensearch_url
            logger.info(f"Using OpenSearch at {self.search_url}")
        else:
            self.search_url = self.elasticsearch_url
            logger.info(f"Using Elasticsearch at {self.search_url}")

        # Create data directory
        os.makedirs(self.data_dir, exist_ok=True)

        # Initialize Prometheus metrics
        self.registry = CollectorRegistry()
        self._init_prometheus_metrics()

        logger.info(f"Hardware monitor initialized for {self.hostname}")

    def _init_prometheus_metrics(self):
        """Initialize Prometheus metrics"""
        self.cpu_usage = Gauge('hardware_cpu_usage', 'CPU usage percentage',
                              ['component_type', 'component_id'], registry=self.registry)
        self.memory_usage = Gauge('hardware_memory_usage', 'Memory usage percentage',
                                 ['component_type', 'component_id'], registry=self.registry)
        self.disk_usage = Gauge('hardware_disk_usage', 'Disk usage percentage',
                               ['component_type', 'component_id'], registry=self.registry)
        self.network_errors = Gauge('hardware_network_errors', 'Network errors count',
                                   ['component_type', 'component_id'], registry=self.registry)
        self.temperature = Gauge('hardware_temperature', 'Temperature in Celsius',
                                ['component_type', 'component_id'], registry=self.registry)
        self.wear_level = Gauge('hardware_wear_level', 'Wear level percentage',
                               ['component_type', 'component_id'], registry=self.registry)
        self.error_count = Gauge('hardware_error_count', 'Error count',
                                ['component_type', 'component_id'], registry=self.registry)
        self.reallocated_sectors = Gauge('hardware_reallocated_sectors', 'Reallocated sectors count',
                                        ['component_type', 'component_id'], registry=self.registry)
        self.age_days = Gauge('hardware_age_days', 'Equipment age in days',
                             ['component_type', 'component_id'], registry=self.registry)
        self.risk_score = Gauge('hardware_risk_score', 'Risk score (0-100)',
                               ['component_type', 'component_id', 'risk_level'], registry=self.registry)
        self.last_collection = Gauge('hardware_last_collection_timestamp', 'Last collection timestamp',
                                    registry=self.registry)

    def _run_command(self, cmd: List[str], timeout: int = 10) -> Optional[str]:
        """Run shell command and return output"""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                logger.warning(f"Command failed: {' '.join(cmd)} - {result.stderr}")
                return None
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            logger.warning(f"Command error: {' '.join(cmd)} - {e}")
            return None

    def _get_manufacture_date(self, component_type: str, component_id: str) -> str:
        """Get manufacture date for component"""
        try:
            if component_type == 'server':
                output = self._run_command(['dmidecode', '-t', 'bios'])
                if output:
                    for line in output.split('\n'):
                        if 'Release Date' in line:
                            return line.split(':')[1].strip()
            elif component_type in ['nvme', 'disk']:
                output = self._run_command(['smartctl', '-i', f'/dev/{component_id}'])
                if output:
                    for line in output.split('\n'):
                        if 'Date' in line and 'First' in line:
                            return line.split(':')[1].strip()
        except Exception as e:
            logger.warning(f"Error getting manufacture date for {component_type}/{component_id}: {e}")
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

    def _calculate_risk_score(self, component_type: str, metrics: Dict[str, Any]) -> tuple:
        """Calculate risk score based on component type and metrics"""
        score = 0
        age_days = metrics.get('age_days', 0)

        # Age-based risk
        if age_days > 1095:  # 3 years
            score += 30
        elif age_days > 730:  # 2 years
            score += 20
        elif age_days > 365:  # 1 year
            score += 10

        # Component-specific risks
        if component_type == 'server':
            if metrics.get('cpu_usage', 0) > 90:
                score += 20
            if metrics.get('memory_usage', 0) > 90:
                score += 20
        elif component_type == 'disk':
            if metrics.get('wear_level', 0) > 90:
                score += 40
            if metrics.get('error_count', 0) > 10:
                score += 30
            if metrics.get('reallocated_sectors', 0) > 100:
                score += 30
        elif component_type == 'network':
            if metrics.get('network_errors', 0) > 1000:
                score += 25

        # Determine risk level
        if score >= 70:
            risk_level = 'critical'
        elif score >= 40:
            risk_level = 'high'
        elif score >= 20:
            risk_level = 'medium'
        else:
            risk_level = 'low'

        return score, risk_level

    def monitor_server(self) -> List[Dict[str, Any]]:
        """Monitor server components"""
        logger.info("Monitoring server components...")
        metrics = []

        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        # Temperature
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

        server_metrics = {
            'timestamp': datetime.now().isoformat(),
            'component_type': 'server',
            'component_id': 'main',
            'manufacture_date': manufacture_date,
            'age_days': age_days,
            'cpu_usage': cpu_percent,
            'memory_usage': memory.percent,
            'disk_usage': disk.percent,
            'temperature': temperature,
            'wear_level': 0,
            'error_count': 0,
            'reallocated_sectors': 0,
            'network_errors': 0
        }

        risk_score, risk_level = self._calculate_risk_score('server', server_metrics)
        server_metrics['risk_score'] = risk_score
        server_metrics['risk_level'] = risk_level

        # Update Prometheus metrics
        self.cpu_usage.labels(component_type='server', component_id='main').set(cpu_percent)
        self.memory_usage.labels(component_type='server', component_id='main').set(memory.percent)
        self.temperature.labels(component_type='server', component_id='main').set(temperature)
        self.age_days.labels(component_type='server', component_id='main').set(age_days)
        self.risk_score.labels(component_type='server', component_id='main', risk_level=risk_level).set(risk_score)

        metrics.append(server_metrics)
        return metrics

    def monitor_disks(self) -> List[Dict[str, Any]]:
        """Monitor disk drives (NVMe and traditional)"""
        logger.info("Monitoring disk drives...")
        metrics = []

        try:
            result = self._run_command(['lsblk', '-d', '-n', '-o', 'NAME'])
            if result:
                disks = [d for d in result.split('\n') if d and (d.startswith(('sd', 'nvme')))]
            else:
                disks = []
        except Exception:
            disks = []

        for disk in disks:
            device_path = f'/dev/{disk}'
            manufacture_date = self._get_manufacture_date('disk', disk)
            age_days = self._calculate_age_days(manufacture_date)

            wear_level = 0
            error_count = 0
            reallocated_sectors = 0

            try:
                if disk.startswith('nvme'):
                    output = self._run_command(['nvme', 'smart-log', device_path])
                    if output:
                        for line in output.split('\n'):
                            if 'percentage_used' in line:
                                wear_level = int(line.split(':')[1].strip().split()[0])
                            elif 'num_err_log_entries' in line:
                                error_count = int(line.split(':')[1].strip())
                else:
                    output = self._run_command(['smartctl', '-A', device_path])
                    if output:
                        for line in output.split('\n'):
                            if 'Reallocated_Sector_Ct' in line:
                                reallocated_sectors = int(line.split()[-1])
                            elif 'Uncorrectable_Error_Ct' in line:
                                error_count = int(line.split()[-1])
            except Exception as e:
                logger.warning(f"Error getting SMART data for {disk}: {e}")

            disk_metrics = {
                'timestamp': datetime.now().isoformat(),
                'component_type': 'disk',
                'component_id': disk,
                'manufacture_date': manufacture_date,
                'age_days': age_days,
                'cpu_usage': 0,
                'memory_usage': 0,
                'disk_usage': 0,
                'temperature': 0,
                'wear_level': wear_level,
                'error_count': error_count,
                'reallocated_sectors': reallocated_sectors,
                'network_errors': 0
            }

            risk_score, risk_level = self._calculate_risk_score('disk', disk_metrics)
            disk_metrics['risk_score'] = risk_score
            disk_metrics['risk_level'] = risk_level

            self.wear_level.labels(component_type='disk', component_id=disk).set(wear_level)
            self.error_count.labels(component_type='disk', component_id=disk).set(error_count)
            self.reallocated_sectors.labels(component_type='disk', component_id=disk).set(reallocated_sectors)
            self.age_days.labels(component_type='disk', component_id=disk).set(age_days)
            self.risk_score.labels(component_type='disk', component_id=disk, risk_level=risk_level).set(risk_score)

            metrics.append(disk_metrics)

        return metrics

    def monitor_network(self) -> List[Dict[str, Any]]:
        """Monitor network interfaces"""
        logger.info("Monitoring network interfaces...")
        metrics = []

        try:
            result = self._run_command(['ip', 'link', 'show'])
            if result:
                interfaces = []
                for line in result.split('\n'):
                    if re.match(r'^\d+:', line) and 'state' in line:
                        iface = line.split(':')[1].strip().split()[0]
                        if iface != 'lo':
                            interfaces.append(iface)
            else:
                interfaces = []
        except Exception:
            interfaces = []

        for iface in interfaces:
            rx_errors = 0
            tx_errors = 0

            try:
                output = self._run_command(['ip', '-s', 'link', 'show', iface])
                if output:
                    lines = output.split('\n')
                    for i, line in enumerate(lines):
                        if 'RX:' in line and i + 1 < len(lines):
                            rx_line = lines[i + 1].strip().split()
                            if len(rx_line) > 2:
                                rx_errors = int(rx_line[2])
                        elif 'TX:' in line and i + 1 < len(lines):
                            tx_line = lines[i + 1].strip().split()
                            if len(tx_line) > 2:
                                tx_errors = int(tx_line[2])
            except Exception as e:
                logger.warning(f"Error getting network stats for {iface}: {e}")

            network_metrics = {
                'timestamp': datetime.now().isoformat(),
                'component_type': 'network',
                'component_id': iface,
                'manufacture_date': 'unknown',
                'age_days': -1,
                'cpu_usage': 0,
                'memory_usage': 0,
                'disk_usage': 0,
                'temperature': 0,
                'wear_level': 0,
                'error_count': 0,
                'reallocated_sectors': 0,
                'network_errors': rx_errors + tx_errors
            }

            risk_score, risk_level = self._calculate_risk_score('network', network_metrics)
            network_metrics['risk_score'] = risk_score
            network_metrics['risk_level'] = risk_level

            self.network_errors.labels(component_type='network', component_id=iface).set(rx_errors + tx_errors)
            self.risk_score.labels(component_type='network', component_id=iface, risk_level=risk_level).set(risk_score)

            metrics.append(network_metrics)

        return metrics

    def collect_all_metrics(self) -> List[Dict[str, Any]]:
        """Collect metrics from all components"""
        all_metrics = []
        try:
            all_metrics.extend(self.monitor_server())
            all_metrics.extend(self.monitor_disks())
            all_metrics.extend(self.monitor_network())
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")

        self.last_collection.set(time.time())
        return all_metrics

    def send_to_elasticsearch(self, metrics: List[Dict[str, Any]]):
        """Send metrics to Elasticsearch/OpenSearch"""
        if not metrics:
            return
        try:
            for metric in metrics:
                doc = {k: metric[k] for k in metric}
                doc['hostname'] = self.hostname

                index_name = f"hardware-metrics-{datetime.now().strftime('%Y.%m.%d')}"
                url = f"{self.search_url}/{index_name}/_doc"
                headers = {'Content-Type': 'application/json'}

                if self.use_opensearch:
                    auth = (self.opensearch_username, self.opensearch_password)
                    response = requests.post(url, json=doc, headers=headers, auth=auth, timeout=10)
                else:
                    response = requests.post(url, json=doc, headers=headers, timeout=10)

                if response.status_code not in [200, 201]:
                    logger.warning(f"Failed to send to search engine: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Error sending to search engine: {e}")

    def send_to_influxdb(self, metrics: List[Dict[str, Any]]):
        """Send metrics to InfluxDB using line protocol"""
        if not metrics:
            return
        try:
            lines = []
            for metric in metrics:
                tags = f'hostname={self.hostname},component_type={metric["component_type"]},component_id={metric["component_id"]},risk_level={metric["risk_level"]}'
                fields = ','.join([f'{k}={v}' for k, v in metric.items()
                                  if k not in ['timestamp', 'component_type', 'component_id', 'manufacture_date', 'risk_level']])
                timestamp = int(datetime.fromisoformat(metric['timestamp']).timestamp() * 1e9)
                line = f'hardware_metrics,{tags} {fields} {timestamp}'
                lines.append(line)

            data = '\n'.join(lines)
            url = f"{self.influxdb_url}/api/v2/write?org=igaming&bucket=hardware_metrics"
            headers = {
                'Authorization': f"Token {os.getenv('INFLUXDB_TOKEN', 'changeme')}",
                'Content-Type': 'text/plain'
            }
            response = requests.post(url, data=data, headers=headers, timeout=10)
            if response.status_code != 204:
                logger.warning(f"Failed to send to InfluxDB: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Error sending to InfluxDB: {e}")

    def send_to_prometheus(self):
        """Push metrics to Prometheus Pushgateway"""
        try:
            push_to_gateway(self.prometheus_gateway, job='hardware-agent',
                          registry=self.registry, timeout=10)
        except Exception as e:
            logger.error(f"Error pushing to Prometheus: {e}")

    def save_to_file(self, metrics: List[Dict[str, Any]]):
        """Save metrics to local file for offline analysis"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{self.data_dir}/hardware_metrics_{timestamp}.json"
            with open(filename, 'w') as f:
                json.dump(metrics, f, indent=2)
            logger.info(f"Metrics saved to {filename}")
        except Exception as e:
            logger.error(f"Error saving metrics to file: {e}")

    def run_continuous_monitoring(self):
        """Run continuous monitoring loop"""
        logger.info(f"Starting continuous monitoring with {self.collection_interval}s interval")
        while True:
            try:
                metrics = self.collect_all_metrics()
                if metrics:
                    if self.elasticsearch_url:
                        self.send_to_elasticsearch(metrics)
                    if self.influxdb_url:
                        self.send_to_influxdb(metrics)
                    if self.prometheus_gateway:
                        self.send_to_prometheus()
                    self.save_to_file(metrics)
                    logger.info(f"Collected and sent {len(metrics)} metrics")
                time.sleep(self.collection_interval)
            except KeyboardInterrupt:
                logger.info("Monitoring stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(self.collection_interval)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Hardware Monitoring Agent')
    parser.add_argument('--continuous', action='store_true', help='Run continuous monitoring')
    parser.add_argument('--interval', type=int, default=300, help='Collection interval in seconds')
    args = parser.parse_args()

    os.environ['COLLECTION_INTERVAL'] = str(args.interval)
    monitor = HardwareMonitor()

    if args.continuous:
        monitor.run_continuous_monitoring()
    else:
        metrics = monitor.collect_all_metrics()
        monitor.save_to_file(metrics)
        print(f"Collected {len(metrics)} metrics")

if __name__ == '__main__':
    main()
