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
CloudFlair - Cloudflare Bypass Detection Tool

Discovers origin server IPs hidden behind Cloudflare by searching for SSL
certificates in Censys and testing candidate hosts for content similarity.

Used at AcmetoCasino to verify that our production origin servers are not
discoverable via certificate transparency logs. Also used for competitive
analysis of other regulated gambling platforms' infrastructure.

Flow:
1. Confirm target domain resolves to Cloudflare IPs.
2. Search Censys for SSL certificates matching the domain.
3. Find IPv4 hosts that presented those certificates.
4. Filter out Cloudflare IPs from the results.
5. Compare each candidate's response to the original Cloudflare-fronted page.
6. Report origins where HTML content matches (identical or structurally similar).

Requires: CENSYS_API_ID and CENSYS_API_SECRET environment variables.
"""

import os
import sys
import random
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -- Configuration --
CONFIG = {
    'http_timeout_seconds': 3,
    'response_similarity_threshold': 0.9
}

CERT_CHUNK_SIZE = 25


def get_user_agent() -> str:
    """Return a legitimate-looking browser user-agent."""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; rv:68.0) Gecko/20100101 Firefox/68.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/76.0.3809.100 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/59.0.3071.115 Safari/537.36"
    ]
    return random.choice(user_agents)


def filter_cloudflare_ips(ips, cloudflare_checker):
    """Remove any Cloudflare IPs from the candidate list."""
    return [ip for ip in ips if not cloudflare_checker(ip)]


def find_hosts(domain, censys_api_id, censys_api_secret, cloudflare_checker, censys_search_fn):
    """
    Find candidate origin hosts by searching Censys for matching certificates.

    Args:
        domain: Target domain behind Cloudflare.
        censys_api_id: Censys API ID.
        censys_api_secret: Censys API secret.
        cloudflare_checker: Callable that returns True if IP is Cloudflare.
        censys_search_fn: Module with get_certificates() and get_hosts().
    """
    print(f'[*] Looking for certificates matching "{domain}" using Censys')
    cert_fingerprints = list(censys_search_fn.get_certificates(
        domain, censys_api_id, censys_api_secret
    ))
    cert_count = len(cert_fingerprints)
    print(f'[*] {cert_count} certificates matching "{domain}" found.')

    if cert_count == 0:
        print('Exiting.')
        return set()

    chunking = (cert_count > CERT_CHUNK_SIZE)
    if chunking:
        print(f'[*] Splitting certificates into chunks of {CERT_CHUNK_SIZE}.')

    print('[*] Looking for IPv4 hosts presenting these certificates...')
    hosts = set()
    for i in range(0, cert_count, CERT_CHUNK_SIZE):
        if chunking:
            chunk_num = i // CERT_CHUNK_SIZE + 1
            total_chunks = cert_count // CERT_CHUNK_SIZE + 1
            print(f'[*] Processing chunk {chunk_num}/{total_chunks}')
        hosts.update(censys_search_fn.get_hosts(
            cert_fingerprints[i:i + CERT_CHUNK_SIZE],
            censys_api_id, censys_api_secret
        ))

    hosts = filter_cloudflare_ips(hosts, cloudflare_checker)
    print(f'[*] {len(hosts)} non-Cloudflare hosts found presenting certificates for "{domain}".')

    return set(hosts)


def retrieve_original_page(domain):
    """Fetch the homepage through Cloudflare for comparison."""
    url = f'https://{domain}'
    print(f'[*] Retrieving target homepage at {url}')
    try:
        headers = {'User-Agent': get_user_agent()}
        response = requests.get(
            url, timeout=CONFIG['http_timeout_seconds'], headers=headers
        )
    except requests.exceptions.Timeout:
        sys.stderr.write(f'[-] {url} timed out after {CONFIG["http_timeout_seconds"]} seconds.\n')
        return None
    except requests.exceptions.RequestException:
        sys.stderr.write(f'[-] Failed to retrieve {url}\n')
        return None

    if response.status_code != 200:
        print(f'[-] {url} responded with HTTP {response.status_code}')
        return None

    if response.url != url:
        print(f'[*] "{url}" redirected to "{response.url}"')

    return response


def find_origins(domain, candidates):
    """
    Test each candidate IP to determine if it serves the same content as the
    Cloudflare-fronted domain. Uses HTML content comparison.
    """
    print('\n[*] Testing candidate origin servers')
    original_response = retrieve_original_page(domain)
    if original_response is None:
        return []

    host_header_value = original_response.url.replace('https://', '').split('/')[0]
    origins = []

    for host in candidates:
        try:
            print(f'  - {host}')
            url = f'https://{host}'
            headers = {
                'Host': host_header_value,
                'User-Agent': get_user_agent()
            }
            response = requests.get(
                url, timeout=CONFIG['http_timeout_seconds'],
                headers=headers, verify=False
            )
        except requests.exceptions.Timeout:
            print(f'      timed out after {CONFIG["http_timeout_seconds"]} seconds')
            continue
        except requests.exceptions.RequestException:
            print('      unable to retrieve')
            continue

        if response.status_code != 200:
            print(f'      responded with HTTP {response.status_code}')
            continue

        if response.text == original_response.text:
            origins.append((host, f'HTML content identical to {domain}'))
            continue

        # Structural similarity check (requires html_similarity package)
        if len(response.text) > 0:
            try:
                from html_similarity import similarity  # ty:ignore[unresolved-import]
                page_similarity = similarity(response.text, original_response.text)
                if page_similarity > CONFIG['response_similarity_threshold']:
                    origins.append((
                        host,
                        f'HTML content is {round(100 * page_similarity, 2)}% similar to {domain}'
                    ))
            except ImportError:
                pass  # html_similarity not installed; skip structural check

    return origins


def save_origins_to_file(origins, output_file):
    """Write discovered origins to a file."""
    if output_file is None:
        return

    try:
        with open(output_file, 'w') as f:
            for origin in origins:
                f.write(origin[0] + '\n')
        print(f'[*] Wrote {len(origins)} likely origins to {os.path.abspath(output_file)}')
    except IOError as e:
        sys.stderr.write(f'[-] Unable to write to {output_file}: {e}\n')


def print_origins(origins):
    """Display discovered origin servers."""
    for origin in origins:
        print(f'  - {origin[0]} ({origin[1]})')
    print('')


def main(domain, output_file, censys_api_id, censys_api_secret, cloudflare_checker, censys_search_fn):
    """Main workflow: find hosts, filter, test, report origins."""
    hosts = find_hosts(domain, censys_api_id, censys_api_secret, cloudflare_checker, censys_search_fn)

    if not hosts:
        print('[-] No candidate hosts found.')
        return

    for host in hosts:
        print(f'  - {host}')
    print('')

    origins = find_origins(domain, hosts)

    if not origins:
        print('[-] Did not find any origin server.')
        return

    print(f'\n[*] Found {len(origins)} likely origin servers of {domain}!')
    print_origins(origins)
    save_origins_to_file(origins, output_file)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description='Discover origin servers behind Cloudflare via Censys certificate search'
    )
    parser.add_argument('domain', help='Target domain behind Cloudflare')
    parser.add_argument('-o', '--output', dest='output_file', help='Output file for discovered IPs')
    parser.add_argument('--censys-api-id', help='Censys API ID (or set CENSYS_API_ID env var)')
    parser.add_argument('--censys-api-secret', help='Censys API secret (or set CENSYS_API_SECRET env var)')
    args = parser.parse_args()

    api_id = args.censys_api_id or os.environ.get('CENSYS_API_ID')
    api_secret = args.censys_api_secret or os.environ.get('CENSYS_API_SECRET')

    if not api_id or not api_secret:
        sys.stderr.write('[!] Set CENSYS_API_ID and CENSYS_API_SECRET env vars or pass via CLI.\n')
        sys.exit(1)

    # Import the cloudflare_utils module from the same directory
    from cloudflare_utils import is_cloudflare_ip, uses_cloudflare

    if not uses_cloudflare(args.domain):
        print(f'[-] "{args.domain}" does not appear to be behind Cloudflare.')
        sys.exit(0)
    print('[*] The target appears to be behind Cloudflare.')

    # Note: In production, import censys_search module for Censys API calls.
    # This example expects a censys_search module with get_certificates() and get_hosts().
    try:
        import censys_search  # ty:ignore[unresolved-import]
        main(args.domain, args.output_file, api_id, api_secret, is_cloudflare_ip, censys_search)
    except ImportError:
        sys.stderr.write('[!] censys_search module not found. Install censys package or provide the module.\n')
        sys.exit(1)
