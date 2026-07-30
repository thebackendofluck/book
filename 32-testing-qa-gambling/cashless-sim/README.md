# Cashless Gaming Simulator

Production-derived testing tool for cashless gaming operations on an iGaming platform.
Includes both a GUI-based interactive simulator and a chaos/load testing variant.

## Context (Chapter 32 - Testing & QA in Gambling)

Cashless gaming (transferring funds between a player's online wallet and a physical
slot machine via SAS protocol) requires thorough end-to-end testing. This simulator
provides two tools:

1. **`simulator.py`** - Interactive GUI for manual QA testing of the cashless flow:
   register, login, deposit, transfer to/from machine, set player limits
2. **`chaos_simulator.py`** - Multi-threaded load/chaos testing tool that creates
   concurrent players performing deposits, debits, and credits to stress-test
   the platform under load

## Requirements

- Python 3.x
- pip3

## Installation

```bash
pip3 install -r requirements.txt
```

## Usage

### Interactive Testing
```bash
python simulator.py
```
Opens a Tkinter GUI with buttons for each cashless operation.

### Load/Chaos Testing
```bash
python chaos_simulator.py
```
Opens a GUI that includes a "Launch Load Test" button which spawns configurable
threads performing concurrent registrations, deposits, debits, and credits.

## Architecture

The simulator targets the platform's User Gateway API, which is the central
entry point for all player-facing operations. It demonstrates the hub-and-spoke
architecture used in US regulated markets, where a central hub routes requests
to jurisdiction-specific spoke instances.

## Key API Operations

| Operation | API Type | Description |
|-----------|----------|-------------|
| `registeruser` | POST | Create new player account |
| `userlogin` | POST | Authenticate and get session |
| `verifyuser` | POST | KYC verification trigger |
| `geoverify-lease` | POST | Geolocation verification |
| `getbalance` | POST | Query player balance |
| `to-machine` | POST | Transfer funds to slot machine |
| `from-machine` | POST | Transfer funds from slot machine |
| `deposit` | POST | Internal test deposit |
| `deposit-limit-update` | POST | Set responsible gaming limits |
