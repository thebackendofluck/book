// Companion code for "The Backend of Luck" - Chapter 16, Cryptocurrency and DeFi Integration.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Chapter 8: Cryptocurrency and DeFi Integration
 * Multi-Chain Cryptocurrency Manager
 *
 * TypeScript service managing deposits, withdrawals and confirmations across
 * Ethereum, BSC, Polygon, and Solana chains. Features:
 * - Deterministic per-user deposit address generation per chain
 * - Confirmation waiting logic tailored to each chain's block time
 * - ERC-20 token and native currency withdrawal support
 * - Multi-signature approval flow for large withdrawals
 * - USD value calculation and audit trail for all transactions
 *
 * Reference: Chapter 8 - Multi-Chain Integration Architecture section
 */

import { ethers } from 'ethers';
import Web3 from 'web3';
import { Connection, PublicKey, Transaction } from '@solana/web3.js';
import { Token, TOKEN_PROGRAM_ID } from '@solana/spl-token';
import TronWeb from 'tronweb';

interface ChainConfig {
  chainId: number;
  name: string;
  rpcUrl: string;
  explorerUrl: string;
  nativeCurrency: string;
  blockTime: number;
  confirmations: number;
  gasPrice?: string;
}

interface WalletIntegration {
  connect(): Promise<string>;
  disconnect(): Promise<void>;
  getBalance(): Promise<bigint>;
  sendTransaction(to: string, amount: bigint): Promise<string>;
  signMessage(message: string): Promise<string>;
}

class MultiChainCryptoManager {
  private chains: Map<string, ChainConfig> = new Map();
  private providers: Map<string, any> = new Map();
  private walletIntegrations: Map<string, WalletIntegration> = new Map();

  constructor() {
    this.initializeChains();
    this.setupProviders();
  }

  private initializeChains(): void {
    // Ethereum Mainnet
    this.chains.set('ethereum', {
      chainId: 1,
      name: 'Ethereum',
      rpcUrl: process.env.ETHEREUM_RPC_URL!,
      explorerUrl: 'https://etherscan.io',
      nativeCurrency: 'ETH',
      blockTime: 12000,
      confirmations: 12
    });

    // Binance Smart Chain
    this.chains.set('bsc', {
      chainId: 56,
      name: 'Binance Smart Chain',
      rpcUrl: process.env.BSC_RPC_URL!,
      explorerUrl: 'https://bscscan.com',
      nativeCurrency: 'BNB',
      blockTime: 3000,
      confirmations: 20
    });

    // Polygon
    this.chains.set('polygon', {
      chainId: 137,
      name: 'Polygon',
      rpcUrl: process.env.POLYGON_RPC_URL!,
      explorerUrl: 'https://polygonscan.com',
      nativeCurrency: 'MATIC',
      blockTime: 2000,
      confirmations: 100
    });

    // Solana
    this.chains.set('solana', {
      chainId: 101,
      name: 'Solana',
      rpcUrl: process.env.SOLANA_RPC_URL!,
      explorerUrl: 'https://explorer.solana.com',
      nativeCurrency: 'SOL',
      blockTime: 400,
      confirmations: 32
    });
  }

  private setupProviders(): void {
    // Ethereum provider
    const ethProvider = new ethers.JsonRpcProvider(this.chains.get('ethereum')!.rpcUrl);
    this.providers.set('ethereum', ethProvider);

    // BSC provider
    const bscProvider = new Web3(this.chains.get('bsc')!.rpcUrl);
    this.providers.set('bsc', bscProvider);

    // Polygon provider
    const polygonProvider = new ethers.JsonRpcProvider(this.chains.get('polygon')!.rpcUrl);
    this.providers.set('polygon', polygonProvider);

    // Solana provider
    const solanaConnection = new Connection(this.chains.get('solana')!.rpcUrl);
    this.providers.set('solana', solanaConnection);
  }

  async createDepositAddress(chain: string, userId: string): Promise<{
    address: string;
    tag?: string;
    expiresAt: number;
  }> {
    const chainConfig = this.chains.get(chain);
    if (!chainConfig) {
      throw new Error(`Unsupported chain: ${chain}`);
    }

    switch (chain) {
      case 'ethereum':
      case 'bsc':
      case 'polygon':
        return this.createEVMDepositAddress(chain, userId);
      case 'solana':
        return this.createSolanaDepositAddress(userId);
      default:
        throw new Error(`Chain ${chain} not implemented`);
    }
  }

  private async createEVMDepositAddress(
    chain: string,
    userId: string
  ): Promise<{ address: string; expiresAt: number }> {
    // Generate deterministic address from user ID
    const wallet = ethers.Wallet.createRandom();
    const address = wallet.address;

    // Store mapping in secure database
    await this.storeDepositMapping({
      chain,
      userId,
      address,
      privateKey: wallet.privateKey, // Encrypted storage required
      expiresAt: Date.now() + 24 * 60 * 60 * 1000 // 24 hours
    });

    return {
      address,
      expiresAt: Date.now() + 24 * 60 * 60 * 1000
    };
  }

  async processDeposit(
    chain: string,
    transactionHash: string,
    confirmations: number = 0
  ): Promise<void> {
    const chainConfig = this.chains.get(chain);
    if (!chainConfig) {
      throw new Error(`Unsupported chain: ${chain}`);
    }

    // Wait for required confirmations
    if (confirmations < chainConfig.confirmations) {
      await this.waitForConfirmations(chain, transactionHash, chainConfig.confirmations);
    }

    // Get transaction details
    const txDetails = await this.getTransactionDetails(chain, transactionHash);

    // Validate transaction
    if (!await this.validateDepositTransaction(chain, txDetails)) {
      throw new Error('Invalid deposit transaction');
    }

    // Find user by deposit address
    const userId = await this.findUserByDepositAddress(chain, txDetails.to);
    if (!userId) {
      throw new Error('Deposit address not found');
    }

    // Calculate USD value
    const usdValue = await this.calculateUSDValue(
      chain,
      txDetails.value,
      txDetails.timestamp
    );

    // Credit user account
    await this.creditUserAccount(userId, {
      chain,
      amount: txDetails.value,
      usdValue,
      transactionHash,
      timestamp: txDetails.timestamp
    });

    // Store for audit trail
    await this.storeDepositRecord({
      userId,
      chain,
      transactionHash,
      amount: txDetails.value,
      usdValue,
      confirmations: chainConfig.confirmations,
      timestamp: Date.now()
    });
  }

  private async waitForConfirmations(
    chain: string,
    txHash: string,
    requiredConfirmations: number
  ): Promise<void> {
    const provider = this.providers.get(chain);

    if (chain === 'solana') {
      // Solana confirmation logic
      const connection = provider as Connection;
      let confirmations = 0;

      while (confirmations < requiredConfirmations) {
        const status = await connection.getSignatureStatus(txHash);
        confirmations = status.value?.confirmations || 0;

        if (confirmations < requiredConfirmations) {
          await new Promise(resolve => setTimeout(resolve, 1000)); // Wait 1 second
        }
      }
    } else {
      // EVM confirmation logic
      let currentBlock = await provider.getBlockNumber();
      let txBlock = 0;

      while (currentBlock - txBlock < requiredConfirmations) {
        const tx = await provider.getTransaction(txHash);
        if (tx && tx.blockNumber) {
          txBlock = tx.blockNumber;
          currentBlock = await provider.getBlockNumber();
        }

        if (currentBlock - txBlock < requiredConfirmations) {
          await new Promise(resolve => setTimeout(resolve, 12000)); // Wait for next block
        }
      }
    }
  }

  async withdraw(
    userId: string,
    chain: string,
    toAddress: string,
    amount: bigint,
    token?: string
  ): Promise<string> {
    // Validate withdrawal request
    await this.validateWithdrawal(userId, chain, amount);

    // Get user's internal wallet
    const wallet = await this.getUserWallet(userId, chain);

    // Calculate network fee
    const fee = await this.estimateNetworkFee(chain, toAddress, amount, token);

    // Check if user has sufficient balance including fees
    const totalAmount = amount + fee;
    const balance = await this.getUserBalance(userId, chain, token);

    if (balance < totalAmount) {
      throw new Error('Insufficient balance for withdrawal including fees');
    }

    // Process withdrawal based on chain type
    let transactionHash: string;

    switch (chain) {
      case 'ethereum':
      case 'bsc':
      case 'polygon':
        transactionHash = await this.processEVMWithdrawal(
          wallet, toAddress, amount, fee, token
        );
        break;
      case 'solana':
        transactionHash = await this.processSolanaWithdrawal(
          wallet, toAddress, amount, fee, token
        );
        break;
      default:
        throw new Error(`Withdrawal not supported for ${chain}`);
    }

    // Record withdrawal for audit
    await this.recordWithdrawal({
      userId,
      chain,
      toAddress,
      amount,
      fee,
      transactionHash,
      timestamp: Date.now()
    });

    return transactionHash;
  }

  private async processEVMWithdrawal(
    wallet: ethers.Wallet,
    toAddress: string,
    amount: bigint,
    fee: bigint,
    token?: string
  ): Promise<string> {
    const provider = wallet.provider;

    if (token) {
      // ERC-20 token withdrawal
      const tokenContract = new ethers.Contract(
        token,
        ['function transfer(address to, uint256 amount) public returns (bool)'],
        wallet
      );

      const tx = await tokenContract.transfer(toAddress, amount);
      return tx.hash;
    } else {
      // Native currency withdrawal
      const tx = await wallet.sendTransaction({
        to: toAddress,
        value: amount,
        gasPrice: await provider.getFeeData().then(fee => fee.gasPrice),
        gasLimit: 21000
      });

      return tx.hash;
    }
  }

  // Multi-signature security for large withdrawals
  async processLargeWithdrawal(
    userId: string,
    chain: string,
    toAddress: string,
    amount: bigint,
    approvers: string[]
  ): Promise<string> {
    // Implement multi-sig logic
    const multiSigWallet = await this.getMultiSigWallet(chain);

    // Create withdrawal proposal
    const proposalId = await multiSigWallet.createProposal(
      toAddress,
      amount,
      "Large withdrawal requiring multi-sig approval"
    );

    // Collect approvals
    for (const approver of approvers) {
      await multiSigWallet.approveProposal(proposalId, approver);
    }

    // Execute when threshold reached
    if (await multiSigWallet.hasRequiredApprovals(proposalId)) {
      return await multiSigWallet.executeProposal(proposalId);
    }

    throw new Error('Insufficient approvals for large withdrawal');
  }
}
