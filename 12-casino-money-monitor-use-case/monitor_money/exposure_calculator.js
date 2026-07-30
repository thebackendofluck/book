// Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// exposure_calculator.js -- Exposure Calculator Service
// Calculates the ratio of potential payouts to available reserves on a cron cycle.
// Production pattern: AcmetoCasino real-time financial monitoring.

const cron = require('cron');
const database = require('./database');
const redis = require('./redis');
const alertSystem = require('./alert_system');

class ExposureCalculatorService {
    constructor() {
        this.isInitialized = false;
        this.calculationJob = null;
        this.calculationInterval = parseInt(process.env.CALCULATION_INTERVAL) || 60; // seconds
        this.reserveBufferPercentage = parseFloat(process.env.RESERVE_BUFFER_PERCENTAGE) || 5.00;
        this.lastCalculation = null;
        this.io = null;
    }

    setIo(io) {
        this.io = io;
    }

    async initialize() {
        try {
            logger.info('Initializing Exposure Calculator Service');
            this.startCalculation();
            this.isInitialized = true;
            logger.info('Exposure Calculator Service initialized successfully');
        } catch (error) {
            logger.error('Failed to initialize Exposure Calculator Service:', error);
            throw error;
        }
    }

    startCalculation() {
        // Run immediately, then every calculation interval
        this.calculateExposure();

        // Schedule cron job (every N seconds)
        const cronExpression = `*/${this.calculationInterval} * * * * *`;
        this.calculationJob = new cron.CronJob(cronExpression, () => {
            this.calculateExposure();
        });

        this.calculationJob.start();
        logger.info(`Exposure calculation started - calculating every ${this.calculationInterval} seconds`);
    }

    async calculateExposure() {
        try {
            // Get current data from database
            const exposureData = await database.calculateCurrentExposure();

            // Calculate risk level
            const riskLevel = this.determineRiskLevel(exposureData.exposure_percentage);

            // Create calculation record
            const calculation = {
                totalActiveBets: parseFloat(exposureData.total_active_bets) || 0,
                totalPotentialPayouts: parseFloat(exposureData.total_potential_payouts) || 0,
                totalCashReserves: parseFloat(exposureData.total_cash_reserves) || 0,
                bankBalance: parseFloat(exposureData.bank_balance) || 0,
                exposurePercentage: parseFloat(exposureData.exposure_percentage) || 0,
                riskLevel: riskLevel
            };

            // Store calculation in database
            const savedCalculation = await database.insertExposureCalculation(calculation);

            // Cache in Redis for fast dashboard reads
            await redis.cacheExposureCalculation(calculation);

            // Check for alerts -- only the highest applicable tier fires
            await this.checkAlertThresholds(calculation, savedCalculation.id);

            this.lastCalculation = calculation;

            // Emit real-time update via WebSocket
            this.emitExposureUpdate(calculation);

            logger.info(`Exposure calculated: ${calculation.exposurePercentage.toFixed(2)}% (${riskLevel})`);

        } catch (error) {
            logger.error('Error calculating exposure:', error);
        }
    }

    // Risk classification -- thresholds map to the tiered alert system.
    // These are configurable via the database but the defaults match
    // the operational runbook in Chapter 5.
    determineRiskLevel(exposurePercentage) {
        if (exposurePercentage >= 30) return 'critical';
        if (exposurePercentage >= 20) return 'high';
        if (exposurePercentage >= 15) return 'medium';
        if (exposurePercentage >= 10) return 'low';
        return 'safe';
    }

    async checkAlertThresholds(calculation, calculationId) {
        const thresholds = await this.getAlertThresholds();
        const currentExposure = calculation.exposurePercentage;

        // Check thresholds in descending severity order so the highest applicable alert fires
        const orderedLevels = ['shutdown', 'emergency', 'critical', 'management', 'warning'];
        for (const level of orderedLevels) {
            const threshold = thresholds[level];
            if (threshold !== undefined && currentExposure >= threshold) {
                await alertSystem.triggerAlert(level, currentExposure, threshold, calculationId);
                break; // Only trigger the highest applicable alert
            }
        }
    }

    async getAlertThresholds() {
        const config = await database.getSystemConfig();
        return {
            warning:    parseFloat(config.alert_threshold_10) || 10.00,
            management: parseFloat(config.alert_threshold_15) || 15.00,
            critical:   parseFloat(config.alert_threshold_20) || 20.00,
            emergency:  parseFloat(config.alert_threshold_25) || 25.00,
            shutdown:   parseFloat(config.alert_threshold_30) || 30.00
        };
    }

    emitExposureUpdate(calculation) {
        if (this.io) {
            this.io.to('exposure').emit('exposure_update', {
                type: 'exposure_update',
                data: calculation,
                timestamp: new Date().toISOString()
            });
        }
    }

    // Manual calculation trigger -- used by operations staff via the dashboard
    async triggerManualCalculation() {
        logger.info('Manual exposure calculation triggered');
        await this.calculateExposure();
    }

    // Get current exposure status (cache-first, database fallback)
    async getCurrentExposure() {
        let exposure = await redis.getCachedExposureCalculation();

        if (!exposure) {
            exposure = await database.getLatestExposureCalculation();
            if (exposure) {
                await redis.cacheExposureCalculation(exposure);
            }
        }

        return exposure;
    }

    // Get exposure history for trending charts
    async getExposureHistory(hours = 24) {
        const query = `
            SELECT * FROM exposure_calculations
            WHERE calculation_time >= NOW() - ($1 * INTERVAL '1 hour')
            ORDER BY calculation_time DESC
        `;
        const result = await database.query(query, [hours]);
        return result.rows;
    }

    // Calculate theoretical exposure for "what-if" scenarios.
    // Operations staff use this to model the impact of large promotional events
    // or high-roller sessions before they happen.
    async calculateTheoreticalExposure(scenario) {
        const currentData = await database.calculateCurrentExposure();

        const theoretical = {
            totalActiveBets: (parseFloat(currentData.total_active_bets) || 0) + (scenario.additionalBets || 0),
            totalPotentialPayouts: (parseFloat(currentData.total_potential_payouts) || 0) + (scenario.additionalPayouts || 0),
            totalCashReserves: (parseFloat(currentData.total_cash_reserves) || 0) + (scenario.cashReserveChange || 0),
            bankBalance: (parseFloat(currentData.bank_balance) || 0) + (scenario.bankBalanceChange || 0)
        };

        const totalReserves = theoretical.totalCashReserves + theoretical.bankBalance;
        theoretical.exposurePercentage = totalReserves > 0 ?
            (theoretical.totalPotentialPayouts / totalReserves) * 100 : 100.00;

        theoretical.riskLevel = this.determineRiskLevel(theoretical.exposurePercentage);

        return theoretical;
    }

    // Exposure statistics for reporting dashboards
    async getExposureStatistics(hours = 24) {
        const query = `
            SELECT
                COUNT(*) as total_calculations,
                AVG(exposure_percentage) as avg_exposure,
                MAX(exposure_percentage) as max_exposure,
                MIN(exposure_percentage) as min_exposure,
                AVG(total_active_bets) as avg_active_bets,
                MAX(total_active_bets) as max_active_bets,
                AVG(total_potential_payouts) as avg_potential_payouts,
                MAX(total_potential_payouts) as max_potential_payouts
            FROM exposure_calculations
            WHERE calculation_time >= NOW() - ($1 * INTERVAL '1 hour')
        `;
        const result = await database.query(query, [hours]);
        return result.rows[0];
    }

    // Risk distribution -- how much time the system spent at each risk level
    async getRiskDistribution(hours = 24) {
        const query = `
            SELECT
                risk_level,
                COUNT(*) as count,
                AVG(exposure_percentage) as avg_exposure
            FROM exposure_calculations
            WHERE calculation_time >= NOW() - ($1 * INTERVAL '1 hour')
            GROUP BY risk_level
            ORDER BY count DESC
        `;
        const result = await database.query(query, [hours]);
        return result.rows;
    }

    getStatus() {
        return {
            isInitialized: this.isInitialized,
            isCalculating: this.calculationJob ? this.calculationJob.running : false,
            calculationInterval: this.calculationInterval,
            lastCalculation: this.lastCalculation,
            reserveBufferPercentage: this.reserveBufferPercentage
        };
    }

    stopCalculation() {
        if (this.calculationJob) {
            this.calculationJob.stop();
            logger.info('Exposure calculation stopped');
        }
    }

    async cleanup() {
        this.stopCalculation();
        logger.info('Exposure Calculator Service cleaned up');
    }
}

module.exports = new ExposureCalculatorService();
