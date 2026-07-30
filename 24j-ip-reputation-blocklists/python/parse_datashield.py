# Companion code for "The Backend of Luck" - Chapter 24j, IP Reputation and Blocklist Integration for iGaming Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

import ipaddress


def parse_datashield_list(filepath):
    """
    Parse a Data-Shield blocklist file.
    Returns a list of (network, is_cidr) tuples.
    """
    entries = []
    skipped = 0

    with open(filepath, 'r') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            try:
                # Try parsing as a network (handles both /32 host and CIDR)
                if '/' in line:
                    network = ipaddress.ip_network(line, strict=False)
                    entries.append((network, True))
                else:
                    # Single IP — convert to /32
                    network = ipaddress.ip_network(line + '/32', strict=False)
                    entries.append((network, False))
            except ValueError as e:
                skipped += 1
                if skipped <= 5:  # Log first few parsing errors
                    print(f"Warning: line {lineno}: could not parse '{line}': {e}")

    return entries


# Quick stats on a downloaded list
if __name__ == '__main__':
    import sys
    entries = parse_datashield_list(sys.argv[1])

    host_entries = [e for e in entries if not e[1]]
    cidr_entries = [e for e in entries if e[1]]

    total_ips = sum(e[0].num_addresses for e in entries)

    print(f"Total entries: {len(entries)}")
    print(f"  Host (/32) entries: {len(host_entries)}")
    print(f"  CIDR entries: {len(cidr_entries)}")
    print(f"  Total IP addresses covered: {total_ips:,}")
