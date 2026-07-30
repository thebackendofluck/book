// Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// bank_monitor.js -- Bank Account Monitoring Service
// Polls bank APIs for balance and transaction updates, deduplicates transactions,
// and broadcasts changes over WebSocket.
// Production pattern: AcmetoCasino real-time financial monitoring.

const axios = require('axios');
const cron = require('cron');
const database = require('./database');
const redis = require('./redis');
const bankIntegrations = require('./bank_integrations');

class BankMonitorService {
    constructor() {
        this.isInitialized = false;
        this.monitorJob = null;
        this.bankApiUrl = process.env.BANK_API_URL || 'http://localhost:3001';
        this.bankApiKey = process.env.BANK_API_KEY;
        this.accountNumber = process.env.BANK_ACCOUNT_NUMBER || 'ACME000001';
        this.monitoringInterval = parseInt(process.env.CALCULATION_INTERVAL) || 60; // seconds
        this.bankAdapters = new Map();
        this.io = null;
    }

    setIo(io) {
        this.io = io;
    }

    async initialize() {
        try {
            logger.info('Initializing Bank Monitor Service');

            await bankIntegrations.initialize();
            await this.testBankConnection();
            await this.setupBankAdapters();
            this.startMonitoring();

            this.isInitialized = true;
            logger.info('Bank Monitor Service initialized successfully');
        } catch (error) {
            logger.error('Failed to initialize Bank Monitor Service:', error);
            throw error;
        }
    }

    async setupBankAdapters() {
        // Setup adapters for European banks.
        // Each bank has a different auth scheme (OAuth2, certificate, PSD2).
        const banks = bankIntegrations.getSupportedBanks();

        for (const bank of banks) {
            try {
                const adapter = bankIntegrations.createAdapter(bank.name.toLowerCase(), {
                    credentials: this.getCredentials(bank.name)
                });

                this.bankAdapters.set(bank.name.toLowerCase(), adapter);
                logger.info(`Bank adapter initialized for ${bank.name}`);
            } catch (error) {
                logger.warn(`Failed to initialize adapter for ${bank.name}:`, error.message);
            }
        }
    }

    getCredentials(bankName) {
        // In production, credentials come from a secrets manager (Vault, AWS Secrets Manager).
        // These placeholders demonstrate the structure.
        return {
            clientId: process.env[`BANK_${bankName.toUpperCase()}_CLIENT_ID`] || 'placeholder',
            clientSecret: process.env[`BANK_${bankName.toUpperCase()}_CLIENT_SECRET`] || 'placeholder',
            certificate: process.env[`BANK_${bankName.toUpperCase()}_CERT_PATH`] || '/vault/certs/cert.pem',
            key: process.env[`BANK_${bankName.toUpperCase()}_KEY_PATH`] || '/vault/certs/key.pem',
            webhookSecret: process.env[`BANK_${bankName.toUpperCase()}_WEBHOOK_SECRET`] || 'placeholder'
        };
    }

    async testBankConnection() {
        try {
            const response = await axios.get(`${this.bankApiUrl}/health`, {
                timeout: 5000,
                headers: this.getAuthHeaders()
            });

            if (response.status !== 200) {
                throw new Error(`Bank API health check failed with status ${response.status}`);
            }

            logger.info('Bank API connection test successful');
        } catch (error) {
            logger.error('Bank API connection test failed:', error.message);
            throw error;
        }
    }

    getAuthHeaders() {
        const headers = { 'Content-Type': 'application/json' };
        if (this.bankApiKey) {
            headers['Authorization'] = `Bearer ${this.bankApiKey}`;
        }
        return headers;
    }

    startMonitoring() {
        // Run immediately, then every monitoring interval
        this.monitorBankAccount();

        const cronExpression = `*/${this.monitoringInterval} * * * * *`;
        this.monitorJob = new cron.CronJob(cronExpression, () => {
            this.monitorBankAccount();
        });

        this.monitorJob.start();
        logger.info(`Bank monitoring started - checking every ${this.monitoringInterval} seconds`);
    }

    async monitorBankAccount() {
        try {
            const accountData = await this.getAccountBalance();
            const transactions = await this.getRecentTransactions();

            await this.processTransactions(transactions);
            await this.updateBankBalance(accountData);

            // Cache balance in Redis for fast exposure calculations
            await redis.cacheBankBalance(this.accountNumber, accountData);

            logger.info(`Bank monitoring completed - Balance: EUR ${accountData.balance}, Available: EUR ${accountData.availableBalance}`);

        } catch (error) {
            logger.error('Error during bank monitoring:', error);
            // Continue monitoring despite errors -- resilience over correctness
        }
    }

    async getAccountBalance() {
        try {
            const response = await axios.get(
                `${this.bankApiUrl}/api/v1/accounts/${this.accountNumber}`,
                { timeout: 10000, headers: this.getAuthHeaders() }
            );

            return {
                balance: parseFloat(response.data.balance),
                availableBalance: parseFloat(response.data.availableBalance),
                currency: response.data.currency || 'EUR',
                lastUpdated: new Date().toISOString()
            };
        } catch (error) {
            logger.error('Failed to get account balance:', error.message);
            throw error;
        }
    }

    async getRecentTransactions(limit = 100) {
        try {
            const response = await axios.get(
                `${this.bankApiUrl}/api/v1/accounts/${this.accountNumber}/transactions`,
                { params: { limit }, timeout: 10000, headers: this.getAuthHeaders() }
            );
            return response.data.transactions || [];
        } catch (error) {
            logger.error('Failed to get recent transactions:', error.message);
            return []; // Return empty array to continue processing
        }
    }

    // Deduplicate transactions by external ID before inserting.
    // Bank APIs often return the same transaction across multiple polling cycles.
    async processTransactions(transactions) {
        if (!transactions || transactions.length === 0) return;

        try {
            const accounts = await database.getBankAccounts();
            const account = accounts.find(acc => acc.account_number === this.accountNumber);

            if (!account) {
                logger.error(`Bank account ${this.accountNumber} not found in database`);
                return;
            }

            let processedCount = 0;

            for (const transaction of transactions) {
                try {
                    // Check if transaction already exists (idempotency check)
                    const existingTransaction = await database.query(
                        'SELECT id FROM transactions WHERE transaction_id = $1',
                        [transaction.id]
                    );

                    if (existingTransaction.rows.length === 0) {
                        const dbTransaction = {
                            bankAccountId: account.id,
                            transactionId: transaction.id,
                            amount: parseFloat(transaction.amount),
                            type: transaction.type,
                            category: transaction.category,
                            description: transaction.description,
                            transactionDate: new Date(transaction.timestamp)
                        };

                        await database.insertTransaction(dbTransaction);
                        processedCount++;

                        this.emitTransactionUpdate(dbTransaction);
                    }
                } catch (error) {
                    logger.error(`Error processing transaction ${transaction.id}:`, error);
                }
            }

            if (processedCount > 0) {
                logger.info(`Processed ${processedCount} new transactions`);
            }

        } catch (error) {
            logger.error('Error processing transactions:', error);
        }
    }

    async updateBankBalance(accountData) {
        try {
            const accounts = await database.getBankAccounts();
            const account = accounts.find(acc => acc.account_number === this.accountNumber);

            if (account) {
                await database.updateBankBalance(account.id, accountData.balance, accountData.availableBalance);
                this.emitBalanceUpdate(accountData);
            }
        } catch (error) {
            logger.error('Error updating bank balance:', error);
        }
    }

    emitTransactionUpdate(transaction) {
        if (this.io) {
            this.io.to('transactions').emit('transaction_update', {
                type: 'new_transaction',
                data: transaction,
                timestamp: new Date().toISOString()
            });
        }
    }

    emitBalanceUpdate(balanceData) {
        if (this.io) {
            this.io.to('bank_balance').emit('balance_update', {
                type: 'balance_update',
                data: balanceData,
                timestamp: new Date().toISOString()
            });
        }
    }

    async triggerManualCheck() {
        logger.info('Manual bank check triggered');
        await this.monitorBankAccount();
    }

    getStatus() {
        return {
            isInitialized: this.isInitialized,
            isMonitoring: this.monitorJob ? this.monitorJob.running : false,
            monitoringInterval: this.monitoringInterval,
            lastCheck: this.lastCheck || null,
            bankApiUrl: this.bankApiUrl,
            accountNumber: this.accountNumber,
            supportedBanks: Array.from(this.bankAdapters.keys())
        };
    }

    async testBankAdapter(bankName) {
        const adapter = this.bankAdapters.get(bankName.toLowerCase());
        if (!adapter) {
            throw new Error(`Bank adapter for ${bankName} not found`);
        }
        return await adapter.testConnection();
    }

    getBankAdapter(bankName) {
        return this.bankAdapters.get(bankName.toLowerCase());
    }

    getSupportedBanks() {
        return bankIntegrations.getSupportedBanks();
    }

    getBanksByRegion(region) {
        return bankIntegrations.getBanksByRegion(region);
    }

    stopMonitoring() {
        if (this.monitorJob) {
            this.monitorJob.stop();
            logger.info('Bank monitoring stopped');
        }
    }

    async cleanup() {
        this.stopMonitoring();
        logger.info('Bank Monitor Service cleaned up');
    }
}

module.exports = new BankMonitorService();
