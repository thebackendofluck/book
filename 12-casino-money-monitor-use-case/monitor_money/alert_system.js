// Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// alert_system.js -- Tiered Alert System Service
// Maps exposure levels to escalating operational responses.
// Each tier builds on the previous one: a "critical" alert implies all lower-tier actions are in effect.
// Production pattern: AcmetoCasino real-time financial monitoring.

const nodemailer = require('nodemailer');
const database = require('./database');
const redis = require('./redis');
const maintenanceMode = require('./maintenance_mode');

class AlertSystemService {
    constructor() {
        this.isInitialized = false;
        this.transporter = null;
        this.alertThresholds = {};
        this.maintenanceModeAuto = process.env.MAINTENANCE_MODE_AUTO === 'true';
        this.io = null;
    }

    setIo(io) {
        this.io = io;
    }

    async initialize() {
        try {
            logger.info('Initializing Alert System Service');
            await this.initializeEmailTransporter();
            await this.loadAlertThresholds();
            this.isInitialized = true;
            logger.info('Alert System Service initialized successfully');
        } catch (error) {
            logger.error('Failed to initialize Alert System Service:', error);
            throw error;
        }
    }

    async initializeEmailTransporter() {
        if (process.env.EMAIL_ALERTS_ENABLED !== 'true') {
            logger.info('Email alerts disabled');
            return;
        }

        this.transporter = nodemailer.createTransporter({
            host: process.env.SMTP_HOST,
            port: parseInt(process.env.SMTP_PORT) || 587,
            secure: process.env.SMTP_SECURE === 'true',
            auth: {
                user: process.env.SMTP_USER,
                pass: process.env.SMTP_PASS
            }
        });

        try {
            await this.transporter.verify();
            logger.info('Email transporter verified successfully');
        } catch (error) {
            logger.error('Email transporter verification failed:', error);
            this.transporter = null;
        }
    }

    async loadAlertThresholds() {
        const config = await database.getSystemConfig();
        this.alertThresholds = {
            warning:    parseFloat(config.alert_threshold_10) || 10.00,
            management: parseFloat(config.alert_threshold_15) || 15.00,
            critical:   parseFloat(config.alert_threshold_20) || 20.00,
            emergency:  parseFloat(config.alert_threshold_25) || 25.00,
            shutdown:   parseFloat(config.alert_threshold_30) || 30.00
        };
        logger.info('Alert thresholds loaded:', this.alertThresholds);
    }

    async triggerAlert(level, currentPercentage, threshold, calculationId) {
        try {
            // Debounce: skip if the same alert level already fired in the last 5 minutes
            const existingAlerts = await database.query(
                `SELECT id FROM alerts
                 WHERE exposure_calculation_id = $1
                   AND alert_level = $2
                   AND created_at >= NOW() - INTERVAL '5 minutes'`,
                [calculationId, level]
            );

            if (existingAlerts.rows.length > 0) return;

            const alert = {
                exposureCalculationId: calculationId,
                alertLevel: level,
                message: this.generateAlertMessage(level, currentPercentage, threshold),
                thresholdPercentage: threshold,
                currentPercentage: currentPercentage,
                recipients: this.getAlertRecipients(level)
            };

            const savedAlert = await database.insertAlert(alert);
            await redis.storeAlert(savedAlert);
            await this.sendNotifications(savedAlert);

            // Auto-activate maintenance mode on critical threshold breach
            if (level === 'critical' && this.maintenanceModeAuto) {
                await this.triggerMaintenanceMode(savedAlert);
            }

            this.emitAlert(savedAlert);
            logger.warn(`Alert triggered: ${level} - ${currentPercentage.toFixed(2)}% exposure`);

        } catch (error) {
            logger.error('Error triggering alert:', error);
        }
    }

    generateAlertMessage(level, currentPercentage, threshold) {
        const messages = {
            warning:    `WARNING: Exposure has reached ${currentPercentage.toFixed(2)}% (threshold: ${threshold}%). Please review cash reserves.`,
            management: `MANAGEMENT ALERT: Exposure at ${currentPercentage.toFixed(2)}% (threshold: ${threshold}%). Owner notification required.`,
            critical:   `CRITICAL ALERT: Exposure at ${currentPercentage.toFixed(2)}% (threshold: ${threshold}%). Maintenance mode activated.`,
            emergency:  `EMERGENCY ALERT: Exposure at ${currentPercentage.toFixed(2)}% (threshold: ${threshold}%). Immediate cash infusion required.`,
            shutdown:   `SHUTDOWN ALERT: Exposure at ${currentPercentage.toFixed(2)}% (threshold: ${threshold}%). Critical operational risk detected.`
        };
        return messages[level] || `ALERT: Exposure at ${currentPercentage.toFixed(2)}%`;
    }

    getAlertRecipients(level) {
        const recipients = [];

        if (process.env.ALERT_EMAIL) {
            recipients.push(process.env.ALERT_EMAIL);
        }

        switch (level) {
            case 'warning':
                if (process.env.STAFF_EMAILS) {
                    recipients.push(...process.env.STAFF_EMAILS.split(','));
                }
                break;
            case 'management':
            case 'critical':
            case 'emergency':
            case 'shutdown':
                if (process.env.OWNER_EMAIL) {
                    recipients.push(process.env.OWNER_EMAIL);
                }
                if (process.env.STAFF_EMAILS) {
                    recipients.push(...process.env.STAFF_EMAILS.split(','));
                }
                break;
        }

        return recipients.filter(email => email && email.trim());
    }

    async sendNotifications(alert) {
        if (this.transporter && alert.recipients.length > 0) {
            await this.sendEmailAlert(alert);
        }
        // Additional channels (SMS, Slack, PagerDuty) can be added here
    }

    async sendEmailAlert(alert) {
        try {
            const mailOptions = {
                from: process.env.SMTP_USER,
                to: alert.recipients.join(', '),
                subject: `AcmetoCasino Monitor Alert: ${alert.alert_level.toUpperCase()} - ${alert.current_percentage.toFixed(2)}% Exposure`,
                html: this.generateEmailHtml(alert),
                priority: this.getEmailPriority(alert.alert_level)
            };

            const info = await this.transporter.sendMail(mailOptions);
            logger.info(`Alert email sent: ${info.messageId}`);

            await database.query(
                'UPDATE alerts SET sent_at = CURRENT_TIMESTAMP WHERE id = $1',
                [alert.id]
            );

        } catch (error) {
            logger.error('Failed to send alert email:', error);
        }
    }

    generateEmailHtml(alert) {
        const colors = {
            warning:    '#ffa500',
            management: '#ff6b35',
            critical:   '#dc3545',
            emergency:  '#8b0000',
            shutdown:   '#000000'
        };

        return `
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background-color: ${colors[alert.alert_level] || '#666'}; color: white; padding: 20px; text-align: center;">
                    <h1>AcmetoCasino Monitor Alert</h1>
                    <h2 style="margin: 0;">${alert.alert_level.toUpperCase()}</h2>
                </div>
                <div style="padding: 20px; border: 1px solid #ddd;">
                    <p><strong>Exposure Level:</strong> ${alert.current_percentage.toFixed(2)}%</p>
                    <p><strong>Threshold:</strong> ${alert.threshold_percentage.toFixed(2)}%</p>
                    <p><strong>Time:</strong> ${new Date().toLocaleString()}</p>
                    <p><strong>Message:</strong></p>
                    <p style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid ${colors[alert.alert_level] || '#666'};">
                        ${alert.message}
                    </p>
                    <div style="margin-top: 20px; padding: 15px; background-color: #fff3cd; border: 1px solid #ffeaa7;">
                        <strong>Action Required:</strong> ${this.getActionRequired(alert.alert_level)}
                    </div>
                </div>
            </div>
        `;
    }

    getEmailPriority(level) {
        switch (level) {
            case 'shutdown':
            case 'emergency':
            case 'critical':
                return 'high';
            default:
                return 'normal';
        }
    }

    getActionRequired(level) {
        const actions = {
            warning:    'Review cash reserves and prepare contingency plans.',
            management: 'Assess financial position and consider cash transfers. Owner notification sent.',
            critical:   'Maintenance mode activated. Suspend new bets and initiate cash infusion.',
            emergency:  'Immediate cash infusion required. Activate emergency protocols.',
            shutdown:   'Critical operational risk. Immediate action required to prevent insolvency.'
        };
        return actions[level] || 'Review system status immediately.';
    }

    async triggerMaintenanceMode(alert) {
        try {
            await maintenanceMode.activateMaintenanceMode(
                'System',
                `Automatic activation due to ${alert.alert_level} alert: ${alert.current_percentage.toFixed(2)}% exposure`
            );
            logger.warn('Maintenance mode automatically activated due to critical alert');
        } catch (error) {
            logger.error('Failed to activate maintenance mode:', error);
        }
    }

    emitAlert(alert) {
        if (this.io) {
            this.io.to('alerts').emit('alert', {
                type: 'new_alert',
                data: alert,
                timestamp: new Date().toISOString()
            });
        }
    }

    async getUnacknowledgedAlerts() {
        return await database.getUnacknowledgedAlerts();
    }

    async acknowledgeAlert(alertId, acknowledgedBy = 'System') {
        try {
            const alert = await database.acknowledgeAlert(alertId);

            if (this.io) {
                this.io.to('alerts').emit('alert_acknowledged', {
                    alertId,
                    acknowledgedBy,
                    timestamp: new Date().toISOString()
                });
            }

            logger.info(`Alert ${alertId} acknowledged by ${acknowledgedBy}`);
            return alert;
        } catch (error) {
            logger.error(`Failed to acknowledge alert ${alertId}:`, error);
            throw error;
        }
    }

    async getAlertHistory(hours = 24) {
        const query = `
            SELECT * FROM alerts
            WHERE created_at >= NOW() - ($1 || ' hours')::interval
            ORDER BY created_at DESC
        `;
        const result = await database.query(query, [parseInt(hours)]);
        return result.rows;
    }

    async getAlertStatistics(hours = 24) {
        const query = `
            SELECT
                alert_level,
                COUNT(*) as count,
                AVG(current_percentage) as avg_exposure,
                MAX(current_percentage) as max_exposure
            FROM alerts
            WHERE created_at >= NOW() - ($1 || ' hours')::interval
            GROUP BY alert_level
            ORDER BY count DESC
        `;
        const result = await database.query(query, [parseInt(hours)]);
        return result.rows;
    }

    getStatus() {
        return {
            isInitialized: this.isInitialized,
            emailEnabled: !!this.transporter,
            thresholds: this.alertThresholds,
            maintenanceModeAuto: this.maintenanceModeAuto
        };
    }

    async cleanup() {
        if (this.transporter) {
            this.transporter.close();
        }
        logger.info('Alert System Service cleaned up');
    }
}

module.exports = new AlertSystemService();
