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
Cloudflare IP Range Utilities

Fetches the current Cloudflare IPv4 ranges from cloudflare.com/ips-v4 and
provides functions to check whether an IP address belongs to Cloudflare.

Used at AcmetoCasino for:
- Filtering Cloudflare IPs when searching for origin servers.
- Validating that DNS resolutions point to Cloudflare (confirming CDN protection).
- Security audits verifying that all player-facing domains are behind CDN.
"""

import ipaddress
import sys
import requests
import dns.resolver  # ty:ignore[unresolved-import]


def get_cloudflare_ip_ranges():
    """
    Fetch current Cloudflare IPv4 ranges.
    Falls back to a static list if the API is unreachable.
    """
    cloudflare_ip_ranges_url = 'https://www.cloudflare.com/ips-v4'

    ip_ranges_fallback = [
        "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "104.16.0.0/12", "108.162.192.0/18", "131.0.72.0/22",
        "141.101.64.0/18", "162.158.0.0/15", "172.64.0.0/13",
        "173.245.48.0/20", "188.114.96.0/20", "190.93.240.0/20",
        "197.234.240.0/22", "198.41.128.0/17"
    ]

    try:
        print(f'[*] Retrieving Cloudflare IP ranges from {cloudflare_ip_ranges_url}')
        response = requests.get(cloudflare_ip_ranges_url, timeout=10)
        ip_ranges = [ip for ip in response.text.split("\n") if ip.strip()]
        return ip_ranges
    except requests.exceptions.RequestException:
        sys.stderr.write(
            '[-] Failed to retrieve Cloudflare IP ranges -- using default (possibly outdated) list\n'
        )
        return ip_ranges_fallback


# Load ranges at module import time
cloudflare_ip_ranges = get_cloudflare_ip_ranges()
cloudflare_subnets = [ipaddress.ip_network(ip_range) for ip_range in cloudflare_ip_ranges]


def is_cloudflare_ip(ip: str) -> bool:
    """Check if the given IP address belongs to a Cloudflare subnet."""
    try:
        addr = ipaddress.ip_network(ip)
        return any(subnet.overlaps(addr) for subnet in cloudflare_subnets)
    except ValueError:
        return False


def uses_cloudflare(domain: str) -> bool:
    """Check if a domain's A records resolve to Cloudflare IPs."""
    try:
        answers = dns.resolver.resolve(domain, 'A')
        for answer in answers:
            if is_cloudflare_ip(str(answer)):
                return True
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.DNSException):
        pass
    return False
