# Prerequisites — postgres-vm provisioning scripts

## Required host packages

| Package | Purpose |
|---|---|
| `libvirt-daemon-system`, `libvirt-clients` | VM lifecycle (virsh, virt-install) |
| `qemu-kvm`, `virt-install` | KVM hypervisor and VM installer |
| `cloud-image-utils` (`cloud-localds`) | Build cloud-init seed ISO |
| `postgresql-client-16` | psql for post-provision verification |
| `pgbackrest` | Backup tool installed inside VM |
| `softhsm2` | Software HSM for testing (`--hsm-type softhsm`) |
| `shellcheck` | Shell script linting |

Install on Ubuntu/Debian:
```bash
apt-get install -y \
  libvirt-daemon-system libvirt-clients qemu-kvm virt-install \
  cloud-image-utils postgresql-client-16 pgbackrest \
  softhsm2 opensc shellcheck
```

## Optional packages (production)

| Package | Purpose |
|---|---|
| `yubihsm2-connector`, `yubihsm-shell` | Physical YubiHSM2 key management (`--hsm-type yubihsm`) |
| `vault` CLI | HashiCorp Vault KV for LUKS passphrase (`--hsm-type vault`) |
| `ansible` | Automated configuration management |
| `numactl` | NUMA topology detection and CPU pinning |
| `fio` | Disk benchmark during verification phase |

## Minimum host resources

| Resource | Minimum | Recommended (production) |
|---|---|---|
| RAM free | 4 GB | 16 GB per VM |
| CPU cores | 2 | 8+ |
| Disk (provisioned path) | 300 GB on `/nvme-0-zfs` | 1 TB+ on NVMe ZFS pool |

## Network

- Bridge interface `br1` on `10.0.10.0/24` (net=50, default)
- Bridge interface `br120` on `10.100.2.0/24` (net=120)
- DNS: `10.0.10.242, 10.0.10.1, 8.8.8.8`

## Golden image

Ubuntu 24.04 LTS cloud image at:
```
/nvme-0-zfs/vms/golden/noble-golden-generic.qcow2
```

Download:
```bash
wget -O /nvme-0-zfs/vms/golden/noble-golden-generic.qcow2 \
  https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
```

## HSM setup

### SoftHSM2 (default, testing)
```bash
softhsm2-util --init-token --free --label casino-db --pin 5678 --so-pin 5678
softhsm2-util --list-tokens
```

### YubiHSM2 (production)
```bash
# Verify connector is running
systemctl status yubihsm-connector
yubihsm-shell -a get-device-info

# Create wrap key for LUKS (label: pg-luks-wrap)
yubihsm-shell -a generate-wrap-key \
  --authkey 1 --password 5678 \
  --label pg-luks-wrap \
  --algorithm aes256-ccm-wrap \
  --capabilities wrap-data,unwrap-data
```

### HashiCorp Vault (production, backed by YubiHSM)
```bash
# Store LUKS passphrase
vault kv put secret/database/luks passphrase="$(openssl rand -hex 32)"

# Verify
vault kv get -field=passphrase secret/database/luks
```
