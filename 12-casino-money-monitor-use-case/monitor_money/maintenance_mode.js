// Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// maintenance_mode.js -- Maintenance Mode Service
// Manages the operational state where new bet acceptance is suspended.
// Supports manual activation, automatic activation (triggered by the alert system),
// scheduled maintenance windows, and emergency activation.
// Production pattern: AcmetoCasino real-time financial monitoring.

const database = require('./database');
const redis = require('./redis');

class MaintenanceModeService {
    constructor() {
        this.isInitialized = false;
        this.currentStatus = null;
        this.io = null;
    }

    setIo(io) {
        this.io = io;
    }

    async initialize() {
        try {
            logger.info('Initializing Maintenance Mode Service');
            await this.loadCurrentStatus();
            this.isInitialized = true;
            logger.info('Maintenance Mode Service initialized successfully');
        } catch (error) {
            logger.error('Failed to initialize Maintenance Mode Service:', error);
            throw error;
        }
    }

    async loadCurrentStatus() {
        const status = await database.getMaintenanceModeStatus();
        this.currentStatus = status;
        await redis.setMaintenanceMode(status);
    }

    async activateMaintenanceMode(activatedBy = 'System', reason = 'Manual activation') {
        try {
            if (this.currentStatus && this.currentStatus.status === 'active') {
                logger.warn('Maintenance mode already active');
                return this.currentStatus;
            }

            const maintenanceRecord = await database.setMaintenanceMode('active', activatedBy, reason);

            this.currentStatus = maintenanceRecord;
            await redis.setMaintenanceMode(maintenanceRecord);
            this.emitMaintenanceUpdate(maintenanceRecord);

            logger.warn(`Maintenance mode activated by ${activatedBy}: ${reason}`);
            return maintenanceRecord;
        } catch (error) {
            logger.error('Failed to activate maintenance mode:', error);
            throw error;
        }
    }

    async deactivateMaintenanceMode(deactivatedBy = 'System') {
        try {
            if (!this.currentStatus || this.currentStatus.status !== 'active') {
                logger.warn('Maintenance mode not currently active');
                return this.currentStatus;
            }

            const updatedRecord = await database.updateMaintenanceMode(
                this.currentStatus.id,
                'inactive',
                deactivatedBy
            );

            this.currentStatus = updatedRecord;
            await redis.setMaintenanceMode(updatedRecord);
            this.emitMaintenanceUpdate(updatedRecord);

            logger.info(`Maintenance mode deactivated by ${deactivatedBy}`);
            return updatedRecord;
        } catch (error) {
            logger.error('Failed to deactivate maintenance mode:', error);
            throw error;
        }
    }

    async scheduleMaintenanceMode(scheduledBy, reason, durationHours = 1) {
        try {
            const maintenanceRecord = await database.setMaintenanceMode(
                'scheduled',
                scheduledBy,
                reason
            );

            await database.query(
                'UPDATE maintenance_mode SET estimated_duration = $1 WHERE id = $2',
                [`${durationHours} hours`, maintenanceRecord.id]
            );

            maintenanceRecord.estimated_duration = `${durationHours} hours`;
            await redis.setMaintenanceMode(maintenanceRecord, durationHours * 3600);
            this.emitMaintenanceUpdate(maintenanceRecord);

            logger.info(`Maintenance mode scheduled by ${scheduledBy} for ${durationHours} hours: ${reason}`);
            return maintenanceRecord;
        } catch (error) {
            logger.error('Failed to schedule maintenance mode:', error);
            throw error;
        }
    }

    async getMaintenanceStatus() {
        // Cache-first, database fallback
        let status = await redis.getMaintenanceMode();

        if (!status) {
            status = await database.getMaintenanceModeStatus();
            if (status) {
                await redis.setMaintenanceMode(status);
            }
        }

        return status;
    }

    async getMaintenanceHistory(hours = 24) {
        const query = `
            SELECT * FROM maintenance_mode
            WHERE created_at >= NOW() - INTERVAL '${hours} hours'
            ORDER BY created_at DESC
        `;
        const result = await database.query(query);
        return result.rows;
    }

    isMaintenanceActive() {
        if (!this.currentStatus) return false;

        if (this.currentStatus.status === 'scheduled') {
            return false;
        }

        return this.currentStatus.status === 'active';
    }

    // Returns duration in minutes since maintenance was activated
    getMaintenanceDuration() {
        if (!this.currentStatus || this.currentStatus.status !== 'active') {
            return null;
        }

        const activatedAt = new Date(this.currentStatus.activated_at);
        const now = new Date();
        return Math.floor((now - activatedAt) / (1000 * 60));
    }

    // Emergency maintenance activation -- bypasses normal checks.
    // Used when exposure exceeds shutdown threshold (30%+).
    async emergencyActivate(reason = 'Emergency activation') {
        try {
            logger.error(`EMERGENCY MAINTENANCE ACTIVATION: ${reason}`);

            const maintenanceRecord = await database.setMaintenanceMode('active', 'Emergency', reason);

            this.currentStatus = maintenanceRecord;
            await redis.setMaintenanceMode(maintenanceRecord);
            this.emitMaintenanceUpdate(maintenanceRecord);
            this.emitEmergencyAlert(reason);

            return maintenanceRecord;
        } catch (error) {
            logger.error('Failed emergency maintenance activation:', error);
            throw error;
        }
    }

    emitMaintenanceUpdate(status) {
        if (this.io) {
            this.io.to('maintenance').emit('maintenance_update', {
                type: 'maintenance_update',
                data: status,
                timestamp: new Date().toISOString()
            });
        }
    }

    emitEmergencyAlert(reason) {
        if (this.io) {
            this.io.emit('emergency_alert', {
                type: 'emergency_maintenance',
                reason: reason,
                timestamp: new Date().toISOString()
            });
        }
    }

    // API for external systems (game servers, payment gateways) to check maintenance status
    async getStatusForAPI() {
        const status = await this.getMaintenanceStatus();

        return {
            maintenanceActive: this.isMaintenanceActive(),
            status: status ? status.status : 'inactive',
            activatedAt: status ? status.activated_at : null,
            activatedBy: status ? status.activated_by : null,
            reason: status ? status.reason : null,
            duration: this.getMaintenanceDuration(),
            estimatedDuration: status ? status.estimated_duration : null
        };
    }

    async forceStatusUpdate(newStatus, updatedBy = 'Admin') {
        try {
            if (!this.currentStatus) {
                throw new Error('No current maintenance record found');
            }

            const updatedRecord = await database.updateMaintenanceMode(
                this.currentStatus.id,
                newStatus,
                updatedBy
            );

            this.currentStatus = updatedRecord;
            await redis.setMaintenanceMode(updatedRecord);
            this.emitMaintenanceUpdate(updatedRecord);

            logger.info(`Maintenance status force-updated to ${newStatus} by ${updatedBy}`);
            return updatedRecord;
        } catch (error) {
            logger.error('Failed to force status update:', error);
            throw error;
        }
    }

    async getMaintenanceStatistics(hours = 24) {
        const query = `
            SELECT
                COUNT(*) as total_activations,
                AVG(EXTRACT(EPOCH FROM (deactivated_at - activated_at))/60) as avg_duration_minutes,
                MAX(EXTRACT(EPOCH FROM (deactivated_at - activated_at))/60) as max_duration_minutes,
                MIN(EXTRACT(EPOCH FROM (deactivated_at - activated_at))/60) as min_duration_minutes
            FROM maintenance_mode
            WHERE status = 'inactive'
            AND created_at >= NOW() - INTERVAL '${hours} hours'
        `;
        const result = await database.query(query);
        return result.rows[0];
    }

    getStatus() {
        return {
            isInitialized: this.isInitialized,
            currentStatus: this.currentStatus,
            isActive: this.isMaintenanceActive(),
            duration: this.getMaintenanceDuration()
        };
    }

    async cleanup() {
        logger.info('Maintenance Mode Service cleaned up');
    }
}

module.exports = new MaintenanceModeService();
