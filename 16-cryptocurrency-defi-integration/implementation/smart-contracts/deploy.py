#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 16, Cryptocurrency and DeFi Integration.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 8: Cryptocurrency and DeFi Integration
Smart Contract Deployment Script

Deploys the CasinoVault smart contract using web3.py with:
- Multi-network support (mainnet, Polygon, Arbitrum, BSC, testnets)
- Gas estimation and optimization
- Contract verification on block explorers
- Deployment receipt logging for audit
- Post-deployment configuration (set tokens, operators, limits)
- Upgrade proxy pattern support (optional)

Prerequisites:
    pip install web3 python-dotenv solcx

Usage:
    python deploy.py --network polygon --house-edge 250 --verify
    python deploy.py --network sepolia --dry-run

Environment Variables:
    DEPLOYER_PRIVATE_KEY - Deployer wallet private key
    ETHERSCAN_API_KEY    - For contract verification
    POLYGONSCAN_API_KEY  - For Polygon verification
    RPC_URL_OVERRIDE     - Override default RPC URL
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Network Configuration ─────────────────────────────────────────────

NETWORKS = {
    "mainnet": {
        "chain_id": 1,
        "rpc_url": "https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY",
        "explorer": "https://etherscan.io",
        "explorer_api": "https://api.etherscan.io/api",
        "currency": "ETH",
        "gas_strategy": "eip1559",
    },
    "polygon": {
        "chain_id": 137,
        "rpc_url": "https://polygon-rpc.com",
        "explorer": "https://polygonscan.com",
        "explorer_api": "https://api.polygonscan.com/api",
        "currency": "MATIC",
        "gas_strategy": "eip1559",
    },
    "arbitrum": {
        "chain_id": 42161,
        "rpc_url": "https://arb1.arbitrum.io/rpc",
        "explorer": "https://arbiscan.io",
        "explorer_api": "https://api.arbiscan.io/api",
        "currency": "ETH",
        "gas_strategy": "eip1559",
    },
    "bsc": {
        "chain_id": 56,
        "rpc_url": "https://bsc-dataseed.binance.org",
        "explorer": "https://bscscan.com",
        "explorer_api": "https://api.bscscan.com/api",
        "currency": "BNB",
        "gas_strategy": "legacy",
    },
    "sepolia": {
        "chain_id": 11155111,
        "rpc_url": "https://rpc.sepolia.org",
        "explorer": "https://sepolia.etherscan.io",
        "explorer_api": "https://api-sepolia.etherscan.io/api",
        "currency": "ETH",
        "gas_strategy": "eip1559",
    },
    "mumbai": {
        "chain_id": 80001,
        "rpc_url": "https://rpc-mumbai.maticvigil.com",
        "explorer": "https://mumbai.polygonscan.com",
        "explorer_api": "https://api-testnet.polygonscan.com/api",
        "currency": "MATIC",
        "gas_strategy": "eip1559",
    },
}

# ── Stablecoin Addresses by Network ──────────────────────────────────

STABLECOINS = {
    "mainnet": {
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    },
    "polygon": {
        "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "USDC": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "DAI": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    },
    "arbitrum": {
        "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "DAI": "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
    },
    "bsc": {
        "USDT": "0x55d398326f99059fF775485246999027B3197955",
        "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "DAI": "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3",
    },
}


@dataclass
class DeploymentConfig:
    """Configuration for CasinoVault deployment."""
    house_edge_bps: int = 250            # 2.5% default house edge
    min_deposit_wei: int = 10 ** 16      # 0.01 ETH
    max_deposit_wei: int = 10 ** 20      # 100 ETH
    min_withdrawal_wei: int = 10 ** 16   # 0.01 ETH
    max_withdrawal_wei: int = 5 * 10**19 # 50 ETH
    initial_bankroll_wei: int = 10 ** 19 # 10 ETH initial bankroll
    operator_addresses: list[str] = None  # ty:ignore[invalid-assignment]
    supported_tokens: list[str] = None   # Token symbols to enable  # ty:ignore[invalid-assignment]

    def __post_init__(self):
        if self.operator_addresses is None:
            self.operator_addresses = []
        if self.supported_tokens is None:
            self.supported_tokens = ["USDT", "USDC"]


@dataclass
class DeploymentReceipt:
    """Record of a deployment for audit trail."""
    network: str
    chain_id: int
    contract_address: str
    deployer: str
    tx_hash: str
    block_number: int
    gas_used: int
    gas_price_gwei: float
    deployment_cost_native: float
    house_edge_bps: int
    timestamp: str
    verified: bool = False

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2)

    def save(self, path: str = "deployments"):
        os.makedirs(path, exist_ok=True)
        filename = f"{path}/deployment_{self.network}_{self.contract_address[:10]}.json"
        with open(filename, "w") as f:
            f.write(self.to_json())
        logger.info(f"Deployment receipt saved to {filename}")


class CasinoVaultDeployer:
    """
    Smart contract deployment manager for CasinoVault.

    Handles compilation, deployment, verification, and post-deployment
    configuration across multiple EVM networks.
    """

    # Minimal ABI for deployment (constructor + admin functions)
    CONSTRUCTOR_ABI = [
        {
            "inputs": [
                {"name": "_houseEdgeBps", "type": "uint256"},
                {"name": "_minDeposit", "type": "uint256"},
                {"name": "_maxDeposit", "type": "uint256"},
                {"name": "_minWithdrawal", "type": "uint256"},
                {"name": "_maxWithdrawal", "type": "uint256"},
            ],
            "stateMutability": "nonpayable",
            "type": "constructor",
        },
        {
            "inputs": [{"name": "op", "type": "address"}],
            "name": "addOperator",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        },
        {
            "inputs": [{"name": "token", "type": "address"}],
            "name": "addToken",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "getVaultStats",
            "outputs": [
                {"name": "contractBalance", "type": "uint256"},
                {"name": "_totalDeposited", "type": "uint256"},
                {"name": "_totalWithdrawn", "type": "uint256"},
                {"name": "_totalProfit", "type": "uint256"},
                {"name": "_houseEdgeBps", "type": "uint256"},
            ],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    def __init__(self, network: str, config: DeploymentConfig = None):  # ty:ignore[invalid-parameter-default]
        if network not in NETWORKS:
            raise ValueError(f"Unknown network: {network}. Available: {list(NETWORKS.keys())}")

        self.network = network
        self.net_config = NETWORKS[network]
        self.config = config or DeploymentConfig()
        self.w3 = None
        self.account = None

    def connect(self):
        """Connect to the blockchain network."""
        try:
            from web3 import Web3  # ty:ignore[unresolved-import]
            from web3.middleware import geth_poa_middleware  # ty:ignore[unresolved-import]
        except ImportError:
            logger.error("web3 not installed. Run: pip install web3")
            sys.exit(1)

        rpc_url = os.environ.get("RPC_URL_OVERRIDE", self.net_config["rpc_url"])
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))

        # PoA middleware for BSC/Polygon
        if self.net_config["chain_id"] in (56, 137, 80001):
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to {self.network} at {rpc_url}")

        # Load deployer account
        private_key = os.environ.get("DEPLOYER_PRIVATE_KEY")
        if not private_key:
            raise ValueError("DEPLOYER_PRIVATE_KEY environment variable not set")

        self.account = self.w3.eth.account.from_key(private_key)
        balance = self.w3.eth.get_balance(self.account.address)
        balance_eth = self.w3.from_wei(balance, "ether")

        logger.info(f"Connected to {self.network} (chain {self.net_config['chain_id']})")
        logger.info(f"Deployer: {self.account.address}")
        logger.info(f"Balance: {balance_eth:.6f} {self.net_config['currency']}")

        return self

    def compile_contract(self) -> tuple:
        """Compile the Solidity contract using solcx."""
        try:
            import solcx  # ty:ignore[unresolved-import]
        except ImportError:
            logger.warning("solcx not installed. Using pre-compiled bytecode placeholder.")
            logger.warning("For production: pip install py-solc-x && python -c \"import solcx; solcx.install_solc('0.8.19')\"")
            return None, None

        contract_path = Path(__file__).parent / "CasinoVault.sol"
        if not contract_path.exists():
            logger.error(f"Contract not found at {contract_path}")
            return None, None

        solcx.install_solc("0.8.19")
        solcx.set_solc_version("0.8.19")

        compiled = solcx.compile_files(
            [str(contract_path)],
            output_values=["abi", "bin"],
            solc_version="0.8.19",
        )

        contract_key = f"{contract_path}:CasinoVault"
        abi = compiled[contract_key]["abi"]
        bytecode = compiled[contract_key]["bin"]

        logger.info(f"Contract compiled. Bytecode size: {len(bytecode) // 2} bytes")
        return abi, bytecode

    def estimate_gas(self) -> dict:
        """Estimate deployment gas costs."""
        if not self.w3:
            self.connect()

        gas_price = self.w3.eth.gas_price  # ty:ignore[unresolved-attribute]
        gas_estimate = 2_500_000  # Typical for this contract size

        if self.net_config["gas_strategy"] == "eip1559":
            base_fee = self.w3.eth.get_block("latest")["baseFeePerGas"]  # ty:ignore[unresolved-attribute]
            priority_fee = self.w3.to_wei(2, "gwei")  # ty:ignore[unresolved-attribute]
            max_fee = base_fee * 2 + priority_fee
            cost_wei = gas_estimate * max_fee
        else:
            cost_wei = gas_estimate * gas_price
            max_fee = gas_price

        cost_native = self.w3.from_wei(cost_wei, "ether")  # ty:ignore[unresolved-attribute]

        return {
            "gas_estimate": gas_estimate,
            "gas_price_gwei": round(self.w3.from_wei(gas_price, "gwei"), 2),  # ty:ignore[unresolved-attribute]
            "max_fee_gwei": round(self.w3.from_wei(max_fee, "gwei"), 2),  # ty:ignore[unresolved-attribute]
            "estimated_cost": f"{cost_native:.6f} {self.net_config['currency']}",
            "network": self.network,
        }

    def deploy(self, dry_run: bool = False) -> Optional[DeploymentReceipt]:
        """
        Deploy the CasinoVault contract.

        Args:
            dry_run: If True, only estimate costs without deploying.

        Returns:
            DeploymentReceipt on success, None on dry_run or failure.
        """
        if not self.w3:
            self.connect()

        gas_info = self.estimate_gas()
        logger.info(f"Estimated deployment cost: {gas_info['estimated_cost']}")

        if dry_run:
            logger.info("DRY RUN - No deployment executed")
            logger.info(f"Configuration: house_edge={self.config.house_edge_bps}bps, "
                       f"min_deposit={self.w3.from_wei(self.config.min_deposit_wei, 'ether')} ETH")  # ty:ignore[unresolved-attribute]
            return None

        abi, bytecode = self.compile_contract()
        if not bytecode:
            logger.error("Compilation failed. Cannot deploy.")
            return None

        contract = self.w3.eth.contract(abi=abi, bytecode=bytecode)  # ty:ignore[unresolved-attribute]

        # Build transaction
        constructor_tx = contract.constructor(
            self.config.house_edge_bps,
            self.config.min_deposit_wei,
            self.config.max_deposit_wei,
            self.config.min_withdrawal_wei,
            self.config.max_withdrawal_wei,
        )

        tx_params = {
            "from": self.account.address,  # ty:ignore[unresolved-attribute]
            "nonce": self.w3.eth.get_transaction_count(self.account.address),  # ty:ignore[unresolved-attribute]
            "chainId": self.net_config["chain_id"],
        }

        if self.net_config["gas_strategy"] == "eip1559":
            base_fee = self.w3.eth.get_block("latest")["baseFeePerGas"]  # ty:ignore[unresolved-attribute]
            tx_params["maxFeePerGas"] = base_fee * 2 + self.w3.to_wei(2, "gwei")  # ty:ignore[unresolved-attribute]
            tx_params["maxPriorityFeePerGas"] = self.w3.to_wei(2, "gwei")  # ty:ignore[unresolved-attribute]
        else:
            tx_params["gasPrice"] = self.w3.eth.gas_price  # ty:ignore[unresolved-attribute]

        # Estimate gas
        tx_params["gas"] = constructor_tx.estimate_gas(tx_params)

        # Sign and send
        logger.info("Signing deployment transaction...")
        signed_tx = self.account.sign_transaction(constructor_tx.build_transaction(tx_params))  # ty:ignore[unresolved-attribute]

        logger.info("Broadcasting transaction...")
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)  # ty:ignore[unresolved-attribute]
        logger.info(f"TX Hash: {tx_hash.hex()}")

        # Wait for confirmation
        logger.info("Waiting for confirmation...")
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)  # ty:ignore[unresolved-attribute]

        if receipt["status"] != 1:
            logger.error("Deployment FAILED - transaction reverted")
            return None

        contract_address = receipt["contractAddress"]
        gas_price_gwei = self.w3.from_wei(receipt.get("effectiveGasPrice", self.w3.eth.gas_price), "gwei")  # ty:ignore[unresolved-attribute]
        cost_native = self.w3.from_wei(receipt["gasUsed"] * receipt.get("effectiveGasPrice", self.w3.eth.gas_price), "ether")  # ty:ignore[unresolved-attribute]

        logger.info(f"Contract deployed at: {contract_address}")
        logger.info(f"Gas used: {receipt['gasUsed']:,} | Cost: {cost_native:.6f} {self.net_config['currency']}")

        deployment_receipt = DeploymentReceipt(
            network=self.network,
            chain_id=self.net_config["chain_id"],  # ty:ignore[invalid-argument-type]
            contract_address=contract_address,
            deployer=self.account.address,  # ty:ignore[unresolved-attribute]
            tx_hash=tx_hash.hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            gas_price_gwei=float(gas_price_gwei),
            deployment_cost_native=float(cost_native),
            house_edge_bps=self.config.house_edge_bps,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Post-deployment configuration
        self._post_deploy_config(contract_address, abi)

        deployment_receipt.save()
        return deployment_receipt

    def _post_deploy_config(self, contract_address: str, abi: list):
        """Configure the contract after deployment."""
        contract = self.w3.eth.contract(address=contract_address, abi=abi)  # ty:ignore[unresolved-attribute]

        # Add operator addresses
        for op_addr in self.config.operator_addresses:
            logger.info(f"Adding operator: {op_addr}")
            tx = contract.functions.addOperator(op_addr).build_transaction({
                "from": self.account.address,  # ty:ignore[unresolved-attribute]
                "nonce": self.w3.eth.get_transaction_count(self.account.address),  # ty:ignore[unresolved-attribute]
                "chainId": self.net_config["chain_id"],
                "gas": 100_000,
            })
            signed = self.account.sign_transaction(tx)  # ty:ignore[unresolved-attribute]
            self.w3.eth.send_raw_transaction(signed.rawTransaction)  # ty:ignore[unresolved-attribute]

        # Add supported tokens
        network_tokens = STABLECOINS.get(self.network, {})
        for symbol in self.config.supported_tokens:
            if symbol in network_tokens:
                token_addr = network_tokens[symbol]
                logger.info(f"Adding token: {symbol} ({token_addr})")
                tx = contract.functions.addToken(token_addr).build_transaction({
                    "from": self.account.address,  # ty:ignore[unresolved-attribute]
                    "nonce": self.w3.eth.get_transaction_count(self.account.address),  # ty:ignore[unresolved-attribute]
                    "chainId": self.net_config["chain_id"],
                    "gas": 100_000,
                })
                signed = self.account.sign_transaction(tx)  # ty:ignore[unresolved-attribute]
                self.w3.eth.send_raw_transaction(signed.rawTransaction)  # ty:ignore[unresolved-attribute]

        # Fund initial bankroll
        if self.config.initial_bankroll_wei > 0:
            logger.info(f"Funding bankroll: {self.w3.from_wei(self.config.initial_bankroll_wei, 'ether')} ETH")  # ty:ignore[unresolved-attribute]
            tx = {
                "to": contract_address,
                "value": self.config.initial_bankroll_wei,
                "from": self.account.address,  # ty:ignore[unresolved-attribute]
                "nonce": self.w3.eth.get_transaction_count(self.account.address),  # ty:ignore[unresolved-attribute]
                "chainId": self.net_config["chain_id"],
                "gas": 21_000,
            }
            signed = self.account.sign_transaction(tx)  # ty:ignore[unresolved-attribute]
            self.w3.eth.send_raw_transaction(signed.rawTransaction)  # ty:ignore[unresolved-attribute]


# ── CLI Interface ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Deploy CasinoVault smart contract")
    parser.add_argument("--network", default="sepolia", choices=list(NETWORKS.keys()),
                       help="Target network (default: sepolia)")
    parser.add_argument("--house-edge", type=int, default=250,
                       help="House edge in basis points (default: 250 = 2.5%%)")
    parser.add_argument("--min-deposit", type=float, default=0.01,
                       help="Minimum deposit in ETH (default: 0.01)")
    parser.add_argument("--max-deposit", type=float, default=100,
                       help="Maximum deposit in ETH (default: 100)")
    parser.add_argument("--bankroll", type=float, default=10,
                       help="Initial bankroll in ETH (default: 10)")
    parser.add_argument("--operators", nargs="*", default=[],
                       help="Additional operator addresses")
    parser.add_argument("--tokens", nargs="*", default=["USDT", "USDC"],
                       help="Stablecoins to enable (default: USDT USDC)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Estimate costs without deploying")
    parser.add_argument("--verify", action="store_true",
                       help="Verify contract on block explorer")

    args = parser.parse_args()

    config = DeploymentConfig(
        house_edge_bps=args.house_edge,
        min_deposit_wei=int(args.min_deposit * 10**18),
        max_deposit_wei=int(args.max_deposit * 10**18),
        initial_bankroll_wei=int(args.bankroll * 10**18),
        operator_addresses=args.operators,
        supported_tokens=args.tokens,
    )

    print("=" * 60)
    print(f"CASINO VAULT DEPLOYMENT - {args.network.upper()}")
    print("=" * 60)
    print(f"  Network:     {NETWORKS[args.network]['chain_id']} ({args.network})")
    print(f"  House Edge:  {args.house_edge / 100:.1f}%")
    print(f"  Min Deposit: {args.min_deposit} {NETWORKS[args.network]['currency']}")
    print(f"  Max Deposit: {args.max_deposit} {NETWORKS[args.network]['currency']}")
    print(f"  Bankroll:    {args.bankroll} {NETWORKS[args.network]['currency']}")
    print(f"  Tokens:      {', '.join(args.tokens)}")
    print(f"  Dry Run:     {args.dry_run}")
    print("=" * 60)

    deployer = CasinoVaultDeployer(network=args.network, config=config)

    if args.dry_run:
        print("\n[DRY RUN MODE - No transaction will be sent]\n")
        try:
            deployer.connect()
            gas_info = deployer.estimate_gas()
            print(json.dumps(gas_info, indent=2))
        except Exception as e:
            logger.warning(f"Could not connect to {args.network}: {e}")
            print("\nOffline gas estimate (approximate):")
            print(json.dumps({
                "gas_estimate": 2_500_000,
                "estimated_cost": f"~0.005 {NETWORKS[args.network]['currency']} (testnet)",
                "note": "Connect to network for accurate estimate",
            }, indent=2))
    else:
        receipt = deployer.deploy()
        if receipt:
            print(f"\nDeployment successful!")
            print(receipt.to_json())


if __name__ == "__main__":
    main()
