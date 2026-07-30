# AcmetoCasino Meraki Network Infrastructure

## Infrastructure Scale
- 15 Networks across US states
- 89 Managed devices (MX250, MS350, vMX100)
- Multi-state geographic distribution
- Cloud integration with AWS and virtual appliances

## Hub-and-Spoke VPN Architecture

Primary Hubs: Indiana, Iowa, Michigan
Cloud Hubs: AWS vMX100, IE vMX100, Stage vMX100

### Geographic Distribution
| Region | State | Devices | Network Type |
|--------|-------|---------|-------------|
| East | New Jersey | 4 | VPN Spoke |
| East | Pennsylvania | 4 | VPN Spoke |
| East | Virginia | 4 | VPN Spoke |
| East | Delaware | 4 | VPN Spoke |
| Midwest | Indiana | 4 | VPN Hub (Primary) |
| Midwest | Michigan | 4 | VPN Hub |
| Midwest | Illinois | 4 | VPN Spoke |
| Midwest | Iowa | 4 | VPN Hub (Secondary) |
| South | Tennessee | 4 | VPN Spoke |
| South | Louisiana | 4 | VPN Spoke |
| South | Arizona | 4 | VPN Spoke |
| South | West Virginia | 4 | VPN Spoke |
| Cloud | AWS | 1 | Cloud VPN |
| Cloud | IE VMx | 1 | Cloud VPN |
| Cloud | Stage | 1 | Cloud VPN |

## VPN Communication Flow
```
Internet Traffic:
  External User -> Public IP -> MX250 Firewall -> Internal Network (10.x.x.x)

VPN Traffic:
  Spoke Network -> Encrypted Tunnel -> VPN Hub -> Encrypted Tunnel -> Destination

Cloud Integration:
  On-premises -> IPSec VPN -> AWS Cloud Services (10.12.x.x, 10.13.x.x)
```

## Security Implementation
- Encryption: AES-256-GCM with certificate-based authentication
- Firewall rules: 750+ rules across all networks
- Intrusion detection: IDS/IPS on hub sites
- Content filtering on 3 networks
- VPN failover: Automatic within 30 seconds

## VLAN Architecture
```
VLAN Standards:
  VLAN 1:       Native/Default (192.168.128.0/24)
  VLAN 100-199: User/Department VLANs
  VLAN 200:     Servers Network (most common)
  VLAN 210:     IPMI Management
  VLAN 300-399: Infrastructure VLANs
  VLAN 400-499: Guest/Wireless
```

## Device Inventory
```
MX250 Firewalls:
  11 Primary + 10 Warm Spare units
  Warm spare failover: <30 seconds
  VPN throughput: 1Gbps

MS350-24 Switches:
  24 units, 576 total ports
  PoE+ support, RSTP, LACP

vMX100 Virtual Appliances:
  3 units (AWS, IE, Stage)
```

## Service Level Targets
- Network Availability: 99.9%
- Latency: <50ms for gaming applications
- Packet Loss: <0.1%
- Recovery Time Objective: 4 hours
- Recovery Point Objective: 1 hour

## AWS Third-Party VPN Connections
| Connection | AWS Region | Private Subnet | Local Hub |
|------------|-----------|---------------|-----------|
| VPN-1 | us-east-1 | 10.12.0.0/16 | Indiana |
| VPN-2 | eu-west-1 | 10.13.0.0/16 | Indiana |
| VPN-3 | us-east-1 | 10.0.0.0/16 | New Jersey |
| VPN-4 | eu-west-1 | 10.13.0.0/16 | Pennsylvania |
| VPN-5 | us-east-1 | 10.12.0.0/16 | Indiana |

Encryption: AES-256, IKEv1, certificate authentication
