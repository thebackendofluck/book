<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 16: Cryptocurrency and DeFi Integration

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 16 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Smart contracts, multi-chain wallet infrastructure, Lightning Network, and FATF Travel Rule compliance for crypto-gambling platforms.

## Overview

Production implementations for provably fair on-chain casino logic (Chainlink VRF), hot/cold wallet management across Ethereum, BSC, Polygon and Solana, Lightning Network payment channels, and FATF Travel Rule pipelines. Also includes gas optimisation, stablecoin processing, and Know-Your-Transaction (KYT) monitoring.

## Contents

- `crypto/contracts/` — Solidity smart contracts: `ProvablyFairCasino.sol`, `SecureCasinoWallet.sol`, `BatchProcessor.sol`
- `crypto/services/` — TypeScript: `multi_chain_manager.ts` (HD wallet derivation, multi-chain deposits), `travel_rule_compliance.ts` (FATF originator/beneficiary data)
- `crypto/lightning/` — `lightning_manager.py`: Lightning Network channel management and micro-transaction routing
- `implementation/wallets/` — Python: `hd_wallet.py`, `hot_cold_manager.py`, `multisig_setup.py`
- `implementation/smart-contracts/` — `CasinoVault.sol` + `deploy.py` (Hardhat deployment scripts)
- `implementation/exchange/` — `gas_optimizer.py` (dynamic gas pricing), `rate_feed.py` (crypto-to-fiat rates)
- `implementation/compliance/` — `travel_rule.py`, `kyt_monitor.py` (Chainalysis-compatible), `crypto_tax_reporter.py`
- `implementation/stablecoin/` — `stablecoin_processor.py`: USDC/USDT deposit and settlement flows
- `implementation/docker-compose.yml` — Local Hardhat node + compliance services stack

## Technology Stack

- **Smart Contracts:** Solidity ≥ 0.8, Hardhat, Chainlink VRF v2.5
- **Backend:** Python 3.11+, TypeScript (Node.js 20)
- **Networks:** Ethereum, BSC, Polygon, Solana, Lightning Network (LND)
- **Infrastructure:** Docker Compose

## Prerequisites

- Node.js ≥ 20, Hardhat (`npm install --save-dev hardhat`)
- Python ≥ 3.11 with `web3`, `eth-account`, `lnd-grpc`
- A local Hardhat node or testnet RPC endpoint
- `CHAINLINK_VRF_SUBSCRIPTION_ID`, `PRIVATE_KEY` env vars for contract deployment

## How to Run

```bash
# Start local blockchain + services
docker compose -f implementation/docker-compose.yml up -d

# Deploy smart contracts to local Hardhat
cd implementation/smart-contracts && python deploy.py --network localhost

# Run Lightning channel manager
python crypto/lightning/lightning_manager.py
```

## Security Notes

Hot wallet private keys must be stored in the HSM-backed key store described in Chapter 20. Never pass `PRIVATE_KEY` as a plain env var in production — use OpenBao Transit or AWS KMS. Multisig threshold for cold wallet withdrawals is configured in `multisig_setup.py`.

## Related

- See Chapter 16 in the book for full context on crypto payment architecture and FATF compliance.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
