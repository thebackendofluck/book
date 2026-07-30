#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Domain Analyzer for Regulated Betting Platforms

Analyzes gambling domains (.bet.br, .com, etc.) to:
1. Detect CDN protection (Cloudflare, Akamai, Fastly) via DNS and IP analysis.
2. Extract WHOIS organization and contact details for each domain.
3. Identify hosting providers (AWS, Google Cloud, Azure) from IP WHOIS.
4. Group domains by shared IP addresses to reveal co-hosted platforms.
5. Discover origin IPs behind Cloudflare using Censys certificate search.
6. Generate CSV reports and executive summary for security audits.

Used at AcmetoCasino for competitive infrastructure analysis and to verify
that our own Cloudflare deployments weren't leaking origin server IPs.
"""

import re
import csv
import socket
import subprocess
import json
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import time
import logging
import requests
from urllib.parse import urlparse
import os
import sys
import ipaddress

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Custom DNS server (replace with your resolver)
CUSTOM_DNS = '8.8.8.8'

# Cloudflare IP ranges for detection
CLOUDFLARE_IPV4_RANGES = [
    "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "104.16.0.0/12", "108.162.192.0/18", "131.0.72.0/22",
    "141.101.64.0/18", "162.158.0.0/15", "172.64.0.0/13",
    "173.245.48.0/20", "188.114.96.0/20", "190.93.240.0/20",
    "197.234.240.0/22", "198.41.128.0/17"
]


class DomainAnalyzer:
    """Analyzes gambling domains for CDN, hosting, and security posture."""

    def __init__(self):
        self.domains = []
        self.results = []
        self.ip_groups = defaultdict(list)
        self.domain_to_brand = {}
        self.brand_to_company = {}
        self.cloudflare_subnets = [
            ipaddress.ip_network(cidr) for cidr in CLOUDFLARE_IPV4_RANGES
        ]

    def is_cloudflare_ip(self, ip: str) -> bool:
        """Check if an IP belongs to Cloudflare's published ranges."""
        try:
            addr = ipaddress.ip_address(ip)
            return any(addr in subnet for subnet in self.cloudflare_subnets)
        except ValueError:
            return False

    def extract_domains_from_file(self, filepath: str) -> List[str]:
        """Extract all .bet.br domains from a markdown or text file."""
        domains = set()

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            # Find all .bet.br domains in URLs
            pattern = r'https://([a-zA-Z0-9-]+\.bet\.br)'
            matches = re.findall(pattern, content)

            # Also extract brand to company mappings from table rows
            self._extract_brand_mappings(content)

            domains = list(set(matches))
            domains.sort()

            logger.info(f"Extracted {len(domains)} unique domains from {filepath}")
            return domains

        except FileNotFoundError:
            logger.error(f"File not found: {filepath}")
            return []
        except Exception as e:
            logger.error(f"Error reading file: {e}")
            return []

    def _extract_brand_mappings(self, content: str) -> None:
        """Extract brand-to-company mappings from markdown table rows."""
        table_pattern = r'\| ([^*]+?) \| https://([a-zA-Z0-9-]+\.bet\.br) \| ([^|]+) \|'
        matches = re.findall(table_pattern, content)

        for brand, domain, company in matches:
            brand = brand.strip()
            domain = domain.strip()
            company = company.strip()
            self.domain_to_brand[domain] = brand
            self.brand_to_company[brand] = company

        logger.info(f"Extracted mappings for {len(self.domain_to_brand)} domains")

    def get_dns_info(self, domain: str) -> Dict:
        """Get DNS information including CNAME records for CDN detection."""
        dns_info = {
            'domain': domain,
            'cname_records': [],
            'a_records': [],
            'cdn_provider': 'Unknown',
            'has_cloudflare': False,
            'has_other_cdn': False,
            'dns_status': 'OK'
        }

        try:
            # Resolve A records via dig with custom DNS
            result = subprocess.run(
                ['dig', '@' + CUSTOM_DNS, domain, 'A', '+short'],
                capture_output=True, text=True, timeout=15
            )

            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    line = line.strip()
                    if not line or line.startswith(';'):
                        continue
                    try:
                        ipaddress.ip_address(line)
                        dns_info['a_records'].append(line)
                        if self.is_cloudflare_ip(line):
                            dns_info['cdn_provider'] = 'Cloudflare'
                            dns_info['has_cloudflare'] = True
                    except ValueError:
                        pass  # Not an IP address (might be a CNAME)

            # Get CNAME records
            cname_result = subprocess.run(
                ['dig', '@' + CUSTOM_DNS, domain, 'CNAME', '+short'],
                capture_output=True, text=True, timeout=15
            )

            if cname_result.returncode == 0 and cname_result.stdout.strip():
                for line in cname_result.stdout.strip().split('\n'):
                    line = line.strip()
                    if not line or line.startswith(';'):
                        continue
                    cname = line.rstrip('.')
                    dns_info['cname_records'].append(cname)

                    # Detect CDN from CNAME
                    cname_lower = cname.lower()
                    if 'cloudflare' in cname_lower:
                        dns_info['cdn_provider'] = 'Cloudflare'
                        dns_info['has_cloudflare'] = True
                    elif 'akamai' in cname_lower:
                        dns_info['cdn_provider'] = 'Akamai'
                        dns_info['has_other_cdn'] = True
                    elif 'fastly' in cname_lower:
                        dns_info['cdn_provider'] = 'Fastly'
                        dns_info['has_other_cdn'] = True
                    elif 'cdn' in cname_lower or 'edge' in cname_lower:
                        dns_info['cdn_provider'] = 'Other CDN'
                        dns_info['has_other_cdn'] = True

        except subprocess.TimeoutExpired:
            logger.warning(f"DNS timeout for {domain}")
            dns_info['dns_status'] = "Timeout"
        except Exception as e:
            logger.error(f"Error getting DNS info for {domain}: {e}")
            dns_info['dns_status'] = f"DNS Error: {str(e)}"

        return dns_info

    def get_whois_info(self, domain: str) -> Dict:
        """Get WHOIS information for domain registration details."""
        whois_info = {
            'domain': domain,
            'organization': 'Unknown',
            'emails': [],
            'registrar': 'Unknown',
            'creation_date': 'Unknown',
            'expiration_date': 'Unknown',
            'whois_status': 'OK'
        }

        try:
            result = subprocess.run(
                ['whois', domain],
                capture_output=True, text=True, timeout=20
            )

            if result.returncode == 0:
                whois_output = result.stdout

                # Extract organization
                for pattern in [
                    r'Organization:\s*(.+)', r'OrgName:\s*(.+)',
                    r'Registrant Organization:\s*(.+)',
                    r'Admin Organization:\s*(.+)'
                ]:
                    match = re.search(pattern, whois_output, re.IGNORECASE)
                    if match:
                        whois_info['organization'] = match.group(1).strip()
                        break

                # Extract emails
                email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
                whois_info['emails'] = list(set(re.findall(email_pattern, whois_output)))

                # Extract registrar
                for pattern in [r'Registrar:\s*(.+)', r'Registrar Name:\s*(.+)']:
                    match = re.search(pattern, whois_output, re.IGNORECASE)
                    if match:
                        whois_info['registrar'] = match.group(1).strip()
                        break

                # Extract dates
                for pattern in [r'Creation Date:\s*(.+)', r'Created:\s*(.+)']:
                    match = re.search(pattern, whois_output, re.IGNORECASE)
                    if match:
                        whois_info['creation_date'] = match.group(1).strip()
                        break

                for pattern in [r'Expiration Date:\s*(.+)', r'Expiry Date:\s*(.+)']:
                    match = re.search(pattern, whois_output, re.IGNORECASE)
                    if match:
                        whois_info['expiration_date'] = match.group(1).strip()
                        break
            else:
                whois_info['whois_status'] = f"WHOIS failed: {result.stderr}"

        except subprocess.TimeoutExpired:
            whois_info['whois_status'] = "Timeout"
        except Exception as e:
            whois_info['whois_status'] = f"Error: {str(e)}"

        return whois_info

    def get_ip_provider(self, ip: str) -> str:
        """Determine the hosting provider for an IP address via WHOIS."""
        try:
            result = subprocess.run(
                ['whois', ip], capture_output=True, text=True, timeout=15
            )

            if result.returncode == 0:
                whois_output = result.stdout
                providers = {
                    'amazon': 'AWS', 'amazon web services': 'AWS',
                    'microsoft': 'Microsoft Azure', 'azure': 'Microsoft Azure',
                    'google': 'Google Cloud', 'google cloud': 'Google Cloud',
                    'cloudflare': 'Cloudflare', 'digitalocean': 'DigitalOcean',
                    'linode': 'Linode', 'vultr': 'Vultr',
                    'hetzner': 'Hetzner', 'ovh': 'OVH',
                    'incapsula': 'Incapsula (Imperva)'
                }

                org_match = re.search(r'OrgName:\s*(.+)', whois_output, re.IGNORECASE)
                if org_match:
                    org_name = org_match.group(1).strip()
                    for keyword, provider in providers.items():
                        if keyword.lower() in org_name.lower():
                            return provider
                    return org_name

                desc_match = re.search(r'descr:\s*(.+)', whois_output, re.IGNORECASE)
                if desc_match:
                    desc = desc_match.group(1).strip()
                    for keyword, provider in providers.items():
                        if keyword.lower() in desc.lower():
                            return provider

        except Exception as e:
            logger.error(f"Error getting provider for IP {ip}: {e}")

        return 'Unknown'

    def check_url_response(self, domain: str) -> Dict:
        """Check if URL responds and measure response time."""
        url = f"https://{domain}"
        response_info = {
            'url': url, 'responds': False, 'status_code': None,
            'response_time_ms': None, 'error': None, 'final_url': None
        }

        try:
            start_time = time.time()
            response = requests.get(
                url, timeout=10, verify=False, allow_redirects=True
            )
            response_time = time.time() - start_time

            response_info['responds'] = True
            response_info['status_code'] = response.status_code
            response_info['response_time_ms'] = round(response_time * 1000, 2)
            response_info['final_url'] = response.url

        except requests.exceptions.SSLError:
            response_info['error'] = 'SSL Error'
        except requests.exceptions.ConnectionError:
            response_info['error'] = 'Connection Error'
        except requests.exceptions.Timeout:
            response_info['error'] = 'Timeout'
        except Exception as e:
            response_info['error'] = str(e)

        return response_info

    def analyze_domain(self, domain: str) -> Dict:
        """Analyze a single domain comprehensively."""
        logger.info(f"Analyzing domain: {domain}")

        brand = self.domain_to_brand.get(domain, 'Unknown')
        company = self.brand_to_company.get(brand, 'Unknown')

        dns_info = self.get_dns_info(domain)
        whois_info = self.get_whois_info(domain)
        url_info = self.check_url_response(domain)

        # Analyze IPs and group by shared hosting
        ip_providers = {}
        for ip in dns_info['a_records']:
            provider = self.get_ip_provider(ip)
            ip_providers[ip] = provider
            self.ip_groups[ip].append(domain)

        return {
            'domain': domain,
            'brand': brand,
            'company': company,
            'url': f"https://{domain}",
            'responds': url_info['responds'],
            'status_code': url_info['status_code'],
            'response_time_ms': url_info['response_time_ms'],
            'url_error': url_info['error'],
            'final_url': url_info['final_url'],
            'cdn_provider': dns_info['cdn_provider'],
            'has_cloudflare': dns_info['has_cloudflare'],
            'has_other_cdn': dns_info['has_other_cdn'],
            'dns_status': dns_info['dns_status'],
            'ip_addresses': ', '.join(dns_info['a_records']),
            'ip_providers': ', '.join(set(ip_providers.values())),
            'organization': whois_info['organization'],
            'emails': ', '.join(whois_info['emails']),
            'registrar': whois_info['registrar'],
            'creation_date': whois_info['creation_date'],
            'expiration_date': whois_info['expiration_date'],
            'whois_status': whois_info['whois_status'],
            'cname_records': ', '.join(dns_info['cname_records'])
        }

    def run_analysis(self, domains: List[str]) -> None:
        """Run analysis on all domains with rate limiting."""
        logger.info(f"Starting analysis of {len(domains)} domains")

        for i, domain in enumerate(domains, 1):
            logger.info(f"Processing {i}/{len(domains)}: {domain}")
            try:
                result = self.analyze_domain(domain)
                self.results.append(result)
                time.sleep(2)  # Rate limiting
            except Exception as e:
                logger.error(f"Failed to analyze {domain}: {e}")
                self.results.append({
                    'domain': domain, 'brand': self.domain_to_brand.get(domain, 'Unknown'),
                    'company': 'Error', 'url': f"https://{domain}",
                    'responds': False, 'status_code': None, 'response_time_ms': None,
                    'url_error': str(e), 'final_url': None,
                    'cdn_provider': 'Error', 'has_cloudflare': False,
                    'has_other_cdn': False, 'dns_status': 'Error',
                    'ip_addresses': 'Error', 'ip_providers': 'Error',
                    'organization': 'Error', 'emails': 'Error',
                    'registrar': 'Error', 'creation_date': 'Error',
                    'expiration_date': 'Error', 'whois_status': 'Error',
                    'cname_records': 'Error'
                })

    def save_to_csv(self, filename: str) -> None:
        """Save results to CSV file."""
        if not self.results:
            logger.warning("No results to save")
            return

        fieldnames = [
            'domain', 'brand', 'company', 'url', 'responds', 'status_code',
            'response_time_ms', 'url_error', 'final_url', 'cdn_provider',
            'has_cloudflare', 'has_other_cdn', 'dns_status', 'ip_addresses',
            'ip_providers', 'organization', 'emails', 'registrar',
            'creation_date', 'expiration_date', 'whois_status', 'cname_records'
        ]

        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.results)
        logger.info(f"Results saved to {filename}")

    def save_ip_groups(self, filename: str) -> None:
        """Save IP grouping analysis -- domains sharing the same IP."""
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['IP Address', 'Provider', 'Domains', 'Domain Count'])

            for ip, domains in self.ip_groups.items():
                if len(domains) > 1:
                    provider = self.get_ip_provider(ip)
                    writer.writerow([
                        ip, provider, ', '.join(sorted(domains)), len(domains)
                    ])
        logger.info(f"IP groups saved to {filename}")

    def save_summary_report(self, filename: str) -> None:
        """Save executive summary report with statistics."""
        total = len(self.results)
        if total == 0:
            return

        responding = sum(1 for r in self.results if r['responds'])
        cloudflare = sum(1 for r in self.results if r['has_cloudflare'])
        aws = sum(1 for r in self.results if 'AWS' in r['ip_providers'])
        azure = sum(1 for r in self.results if 'Azure' in r['ip_providers'])

        companies = {}
        for r in self.results:
            company = r.get('company', 'Unknown')
            if company != 'Unknown':
                companies[company] = companies.get(company, 0) + 1

        with open(filename, 'w', encoding='utf-8') as f:
            f.write("Regulated Betting Domain Analysis Report\n")
            f.write("=" * 50 + "\n\n")
            f.write("EXECUTIVE SUMMARY\n")
            f.write("-" * 20 + "\n")
            f.write(f"Total domains analyzed: {total}\n")
            f.write(f"Domains responding to HTTPS: {responding} ({responding/total*100:.1f}%)\n\n")
            f.write("CDN & HOSTING ANALYSIS\n")
            f.write("-" * 25 + "\n")
            f.write(f"Domains using Cloudflare: {cloudflare} ({cloudflare/total*100:.1f}%)\n")
            f.write(f"Domains on AWS: {aws} ({aws/total*100:.1f}%)\n")
            f.write(f"Domains on Azure: {azure} ({azure/total*100:.1f}%)\n\n")

            f.write("TOP COMPANIES BY DOMAIN COUNT\n")
            f.write("-" * 30 + "\n")
            for company, count in sorted(companies.items(), key=lambda x: x[1], reverse=True)[:10]:
                f.write(f"{company}: {count} domains\n")
            f.write("\n")

            f.write("TOP IP GROUPS (SHARED HOSTING)\n")
            f.write("-" * 35 + "\n")
            sorted_groups = sorted(self.ip_groups.items(), key=lambda x: len(x[1]), reverse=True)
            for ip, domains in sorted_groups[:15]:
                if len(domains) > 1:
                    provider = self.get_ip_provider(ip)
                    f.write(f"{ip} ({provider}): {len(domains)} domains\n")
                    for domain in sorted(domains)[:3]:
                        f.write(f"  - {domain}\n")
                    if len(domains) > 3:
                        f.write(f"  ... and {len(domains)-3} more\n")
                    f.write("\n")

            f.write(f"Report generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
        logger.info(f"Summary report saved to {filename}")


def main():
    analyzer = DomainAnalyzer()

    # Extract domains from a list file (adapt to your input format)
    domains = analyzer.extract_domains_from_file('domains_list.md')

    if not domains:
        logger.error("No domains found to analyze")
        return

    analyzer.run_analysis(domains)
    analyzer.save_to_csv('domain_analysis_results.csv')
    analyzer.save_ip_groups('ip_group_analysis.csv')
    analyzer.save_summary_report('analysis_summary.txt')
    logger.info("Analysis complete!")


if __name__ == '__main__':
    main()
