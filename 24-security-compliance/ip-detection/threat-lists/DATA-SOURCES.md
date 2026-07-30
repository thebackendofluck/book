# Threat-list data sources

The consolidation toolkit in this directory downloads and merges public IP
threat feeds at runtime. The consolidated output is **not** committed here,
because the upstream feeds carry licences that are incompatible with this
repository's Apache-2.0 licence:

| Feed | Licence |
|------|---------|
| FireHOL blocklist-ipsets | CC BY-SA 4.0 (share-alike, attribution) |
| DShield / SANS ISC | CC BY-NC-SA 2.5 (non-commercial) |
| Spamhaus DROP/EDROP | Free for non-commercial use |
| Tor Project bulk exit list | CC0 / public |

Run `consolidate-lists.py` to fetch the current feeds into `output/` locally.
Review each feed's licence before using the result in a commercial product.
