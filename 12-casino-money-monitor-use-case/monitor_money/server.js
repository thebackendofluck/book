// Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// server.js -- AcmetoCasino Money Monitor Application Server
// Express + WebSocket server that orchestrates all monitoring services.
// Production pattern: real-time financial monitoring for online casinos.

require('dotenv').config();
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const rateLimit = require('express-rate-limit');
const { createServer } = require('http');
const { Server } = require('socket.io');
const winston = require('winston');
const path = require('path');

// Import services
const database = require('./database');
const redis = require('./redis');
const bankMonitor = require('./bank_monitor');
const exposureCalculator = require('./exposure_calculator');
const alertSystem = require('./alert_system');
const maintenanceMode = require('./maintenance_mode');

// Import routes
const apiRoutes = require('./routes/api');
const healthRoutes = require('./routes/health');
const dashboardRoutes = require('./routes/dashboard');

// Create logger -- structured JSON in production, simple text in development
const logger = winston.createLogger({
    level: process.env.LOG_LEVEL || 'info',
    format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.errors({ stack: true }),
        winston.format.json()
    ),
    defaultMeta: { service: 'acme-money-monitor' },
    transports: [
        new winston.transports.File({
            filename: path.join(__dirname, '../logs/error.log'),
            level: 'error'
        }),
        new winston.transports.File({
            filename: path.join(__dirname, '../logs/combined.log')
        })
    ]
});

if (process.env.NODE_ENV !== 'production') {
    logger.add(new winston.transports.Console({
        format: winston.format.simple()
    }));
}

// Initialize Express app
const app = express();
const server = createServer(app);
const io = new Server(server, {
    cors: {
        origin: process.env.CORS_ORIGIN || 'http://localhost:3000',
        methods: ['GET', 'POST']
    }
});

// Global error handlers -- crash fast on unhandled errors.
// In production, a process manager (PM2, systemd) restarts the process.
process.on('uncaughtException', (error) => {
    logger.error('Uncaught Exception:', error);
    process.exit(1);
});

process.on('unhandledRejection', (reason, promise) => {
    logger.error('Unhandled Rejection at:', promise, 'reason:', reason);
    process.exit(1);
});

// Middleware
app.use(helmet({
    contentSecurityPolicy: {
        directives: {
            defaultSrc: ["'self'"],
            styleSrc: ["'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net'],
            scriptSrc: ["'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net'],
            imgSrc: ["'self'", 'data:', 'https:'],
        },
    },
}));

app.use(cors({
    origin: process.env.CORS_ORIGIN || 'http://localhost:3000',
    credentials: true
}));

// Rate limiting -- 100 requests per 15-minute window per IP
const limiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 100,
    message: 'Too many requests from this IP, please try again later.',
    standardHeaders: true,
    legacyHeaders: false,
});
app.use('/api/', limiter);

// Body parsing
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true, limit: '10mb' }));

// Static files
app.use('/static', express.static(path.join(__dirname, '../static')));
app.use(express.static(path.join(__dirname, '../public')));

// Attach logger to requests
app.use((req, res, next) => {
    req.logger = logger;
    next();
});

// Routes
app.use('/api', apiRoutes);
app.use('/health', healthRoutes);
app.use('/dashboard', dashboardRoutes);

// WebSocket connection handling -- clients join rooms for specific data streams
io.on('connection', (socket) => {
    logger.info(`Client connected: ${socket.id}`);

    socket.on('join', (room) => {
        socket.join(room);
        logger.info(`Client ${socket.id} joined room: ${room}`);
    });

    socket.on('leave', (room) => {
        socket.leave(room);
        logger.info(`Client ${socket.id} left room: ${room}`);
    });

    socket.on('disconnect', () => {
        logger.info(`Client disconnected: ${socket.id}`);
    });
});

// Pass io to services that need real-time broadcasting
bankMonitor.setIo(io);
exposureCalculator.setIo(io);
alertSystem.setIo(io);
maintenanceMode.setIo(io);

// Initialize services in dependency order
async function initializeServices() {
    try {
        await database.connect();
        logger.info('Database connected successfully');

        await redis.connect();
        logger.info('Redis connected successfully');

        await bankMonitor.initialize();
        await exposureCalculator.initialize();
        await alertSystem.initialize();
        await maintenanceMode.initialize();

        logger.info('All services initialized successfully');

    } catch (error) {
        logger.error('Failed to initialize services:', error);
        process.exit(1);
    }
}

// Graceful shutdown -- drain connections before exiting
process.on('SIGTERM', async () => {
    logger.info('SIGTERM received, shutting down gracefully');

    try {
        await database.disconnect();
        await redis.disconnect();
        server.close(() => {
            logger.info('Server closed');
            process.exit(0);
        });
    } catch (error) {
        logger.error('Error during shutdown:', error);
        process.exit(1);
    }
});

process.on('SIGINT', async () => {
    logger.info('SIGINT received, shutting down gracefully');

    try {
        await database.disconnect();
        await redis.disconnect();
        server.close(() => {
            logger.info('Server closed');
            process.exit(0);
        });
    } catch (error) {
        logger.error('Error during shutdown:', error);
        process.exit(1);
    }
});

// Start server
const PORT = process.env.PORT || 3000;

server.listen(PORT, async () => {
    logger.info(`AcmetoCasino Money Monitor server running on port ${PORT}`);
    logger.info(`Dashboard available at http://localhost:${PORT}/dashboard`);

    await initializeServices();
});

module.exports = { app, server, io, logger };
