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
 * streaming-orchestrator.js — Live Casino Streaming Orchestrator
 *
 * Manages mediasoup WebRTC workers, per-table routers, producer transports
 * (studio cameras), and consumer transports (player viewers) for live casino
 * streaming infrastructure.
 *
 * Architecture:
 *   - Multiple mediasoup Worker processes (one per CPU core)
 *   - Per-table Router with Simulcast (720p / 1080p / 4K layers)
 *   - Producer transports for studio cameras (DTLS-SRTP)
 *   - Consumer transports for player viewers (WebRTC)
 *   - Redis for session resumption (DTLS cache) and stats
 *
 * Chapter 13 — Live Casino Streaming Infrastructure
 */

'use strict';

const mediasoup = require('mediasoup');

const WORKER_CONFIG = {
  rtcMinPort: 10000,
  rtcMaxPort: 10999,
  logLevel: 'warn',
  logTags: ['info', 'ice', 'dtls', 'rtp', 'srtp', 'rtcp'],
};

const ROUTER_CODECS = [
  {
    kind: 'audio',
    mimeType: 'audio/opus',
    clockRate: 48000,
    channels: 2,
    parameters: {
      minptime: 10,
      useinbandfec: 1,
      maxaveragebitrate: 510000,
    },
  },
  {
    kind: 'video',
    mimeType: 'video/H264',
    clockRate: 90000,
    parameters: {
      'packetization-mode': 1,
      'profile-level-id': '42e01f',
      'level-asymmetry-allowed': 1,
    },
  },
  {
    kind: 'video',
    mimeType: 'video/VP9',
    clockRate: 90000,
    parameters: { 'profile-id': '0' },
  },
];

const WEBRTC_TRANSPORT_OPTIONS = {
  listenIps: [{ ip: '0.0.0.0', announcedIp: process.env.PUBLIC_IP || '127.0.0.1' }],
  enableUdp: true,
  enableTcp: true,
  preferUdp: true,
};

class StreamingOrchestrator {
  #workers = [];
  #nextWorkerIdx = 0;
  #routers = new Map();    // tableId -> Router
  #transports = new Map(); // transportId -> Transport
  #producers = new Map();  // producerId -> Producer
  #consumers = new Map();  // consumerId -> Consumer
  #redis;
  #log;

  constructor({ redisClient, logger } = {}) {
    this.#redis = redisClient;
    this.#log = logger || console;
  }

  async init(numWorkers = Math.min(require('os').cpus().length, 4)) {
    for (let i = 0; i < numWorkers; i++) {
      const worker = await mediasoup.createWorker(WORKER_CONFIG);
      worker.appData = { connections: 0, index: i };

      worker.on('died', (err) => {
        this.#log.error(`mediasoup worker[${i}] died:`, err);
        this.#workers.splice(this.#workers.indexOf(worker), 1);
        this._respawnWorker(i);
      });

      this.#workers.push(worker);
      this.#log.info(`mediasoup worker[${i}] started — PID ${worker.pid}`);
    }
  }

  async _respawnWorker(index) {
    try {
      const worker = await mediasoup.createWorker(WORKER_CONFIG);
      worker.appData = { connections: 0, index };
      worker.on('died', (err) => {
        this.#log.error(`mediasoup worker[${index}] died again:`, err);
      });
      this.#workers.splice(index, 0, worker);
      this.#log.info(`mediasoup worker[${index}] respawned`);
    } catch (err) {
      this.#log.error('Failed to respawn worker:', err);
    }
  }

  // ---------------------------------------------------------------------------
  // Router management (one per table)
  // ---------------------------------------------------------------------------

  async createTableRouter(tableId) {
    // Round-robin worker selection, prefer workers with fewer connections
    const worker = this.#workers.reduce((min, w) =>
      w.appData.connections < min.appData.connections ? w : min
    );

    const router = await worker.createRouter({ mediaCodecs: ROUTER_CODECS });
    this.#routers.set(tableId, router);
    worker.appData.connections++;

    this.#log.info(`Router created for table ${tableId} on worker ${worker.appData.index}`);
    return router;
  }

  hasRouter(tableId) {
    return this.#routers.has(tableId);
  }

  getRouter(tableId) {
    return this.#routers.get(tableId);
  }

  async closeTableRouter(tableId) {
    const router = this.#routers.get(tableId);
    if (!router) return;
    router.close();
    this.#routers.delete(tableId);
    this.#log.info(`Router closed for table ${tableId}`);
  }

  // ---------------------------------------------------------------------------
  // Transport management
  // ---------------------------------------------------------------------------

  async createProducerTransport(tableId) {
    const router = this.#routers.get(tableId);
    if (!router) throw new Error(`No router for table ${tableId}`);

    const transport = await router.createWebRtcTransport({
      ...WEBRTC_TRANSPORT_OPTIONS,
      initialAvailableOutgoingBitrate: 10_000_000, // 10 Mbps for studio
    });

    this.#transports.set(transport.id, transport);
    this.#log.info(`Producer transport ${transport.id} created for table ${tableId}`);

    return {
      id: transport.id,
      iceParameters: transport.iceParameters,
      iceCandidates: transport.iceCandidates,
      dtlsParameters: transport.dtlsParameters,
    };
  }

  async createConsumerTransport(tableId) {
    const router = this.#routers.get(tableId);
    if (!router) throw new Error(`No router for table ${tableId}`);

    const transport = await router.createWebRtcTransport({
      ...WEBRTC_TRANSPORT_OPTIONS,
      initialAvailableOutgoingBitrate: 5_000_000, // 5 Mbps for viewer
    });

    this.#transports.set(transport.id, transport);
    return {
      id: transport.id,
      iceParameters: transport.iceParameters,
      iceCandidates: transport.iceCandidates,
      dtlsParameters: transport.dtlsParameters,
    };
  }

  async connectTransport(transportId, dtlsParameters) {
    const transport = this.#transports.get(transportId);
    if (!transport) throw new Error(`Transport ${transportId} not found`);
    await transport.connect({ dtlsParameters });
  }

  async closeTransport(transportId) {
    const transport = this.#transports.get(transportId);
    if (transport) {
      transport.close();
      this.#transports.delete(transportId);
    }
  }

  // ---------------------------------------------------------------------------
  // Producer / Consumer management
  // ---------------------------------------------------------------------------

  async produce(transportId, kind, rtpParameters, appData = {}) {
    const transport = this.#transports.get(transportId);
    if (!transport) throw new Error(`Transport ${transportId} not found`);

    const producer = await transport.produce({ kind, rtpParameters, appData });
    this.#producers.set(producer.id, producer);

    if (this.#redis) {
      await this.#redis.set(
        `producer:${producer.id}`,
        JSON.stringify({ tableId: appData.tableId, kind, ts: Date.now() }),
        { EX: 3600 }
      );
    }

    this.#log.info(`Producer ${producer.id} (${kind}) created for table ${appData.tableId}`);
    return producer.id;
  }

  async consume(transportId, producerId, rtpCapabilities) {
    const transport = this.#transports.get(transportId);
    const producer = this.#producers.get(producerId);
    if (!transport) throw new Error(`Transport ${transportId} not found`);
    if (!producer) throw new Error(`Producer ${producerId} not found`);

    const router = this.#routers.get(producer.appData.tableId);
    if (!router.canConsume({ producerId, rtpCapabilities })) {
      throw new Error('Client device cannot consume this producer (codec mismatch)');
    }

    const consumer = await transport.consume({ producerId, rtpCapabilities, paused: false });
    this.#consumers.set(consumer.id, consumer);

    return {
      id: consumer.id,
      producerId,
      kind: consumer.kind,
      rtpParameters: consumer.rtpParameters,
    };
  }

  // ---------------------------------------------------------------------------
  // Stats
  // ---------------------------------------------------------------------------

  async getTableStats(tableId) {
    const producers = [...this.#producers.values()].filter((p) => p.appData.tableId === tableId);
    const consumers = [...this.#consumers.values()].filter((c) => c.appData.tableId === tableId);

    return {
      tableId,
      producers: producers.length,
      viewers: Math.floor(consumers.length / 2), // audio + video per viewer
      routerExists: this.#routers.has(tableId),
      uptime: process.uptime(),
    };
  }

  getWorkerStats() {
    return this.#workers.map((w) => ({
      pid: w.pid,
      index: w.appData.index,
      connections: w.appData.connections,
      closed: w.closed,
    }));
  }

  // ---------------------------------------------------------------------------
  // Cleanup
  // ---------------------------------------------------------------------------

  async close() {
    for (const worker of this.#workers) {
      worker.close();
    }
    this.#workers = [];
    this.#routers.clear();
    this.#transports.clear();
    this.#producers.clear();
    this.#consumers.clear();
    this.#log.info('StreamingOrchestrator closed');
  }
}

module.exports = { StreamingOrchestrator };
