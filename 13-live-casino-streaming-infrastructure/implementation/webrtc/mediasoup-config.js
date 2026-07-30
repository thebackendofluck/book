// Companion code for "The Backend of Luck" - Chapter 13, Live Casino Streaming Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * mediasoup SFU Configuration for Live Casino Streaming
 * Chapter 6 - Live Casino Streaming Infrastructure
 *
 * Purpose: Production-grade mediasoup v3 configuration for a Selective Forwarding
 * Unit (SFU) that handles WebRTC distribution of live casino table streams.
 *
 * Architecture:
 *   Studio Camera -> RTMP Ingest -> mediasoup Router -> WebRTC Consumers (players)
 *
 * Features:
 *   - Multi-worker utilization (one per CPU core)
 *   - Simulcast support for adaptive quality
 *   - DTLS/SRTP encryption
 *   - ICE/TURN configuration for NAT traversal
 *   - Per-table Router isolation
 *   - Connection lifecycle management with metrics
 *
 * Prerequisites:
 *   npm install mediasoup@3 express socket.io prom-client winston
 *
 * Usage:
 *   node mediasoup-config.js
 *   NODE_ENV=production node mediasoup-config.js
 */

'use strict';

const os = require('os');
const mediasoup = require('mediasoup');
const express = require('express');
const http = require('http');
const { Server: SocketIO } = require('socket.io');
const { collectDefaultMetrics, Counter, Gauge, Histogram, register } = require('prom-client');
const winston = require('winston');

// =============================================================================
// Logging
// =============================================================================
const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.json()
  ),
  defaultMeta: { service: 'mediasoup-sfu' },
  transports: [
    new winston.transports.Console(),
    new winston.transports.File({ filename: '/var/log/live-casino/mediasoup.log', maxsize: 50_000_000 }),
  ],
});

// =============================================================================
// Prometheus Metrics
// =============================================================================
collectDefaultMetrics({ prefix: 'mediasoup_' });

const metricsActiveProducers = new Gauge({
  name: 'mediasoup_active_producers',
  help: 'Number of active media producers (studio cameras)',
  labelNames: ['table_id', 'camera'],
});
const metricsActiveConsumers = new Gauge({
  name: 'mediasoup_active_consumers',
  help: 'Number of active media consumers (player viewers)',
  labelNames: ['table_id'],
});
const metricsConnectionDuration = new Histogram({
  name: 'mediasoup_connection_duration_seconds',
  help: 'Duration of WebRTC consumer connections',
  labelNames: ['table_id'],
  buckets: [10, 30, 60, 300, 600, 1800, 3600],
});
const metricsErrors = new Counter({
  name: 'mediasoup_errors_total',
  help: 'Total mediasoup errors',
  labelNames: ['type'],
});

// =============================================================================
// mediasoup Configuration
// =============================================================================
const config = {
  // --- Server ---
  listenIp: process.env.LISTEN_IP || '0.0.0.0',
  listenPort: parseInt(process.env.LISTEN_PORT, 10) || 3000,
  metricsPort: parseInt(process.env.METRICS_PORT, 10) || 9100,

  // --- mediasoup Worker settings ---
  worker: {
    rtcMinPort: 40000,
    rtcMaxPort: 49999,
    logLevel: 'warn',
    logTags: ['info', 'ice', 'dtls', 'rtp', 'srtp', 'rtcp'],
    // One worker per CPU core for maximum throughput
    numWorkers: parseInt(process.env.NUM_WORKERS, 10) || os.cpus().length,
  },

  // --- Router settings (one per table) ---
  router: {
    mediaCodecs: [
      {
        kind: 'audio',
        mimeType: 'audio/opus',
        clockRate: 48000,
        channels: 2,
        parameters: {
          minptime: 10,
          useinbandfec: 1,
          usedtx: 1,
          stereo: 1,
          'sprop-stereo': 1,
        },
      },
      {
        kind: 'video',
        mimeType: 'video/H264',
        clockRate: 90000,
        parameters: {
          'packetization-mode': 1,
          'profile-level-id': '42e01f', // Baseline for compatibility
          'level-asymmetry-allowed': 1,
        },
      },
      {
        kind: 'video',
        mimeType: 'video/H264',
        clockRate: 90000,
        parameters: {
          'packetization-mode': 1,
          'profile-level-id': '640032', // High profile for quality
          'level-asymmetry-allowed': 1,
        },
      },
      {
        kind: 'video',
        mimeType: 'video/VP8',
        clockRate: 90000,
      },
    ],
  },

  // --- WebRTC Transport settings ---
  webRtcTransport: {
    listenIps: [
      {
        ip: process.env.MEDIASOUP_LISTEN_IP || '0.0.0.0',
        announcedIp: process.env.MEDIASOUP_ANNOUNCED_IP || null, // Set to public IP in production
      },
    ],
    initialAvailableOutgoingBitrate: 4_500_000, // 4.5 Mbps initial
    maxIncomingBitrate: 10_000_000, // 10 Mbps max from studios
    minimumAvailableOutgoingBitrate: 600_000, // 600 Kbps floor
    maxSctpMessageSize: 262_144,
    enableUdp: true,
    enableTcp: true, // TCP fallback for restrictive firewalls
    preferUdp: true,
    iceConsentTimeout: 25, // seconds
  },

  // --- Plain RTP Transport (for RTMP -> RTP bridge) ---
  plainRtpTransport: {
    listenIp: {
      ip: process.env.MEDIASOUP_LISTEN_IP || '0.0.0.0',
      announcedIp: null,
    },
    rtcpMux: true,
    comedia: true,
  },

  // --- TURN servers for NAT traversal ---
  iceServers: [
    {
      urls: [
        'turn:turn1.livecasino.com:3478?transport=udp',
        'turn:turn1.livecasino.com:3478?transport=tcp',
        'turns:turn1.livecasino.com:5349?transport=tcp',
      ],
      username: process.env.TURN_USERNAME || 'livecasino',
      credential: process.env.TURN_CREDENTIAL || 'changeme',
    },
  ],

  // --- Simulcast configuration ---
  simulcast: {
    enabled: true,
    encodings: [
      { rid: 'r0', maxBitrate: 500_000, scaleResolutionDownBy: 4.0 },   // 480p
      { rid: 'r1', maxBitrate: 1_500_000, scaleResolutionDownBy: 2.0 }, // 720p
      { rid: 'r2', maxBitrate: 4_500_000, scaleResolutionDownBy: 1.0 }, // 1080p
    ],
  },
};

// =============================================================================
// SFU Server Implementation
// =============================================================================
class LiveCasinoSFU {
  constructor() {
    this.workers = [];
    this.nextWorkerIdx = 0;
    this.routers = new Map();     // tableId -> Router
    this.producers = new Map();   // producerId -> { tableId, camera, transport, producer }
    this.consumers = new Map();   // consumerId -> { tableId, playerId, transport, consumer, startTime }
    this.transports = new Map();  // transportId -> Transport
  }

  /**
   * Initialize mediasoup workers (one per CPU core)
   */
  async init() {
    logger.info(`Creating ${config.worker.numWorkers} mediasoup workers`);

    for (let i = 0; i < config.worker.numWorkers; i++) {
      const worker = await mediasoup.createWorker({
        logLevel: config.worker.logLevel,
        logTags: config.worker.logTags,
        rtcMinPort: config.worker.rtcMinPort,
        rtcMaxPort: config.worker.rtcMaxPort,
      });

      worker.on('died', (error) => {
        logger.error(`mediasoup worker ${worker.pid} died`, { error: error.message });
        metricsErrors.inc({ type: 'worker_died' });
        // In production, trigger a process restart via systemd/k8s
        setTimeout(() => process.exit(1), 2000);
      });

      this.workers.push(worker);
      logger.info(`Worker ${i} created (PID: ${worker.pid})`);
    }
  }

  /**
   * Round-robin worker selection for load distribution
   */
  getNextWorker() {
    const worker = this.workers[this.nextWorkerIdx];
    this.nextWorkerIdx = (this.nextWorkerIdx + 1) % this.workers.length;
    return worker;
  }

  /**
   * Get or create a Router for a specific table
   * Each table gets its own Router for isolation
   */
  async getOrCreateRouter(tableId) {
    if (this.routers.has(tableId)) {
      return this.routers.get(tableId);
    }

    const worker = this.getNextWorker();
    const router = await worker.createRouter({
      mediaCodecs: config.router.mediaCodecs,
    });

    this.routers.set(tableId, router);
    logger.info(`Router created for table ${tableId} on worker ${worker.pid}`);

    router.on('close', () => {
      this.routers.delete(tableId);
      logger.info(`Router closed for table ${tableId}`);
    });

    return router;
  }

  /**
   * Create a WebRTC transport for a producer (studio camera) or consumer (player)
   */
  async createWebRtcTransport(tableId) {
    const router = await this.getOrCreateRouter(tableId);

    const transport = await router.createWebRtcTransport({
      ...config.webRtcTransport,
      appData: { tableId },
    });

    // Set maximum incoming bitrate for bandwidth management
    if (config.webRtcTransport.maxIncomingBitrate) {
      try {
        await transport.setMaxIncomingBitrate(config.webRtcTransport.maxIncomingBitrate);
      } catch (err) {
        logger.warn('Failed to set max incoming bitrate', { error: err.message });
      }
    }

    this.transports.set(transport.id, transport);

    transport.on('close', () => {
      this.transports.delete(transport.id);
    });

    transport.on('dtlsstatechange', (dtlsState) => {
      if (dtlsState === 'failed' || dtlsState === 'closed') {
        logger.warn(`Transport ${transport.id} DTLS state: ${dtlsState}`);
        transport.close();
      }
    });

    return {
      id: transport.id,
      iceParameters: transport.iceParameters,
      iceCandidates: transport.iceCandidates,
      dtlsParameters: transport.dtlsParameters,
      sctpParameters: transport.sctpParameters,
    };
  }

  /**
   * Create a Plain RTP transport for RTMP-to-RTP bridge
   * Used when studio streams arrive via RTMP and need conversion to RTP
   */
  async createPlainRtpTransport(tableId) {
    const router = await this.getOrCreateRouter(tableId);

    const transport = await router.createPlainTransport({
      ...config.plainRtpTransport,
      appData: { tableId },
    });

    this.transports.set(transport.id, transport);

    return {
      id: transport.id,
      ip: transport.tuple.localIp,
      port: transport.tuple.localPort,
      rtcpPort: transport.rtcpTuple?.localPort,
    };
  }

  /**
   * Connect a transport (client-side DTLS handshake completion)
   */
  async connectTransport(transportId, dtlsParameters) {
    const transport = this.transports.get(transportId);
    if (!transport) throw new Error(`Transport ${transportId} not found`);

    await transport.connect({ dtlsParameters });
    logger.info(`Transport ${transportId} connected`);
  }

  /**
   * Create a producer (studio camera publishing media)
   */
  async produce(transportId, { kind, rtpParameters, appData }) {
    const transport = this.transports.get(transportId);
    if (!transport) throw new Error(`Transport ${transportId} not found`);

    const producer = await transport.produce({
      kind,
      rtpParameters,
      appData: { ...appData, tableId: transport.appData.tableId },
    });

    const tableId = transport.appData.tableId;
    this.producers.set(producer.id, {
      tableId,
      camera: appData.camera || 'unknown',
      transport,
      producer,
    });

    metricsActiveProducers.inc({ table_id: tableId, camera: appData.camera || 'unknown' });

    producer.on('close', () => {
      this.producers.delete(producer.id);
      metricsActiveProducers.dec({ table_id: tableId, camera: appData.camera || 'unknown' });
      logger.info(`Producer ${producer.id} closed for table ${tableId}`);
    });

    producer.on('score', (score) => {
      logger.debug(`Producer ${producer.id} score`, { score });
    });

    logger.info(`Producer created: ${producer.id} (${kind}) for table ${tableId}`);
    return { id: producer.id };
  }

  /**
   * Create a consumer (player viewing a table stream)
   */
  async consume(transportId, { producerId, rtpCapabilities, appData }) {
    const transport = this.transports.get(transportId);
    if (!transport) throw new Error(`Transport ${transportId} not found`);

    const tableId = transport.appData.tableId;
    const router = this.routers.get(tableId);
    if (!router) throw new Error(`Router not found for table ${tableId}`);

    // Verify the consumer can receive the producer's media
    if (!router.canConsume({ producerId, rtpCapabilities })) {
      throw new Error('Cannot consume: incompatible RTP capabilities');
    }

    const consumer = await transport.consume({
      producerId,
      rtpCapabilities,
      paused: true, // Start paused, resume after client is ready
      appData: { ...appData, tableId },
    });

    const startTime = Date.now();
    this.consumers.set(consumer.id, {
      tableId,
      playerId: appData.playerId,
      transport,
      consumer,
      startTime,
    });

    metricsActiveConsumers.inc({ table_id: tableId });

    consumer.on('close', () => {
      const duration = (Date.now() - startTime) / 1000;
      metricsConnectionDuration.observe({ table_id: tableId }, duration);
      metricsActiveConsumers.dec({ table_id: tableId });
      this.consumers.delete(consumer.id);
      logger.info(`Consumer ${consumer.id} closed (duration: ${duration.toFixed(1)}s)`);
    });

    consumer.on('producerclose', () => {
      logger.info(`Producer closed for consumer ${consumer.id}`);
      consumer.close();
    });

    consumer.on('score', (score) => {
      // Adaptive: if consumer score is low, suggest lower quality layer
      if (score.score < 5) {
        logger.warn(`Low consumer score for ${consumer.id}`, { score });
      }
    });

    consumer.on('layerschange', (layers) => {
      logger.debug(`Consumer ${consumer.id} layers changed`, { layers });
    });

    return {
      id: consumer.id,
      producerId,
      kind: consumer.kind,
      rtpParameters: consumer.rtpParameters,
      appData: consumer.appData,
    };
  }

  /**
   * Resume a consumer (called after client signals readiness)
   */
  async resumeConsumer(consumerId) {
    const entry = this.consumers.get(consumerId);
    if (!entry) throw new Error(`Consumer ${consumerId} not found`);
    await entry.consumer.resume();
    logger.info(`Consumer ${consumerId} resumed`);
  }

  /**
   * Set preferred simulcast layer for a consumer
   */
  async setConsumerPreferredLayers(consumerId, { spatialLayer, temporalLayer }) {
    const entry = this.consumers.get(consumerId);
    if (!entry) throw new Error(`Consumer ${consumerId} not found`);
    await entry.consumer.setPreferredLayers({ spatialLayer, temporalLayer });
    logger.debug(`Consumer ${consumerId} preferred layers set`, { spatialLayer, temporalLayer });
  }

  /**
   * Get Router RTP capabilities for a table (sent to client for negotiation)
   */
  async getRouterRtpCapabilities(tableId) {
    const router = await this.getOrCreateRouter(tableId);
    return router.rtpCapabilities;
  }

  /**
   * Get stats for monitoring dashboards
   */
  getStats() {
    return {
      workers: this.workers.length,
      routers: this.routers.size,
      producers: this.producers.size,
      consumers: this.consumers.size,
      transports: this.transports.size,
      tables: [...this.routers.keys()],
    };
  }
}

// =============================================================================
// HTTP + WebSocket Server
// =============================================================================
async function main() {
  const sfu = new LiveCasinoSFU();
  await sfu.init();

  const app = express();
  app.use(express.json());

  // Health check
  app.get('/health', (req, res) => {
    res.json({ status: 'healthy', ...sfu.getStats() });
  });

  // Prometheus metrics
  app.get('/metrics', async (req, res) => {
    res.set('Content-Type', register.contentType);
    res.end(await register.metrics());
  });

  // REST API for service-to-service calls
  app.get('/api/v1/tables/:tableId/rtp-capabilities', async (req, res) => {
    try {
      const caps = await sfu.getRouterRtpCapabilities(req.params.tableId);
      res.json(caps);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  const server = http.createServer(app);
  const io = new SocketIO(server, {
    cors: { origin: '*', methods: ['GET', 'POST'] },
    transports: ['websocket'],
    pingInterval: 10000,
    pingTimeout: 5000,
  });

  // --- Socket.IO signaling ---
  io.on('connection', (socket) => {
    logger.info(`Client connected: ${socket.id}`);

    socket.on('getRouterRtpCapabilities', async ({ tableId }, callback) => {
      try {
        const caps = await sfu.getRouterRtpCapabilities(tableId);
        callback({ rtpCapabilities: caps, iceServers: config.iceServers });
      } catch (err) {
        callback({ error: err.message });
      }
    });

    socket.on('createWebRtcTransport', async ({ tableId }, callback) => {
      try {
        const params = await sfu.createWebRtcTransport(tableId);
        callback(params);
      } catch (err) {
        callback({ error: err.message });
      }
    });

    socket.on('connectTransport', async ({ transportId, dtlsParameters }, callback) => {
      try {
        await sfu.connectTransport(transportId, dtlsParameters);
        callback({ success: true });
      } catch (err) {
        callback({ error: err.message });
      }
    });

    socket.on('produce', async ({ transportId, kind, rtpParameters, appData }, callback) => {
      try {
        const result = await sfu.produce(transportId, { kind, rtpParameters, appData });
        callback(result);
      } catch (err) {
        callback({ error: err.message });
      }
    });

    socket.on('consume', async ({ transportId, producerId, rtpCapabilities, appData }, callback) => {
      try {
        const result = await sfu.consume(transportId, { producerId, rtpCapabilities, appData });
        callback(result);
      } catch (err) {
        callback({ error: err.message });
      }
    });

    socket.on('resumeConsumer', async ({ consumerId }, callback) => {
      try {
        await sfu.resumeConsumer(consumerId);
        callback({ success: true });
      } catch (err) {
        callback({ error: err.message });
      }
    });

    socket.on('setPreferredLayers', async ({ consumerId, spatialLayer, temporalLayer }, callback) => {
      try {
        await sfu.setConsumerPreferredLayers(consumerId, { spatialLayer, temporalLayer });
        callback({ success: true });
      } catch (err) {
        callback({ error: err.message });
      }
    });

    socket.on('disconnect', () => {
      logger.info(`Client disconnected: ${socket.id}`);
    });
  });

  // Start servers
  server.listen(config.listenPort, config.listenIp, () => {
    logger.info(`mediasoup SFU listening on ${config.listenIp}:${config.listenPort}`);
  });

  // Metrics server on separate port
  const metricsApp = express();
  metricsApp.get('/metrics', async (req, res) => {
    res.set('Content-Type', register.contentType);
    res.end(await register.metrics());
  });
  metricsApp.listen(config.metricsPort, () => {
    logger.info(`Metrics server on port ${config.metricsPort}`);
  });
}

main().catch((err) => {
  logger.error('Failed to start SFU', { error: err.message });
  process.exit(1);
});

module.exports = { config, LiveCasinoSFU };
