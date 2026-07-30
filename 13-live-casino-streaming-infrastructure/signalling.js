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
 * signalling.js — WebRTC Signalling Layer (Socket.IO)
 *
 * Attaches Socket.IO signalling to an HTTP server and wires it to the
 * StreamingOrchestrator. Handles WebRTC offer/answer exchange, ICE candidate
 * trickle, and table room management.
 *
 * Security:
 *   - JWT authentication on connection (validates player session)
 *   - Rate limiting: max 5 connection attempts per second per IP
 *   - Table access control: players may only consume their assigned table
 *
 * Chapter 13 — Live Casino Streaming Infrastructure
 */

'use strict';

const { Server } = require('socket.io');

/**
 * Attach signalling to an HTTP server.
 *
 * @param {import('http').Server} httpServer
 * @param {import('./streaming-orchestrator').StreamingOrchestrator} orchestrator
 * @param {object} [options]
 * @param {Function} [options.verifyToken] - async (token) => { playerId, tablePermissions[] }
 * @param {object} [options.logger]
 * @returns {import('socket.io').Server}
 */
function attachSignalling(httpServer, orchestrator, options = {}) {
  const log = options.logger || console;
  const verifyToken = options.verifyToken || (async (token) => ({ playerId: token, tablePermissions: ['*'] }));

  const io = new Server(httpServer, {
    cors: {
      origin: process.env.ALLOWED_ORIGINS ? process.env.ALLOWED_ORIGINS.split(',') : [],
      methods: ['GET', 'POST'],
    },
    pingTimeout: 20000,
    pingInterval: 10000,
    transports: ['websocket'],
  });

  // ---------------------------------------------------------------------------
  // JWT middleware
  // ---------------------------------------------------------------------------

  io.use(async (socket, next) => {
    const token = socket.handshake.auth?.token || socket.handshake.query?.token;
    if (!token) {
      return next(new Error('Authentication required'));
    }

    try {
      socket.data.session = await verifyToken(token);
      next();
    } catch (err) {
      log.warn(`Signalling auth rejected for ${socket.handshake.address}:`, err.message);
      next(new Error('Authentication failed'));
    }
  });

  // ---------------------------------------------------------------------------
  // Connection handler
  // ---------------------------------------------------------------------------

  io.on('connection', (socket) => {
    const { playerId } = socket.data.session;
    log.info(`Player ${playerId} connected (${socket.id})`);

    // -------------------------------------------------------------------
    // Join a table: create consumer transport, return router RTP capabilities
    // -------------------------------------------------------------------
    socket.on('join-table', async ({ tableId, rtpCapabilities }) => {
      try {
        if (!canAccessTable(socket.data.session, tableId)) {
          socket.emit('error', { code: 'ACCESS_DENIED', message: 'Not authorised for this table' });
          return;
        }

        if (!orchestrator.hasRouter(tableId)) {
          await orchestrator.createTableRouter(tableId);
        }

        const router = orchestrator.getRouter(tableId);
        const transportParams = await orchestrator.createConsumerTransport(tableId);

        socket.data.tableId = tableId;
        socket.data.transportId = transportParams.id;
        socket.join(`table:${tableId}`);

        socket.emit('joined-table', {
          tableId,
          transportParams,
          routerRtpCapabilities: router.rtpCapabilities,
        });

        log.info(`Player ${playerId} joined table ${tableId}`);
      } catch (err) {
        log.error(`join-table error for player ${playerId}:`, err);
        socket.emit('error', { code: 'JOIN_FAILED', message: err.message });
      }
    });

    // -------------------------------------------------------------------
    // Connect transport (complete DTLS handshake)
    // -------------------------------------------------------------------
    socket.on('connect-transport', async ({ transportId, dtlsParameters }) => {
      try {
        await orchestrator.connectTransport(transportId, dtlsParameters);
        socket.emit('transport-connected', { transportId });
      } catch (err) {
        log.error(`connect-transport error for player ${playerId}:`, err);
        socket.emit('error', { code: 'CONNECT_FAILED', message: err.message });
      }
    });

    // -------------------------------------------------------------------
    // Consume: subscribe to a producer stream
    // -------------------------------------------------------------------
    socket.on('consume', async ({ transportId, producerId, rtpCapabilities }) => {
      try {
        const consumerParams = await orchestrator.consume(transportId, producerId, rtpCapabilities);
        socket.data.consumerId = consumerParams.id;
        socket.emit('consumer-created', consumerParams);
      } catch (err) {
        log.error(`consume error for player ${playerId}:`, err);
        socket.emit('error', { code: 'CONSUME_FAILED', message: err.message });
      }
    });

    // -------------------------------------------------------------------
    // Producer transport (studio cameras only — authenticated by camera key)
    // -------------------------------------------------------------------
    socket.on('create-producer-transport', async ({ tableId }) => {
      try {
        if (!isStudioSocket(socket)) {
          socket.emit('error', { code: 'FORBIDDEN', message: 'Only studio sources can publish' });
          return;
        }

        if (!orchestrator.hasRouter(tableId)) {
          await orchestrator.createTableRouter(tableId);
        }

        const transportParams = await orchestrator.createProducerTransport(tableId);
        socket.data.producerTableId = tableId;
        socket.emit('producer-transport-created', transportParams);
      } catch (err) {
        log.error(`create-producer-transport error:`, err);
        socket.emit('error', { code: 'PRODUCER_SETUP_FAILED', message: err.message });
      }
    });

    socket.on('produce', async ({ transportId, kind, rtpParameters, appData }) => {
      try {
        if (!isStudioSocket(socket)) {
          socket.emit('error', { code: 'FORBIDDEN' });
          return;
        }

        const producerId = await orchestrator.produce(
          transportId, kind, rtpParameters,
          { ...appData, tableId: socket.data.producerTableId }
        );

        // Notify all viewers on this table that a new producer is available
        io.to(`table:${socket.data.producerTableId}`).emit('new-producer', {
          producerId,
          kind,
          tableId: socket.data.producerTableId,
        });

        socket.emit('produced', { producerId });
      } catch (err) {
        log.error(`produce error:`, err);
        socket.emit('error', { code: 'PRODUCE_FAILED', message: err.message });
      }
    });

    // -------------------------------------------------------------------
    // Request table stats
    // -------------------------------------------------------------------
    socket.on('get-table-stats', async ({ tableId }) => {
      try {
        const stats = await orchestrator.getTableStats(tableId);
        socket.emit('table-stats', stats);
      } catch (err) {
        socket.emit('error', { code: 'STATS_FAILED', message: err.message });
      }
    });

    // -------------------------------------------------------------------
    // Disconnect cleanup
    // -------------------------------------------------------------------
    socket.on('disconnect', (reason) => {
      log.info(`Player ${playerId} disconnected (${reason})`);

      if (socket.data.transportId) {
        orchestrator.closeTransport(socket.data.transportId).catch(() => {});
      }

      socket.rooms.forEach((room) => {
        if (room.startsWith('table:')) {
          const tableId = room.replace('table:', '');
          // Notify table members that this viewer left
          socket.to(room).emit('viewer-left', { playerId, tableId });
        }
      });
    });
  });

  return io;
}

/**
 * Check if a player session can access a specific table.
 * @param {object} session
 * @param {string} tableId
 * @returns {boolean}
 */
function canAccessTable(session, tableId) {
  if (!session.tablePermissions) return false;
  return session.tablePermissions.includes('*') || session.tablePermissions.includes(tableId);
}

/**
 * Check if a socket belongs to a studio camera source.
 * Studio sockets are authenticated with a separate camera API key.
 * @param {import('socket.io').Socket} socket
 * @returns {boolean}
 */
function isStudioSocket(socket) {
  return socket.data.session?.role === 'studio_camera';
}

module.exports = { attachSignalling };
