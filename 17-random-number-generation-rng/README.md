<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 17: Random Number Generation (RNG)

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 17 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

Enterprise-grade Random Number Generation implementation for iGaming platforms.

## Overview

This module provides cryptographically secure RNG implementations that meet GLI-11 and GLI-19 certification requirements for online gambling platforms.

## Directory Structure

```
scripts/chapter-17/
├── README.md                    # This file
└── rng-system/                  # Python RNG module (3,276 lines total)
    ├── __init__.py              # Module exports (87 lines)
    ├── entropy.py               # Hardware entropy collection (317 lines)
    ├── prng.py                  # Cryptographic PRNGs (404 lines)
    ├── shuffle.py               # Fisher-Yates shuffle (352 lines)
    ├── game_outcomes.py         # Game-specific RNG adapters (505 lines)
    ├── testing.py               # NIST statistical tests (505 lines)
    └── performance_testing.py   # High-performance parallel testing (1,106 lines)
```

## Components

### Entropy Collection (`entropy.py`)

Hardware-based entropy sources for cryptographic seeding.

| Source | Description | Quality |
|--------|-------------|---------|
| Zymkey TRNG | Hardware security module | Excellent |
| `/dev/hwrng` | Linux hardware RNG | Good |
| `os.urandom` | Kernel entropy pool | Good |
| Timestamp | Nanosecond precision | Supplementary |

**Features:**
- Multiple entropy source mixing (defense in depth)
- Entropy pool management
- Health monitoring for entropy quality

### Cryptographic PRNGs (`prng.py`)

NIST SP 800-90A compliant random number generators.

| Algorithm | Security Level | Speed | Use Case |
|-----------|---------------|-------|----------|
| AES-256-CTR | 256-bit | Fast | Production |
| SimpleLCG | None | Very Fast | Educational only |

**Security Properties:**
- Prediction resistance
- Backtracking resistance
- 2^128+ period
- Audit logging

### Fisher-Yates Shuffle (`shuffle.py`)

The only mathematically correct shuffle for casino card games.

```python
from rng_system.shuffle import create_card_deck, fisher_yates_shuffle

deck = create_card_deck(num_decks=6)  # Blackjack shoe
shuffled = fisher_yates_shuffle(deck, rng)
```

**Validation:**
- Chi-square position distribution test
- Pair frequency analysis
- Card preservation verification

### Game Adapters (`game_outcomes.py`)

Pre-built RNG wrappers for common casino games.

| Game | Class | Features |
|------|-------|----------|
| Slots | `SlotRNG` | Weighted symbols, RTP configuration |
| Cards | `CardRNG` | Multi-deck shoes, penetration tracking |
| Dice | `DiceRNG` | Configurable sides |
| Roulette | `RouletteRNG` | European/American wheels |
| Lottery | `LotteryRNG` | With/without replacement |

### Statistical Testing (`testing.py`)

NIST SP 800-22 test suite implementation.

| Test | Description | Pass Criteria |
|------|-------------|---------------|
| Monobit | Equal 0s and 1s | p-value ≥ 0.01 |
| Runs | Run length distribution | p-value ≥ 0.01 |
| Poker | Pattern distribution | p-value ≥ 0.01 |
| Autocorrelation | Bit independence | p-value ≥ 0.01 |

### High-Performance Testing (`performance_testing.py`)

Enterprise-grade parallel RNG validation with multiprocessing support.

| Feature | Description |
|---------|-------------|
| Parallel Execution | ProcessPoolExecutor/ThreadPoolExecutor |
| Chunk Processing | Configurable chunk sizes for large datasets |
| Multi-Worker | Auto-scales to CPU core count |
| 10M+ Samples | Handles massive sample sizes efficiently |

**Test Capabilities:**

| Test Type | Parallel Method | Throughput |
|-----------|-----------------|------------|
| Monobit | ProcessPoolExecutor | ~5M samples/sec |
| Runs | ProcessPoolExecutor | ~3M samples/sec |
| Chi-Square | ProcessPoolExecutor | ~4M samples/sec |
| Poker | ProcessPoolExecutor | ~2M samples/sec |
| Full Validation | Combined parallel | ~1M samples/sec |

**Usage:**

```python
from rng_system.performance_testing import HighPerformanceRNGTester

# Create tester with auto-detected CPU cores
tester = HighPerformanceRNGTester(num_workers=8, chunk_size=100_000)

# Run parallel validation (10M samples)
rng = create_casino_rng()
result = tester.run_full_validation(rng, num_samples=10_000_000)

print(f"Score: {result.score:.1f}%")
print(f"Valid: {result.is_valid}")
print(f"Time: {result.total_time_ms:.0f}ms")
print(f"Throughput: {result.throughput_per_sec:.0f} samples/sec")
```

**Parallel Test Methods:**

```python
# Run specific tests in parallel
monobit_results = tester.run_parallel_monobit_tests(rng, num_samples=1_000_000)
runs_results = tester.run_parallel_runs_tests(rng, num_samples=1_000_000)
chi_square_results = tester.run_parallel_chi_square_tests(rng, num_samples=1_000_000)
poker_results = tester.run_parallel_poker_tests(rng, num_samples=1_000_000)
```

## Installation

```bash
# Using pip
pip install cryptography

# Using uv (recommended)
uv pip install cryptography
```

## Usage Examples

### Basic RNG Usage

```python
from rng_system import create_casino_rng

# Create casino-grade RNG
rng = create_casino_rng()

# Generate random values
card_index = rng.random_int(0, 51)
dice_roll = rng.random_int(1, 6)
probability = rng.random_float()
```

### Slot Machine

```python
from rng_system import SlotRNG

slot = SlotRNG(
    symbols={
        "WILD": {"weight": 2, "payout": 1000},
        "SEVEN": {"weight": 5, "payout": 500},
        "BAR": {"weight": 10, "payout": 100},
    },
    num_reels=5,
    rtp_target=0.96
)

result = slot.spin()
print(f"Symbols: {result}")
```

### Card Games

```python
from rng_system import CardRNG

# 6-deck blackjack shoe
shoe = CardRNG(num_decks=6, penetration=0.75)

# Deal initial hands
player_hand = shoe.deal(2)
dealer_hand = shoe.deal(2)

print(f"Player: {player_hand}")
print(f"Dealer: {dealer_hand}")
```

### Validation

```python
from rng_system import create_casino_rng, run_casino_validation

rng = create_casino_rng()
result = run_casino_validation(rng, num_samples=1_000_000)

print(f"Valid: {result.is_valid}")
print(f"Tests passed: {result.tests_passed}/{result.tests_total}")
```

## Regulatory Compliance

| Standard | Requirement | Implementation |
|----------|-------------|----------------|
| GLI-11 | Cryptographic RNG | AES-256-CTR DRBG |
| GLI-19 | RNG statistical testing | NIST SP 800-22 suite |
| GLI-11 | Fisher-Yates shuffle | Correct implementation |
| ISO 17025 | Audit trail | Per-generation logging |

## Security Considerations

1. **Seed Management**: Seeds must come from hardware entropy sources
2. **Key Rotation**: DRBG keys should be rotated periodically
3. **Audit Logging**: All RNG operations are logged for compliance
4. **No LCG**: SimpleLCG is for education only, never production

## Integration with Chapter 18 (RTC)

This RNG module integrates with the RTC system (Chapter 18) for:
- Timestamp entropy contribution
- Hardware RTC nanosecond precision for seeding
- Zymkey shared entropy pool

```python
# Using RTC timestamps for RNG entropy
from chapter_40.rtc_system import get_authoritative_time
from chapter_39.rng_system import create_casino_rng

time, source = get_authoritative_time()
rng = create_casino_rng(use_hardware_entropy=True)
```

## Verification

```bash
# Type check Python modules
ty check scripts/chapter-17/rng-system/*.py

# Run validation
python -c "from rng_system import run_casino_validation, create_casino_rng; print(run_casino_validation(create_casino_rng(), 10000).summary)"
```

## License

MIT
