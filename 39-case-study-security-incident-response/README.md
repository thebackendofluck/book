<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 39: Case Study: Security Incident Response

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 39 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Incident response tooling for online casino security teams, covering detection, containment, forensics, regulatory notification, and system recovery. Includes a dedicated Log4Shell (CVE-2021-44228) response kit.

## Contents

- `incident_response/` - Python incident response framework:
  - `incident_detection.py` - SIEM integration and anomaly detection for identifying breaches
  - `network_containment.py` - Automated network isolation and firewall rule injection
  - `digital_forensics.py` - Evidence collection, memory dumps, and disk imaging workflows
  - `regulatory_compliance.py` - Automated regulator notification (UKGC, MGA, DGE) with required timelines
  - `system_recovery.py` - Staged recovery procedures with integrity verification
- `log4shell-response/` - Log4Shell-specific response tools:
  - `log4shell_response.sh` - Bash remediation script for scanning and patching Log4j vulnerabilities
  - `log4shell_scanner.nse` - Nmap NSE script for network-wide Log4Shell detection

## Technology Stack

- **Incident response:** Python
- **Network scanning:** Nmap (NSE scripts), Bash
- **SIEM integration:** Elasticsearch, Splunk-compatible
- **Forensics:** Standard Linux forensics toolchain

## Key Concepts

- **Regulatory Timelines** - UKGC requires notification within 72 hours; some jurisdictions require faster disclosure
- **Evidence Preservation** - Chain-of-custody procedures critical for potential law enforcement involvement
- **Staged Recovery** - Bringing systems back in dependency order with integrity checks at each stage

## Related

- See Chapter 39 in the book for the full security incident response case study
