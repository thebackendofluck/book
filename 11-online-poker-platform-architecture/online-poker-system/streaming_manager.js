// Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Live Streaming Manager for Online Poker Platform
 *
 * Manages RTMP/HLS/DASH streams for live poker game broadcasting,
 * spectator sessions, chat, quality adaptation, and recording.
 *
 * Production patterns:
 *   - Concurrent stream limits and per-stream viewer caps
 *   - Adaptive bitrate with three quality tiers
 *   - Periodic health checks with automatic timeout cleanup
 *   - Redis-backed stream metadata, viewer sessions, and chat history
 *   - Recording lifecycle (start/stop) with archival hooks
 */

const crypto = require('crypto');

class StreamingManager {
  constructor(logger, redisClient) {
    this.logger = logger;
    this.redis = redisClient;

    this.maxConcurrentStreams = 50;
    this.streamKeyLength = 32;
    this.viewerTimeout = 300_000;   // 5 min
    this.streamTimeout = 3_600_000; // 1 h
    this.maxViewersPerStream = 1000;

    this.activeStreams = new Map();
    this.streamViewers = new Map();

    this.streamQualities = {
      low:    { resolution: '640x360',   bitrate: '800k'  },
      medium: { resolution: '1280x720',  bitrate: '2500k' },
      high:   { resolution: '1920x1080', bitrate: '5000k' }
    };

    this.logger.info('StreamingManager initialized');
  }

  // ----------------------------------------------------------------
  // Stream lifecycle
  // ----------------------------------------------------------------
  async startStream(tableId, streamerData) {
    if (this.activeStreams.size >= this.maxConcurrentStreams) {
      throw new Error('Maximum concurrent streams reached');
    }
    if (this.activeStreams.has(tableId)) {
      throw new Error('Table already has an active stream');
    }

    const streamKey = crypto.randomBytes(this.streamKeyLength / 2).toString('hex');

    const streamConfig = {
      id: crypto.randomUUID(),
      tableId,
      streamerId: streamerData.playerId,
      streamKey,
      startedAt: new Date(),
      status: 'starting',
      viewers: 0,
      quality: streamerData.quality || 'medium',
      // URLs are templates — in production, these resolve via a media gateway
      rtmpUrl: `rtmp://stream.platform.internal/live/${streamKey}`,
      hlsUrl:  `/streams/${streamKey}/index.m3u8`,
      dashUrl: `/streams/${streamKey}/manifest.mpd`,
      metadata: {
        gameType: streamerData.gameType,
        stakes: streamerData.stakes,
        tableName: streamerData.tableName,
        streamerName: streamerData.playerName
      }
    };

    this.activeStreams.set(tableId, streamConfig);
    await this.redis.setex(`stream:${tableId}`, 3600, JSON.stringify(streamConfig));
    this._monitorStream(tableId);

    this.logger.info(`Stream started for table ${tableId}`);
    return streamConfig;
  }

  async stopStream(tableId, reason = 'manual') {
    const stream = this.activeStreams.get(tableId);
    if (!stream) return false;

    stream.status = 'stopped';
    stream.stoppedAt = new Date();
    stream.stopReason = reason;

    // Notify all viewers (WebSocket broadcast in production)
    const viewers = this.streamViewers.get(tableId) || new Set();
    this.logger.debug(`Notifying ${viewers.size} viewers of stream end`);

    this.activeStreams.delete(tableId);
    this.streamViewers.delete(tableId);
    await this.redis.del(`stream:${tableId}`);
    await this._archiveMetadata(stream);

    this.logger.info(`Stream stopped for table ${tableId}: ${reason}`);
    return true;
  }

  // ----------------------------------------------------------------
  // Viewer management
  // ----------------------------------------------------------------
  async addViewer(tableId, viewerData) {
    const stream = this.activeStreams.get(tableId);
    if (!stream) throw new Error('Stream not found');
    if (stream.status !== 'active') throw new Error('Stream not active');

    if (!this.streamViewers.has(tableId)) {
      this.streamViewers.set(tableId, new Set());
    }
    const viewers = this.streamViewers.get(tableId);
    if (viewers.size >= this.maxViewersPerStream) {
      throw new Error('Stream viewer limit reached');
    }

    viewers.add(viewerData.viewerId);
    stream.viewers = viewers.size;

    await this.redis.setex(
      `viewer_session:${viewerData.viewerId}`,
      3600,
      JSON.stringify({ viewerId: viewerData.viewerId, joinedAt: new Date(), tableId })
    );
    await this.redis.setex(`stream:${tableId}`, 3600, JSON.stringify(stream));

    return stream;
  }

  async removeViewer(tableId, viewerId) {
    const viewers = this.streamViewers.get(tableId);
    if (viewers) {
      viewers.delete(viewerId);
      const stream = this.activeStreams.get(tableId);
      if (stream) {
        stream.viewers = viewers.size;
        await this.redis.setex(`stream:${tableId}`, 3600, JSON.stringify(stream));
      }
    }
    await this.redis.del(`viewer_session:${viewerId}`);
  }

  // ----------------------------------------------------------------
  // Quality adaptation
  // ----------------------------------------------------------------
  async adaptStreamQuality(tableId, newQuality) {
    const stream = this.activeStreams.get(tableId);
    if (!stream) throw new Error('Stream not found');
    if (!this.streamQualities[newQuality]) throw new Error('Invalid quality setting');

    const oldQuality = stream.quality;
    stream.quality = newQuality;
    stream.qualityChangedAt = new Date();

    this.logger.info(`Quality changed for ${tableId}: ${oldQuality} -> ${newQuality}`);
    return stream;
  }

  // ----------------------------------------------------------------
  // Chat
  // ----------------------------------------------------------------
  async sendStreamMessage(tableId, messageData) {
    const stream = this.activeStreams.get(tableId);
    if (!stream) throw new Error('Stream not found');

    const message = {
      id: crypto.randomUUID(),
      tableId,
      senderId: messageData.senderId,
      senderName: messageData.senderName,
      content: messageData.content,
      timestamp: new Date(),
      type: messageData.type || 'chat'
    };

    const chatKey = `stream_chat:${tableId}`;
    await this.redis.lpush(chatKey, JSON.stringify(message));
    await this.redis.ltrim(chatKey, 0, 99);

    return message;
  }

  // ----------------------------------------------------------------
  // Recording
  // ----------------------------------------------------------------
  async startRecording(tableId, recordingConfig) {
    const stream = this.activeStreams.get(tableId);
    if (!stream) throw new Error('Stream not found');

    const recording = {
      id: crypto.randomUUID(),
      streamId: stream.id,
      tableId,
      startedAt: new Date(),
      status: 'recording',
      config: recordingConfig
    };
    stream.recording = recording;
    this.logger.info(`Recording started for stream ${tableId}`);
    return recording;
  }

  async stopRecording(tableId) {
    const stream = this.activeStreams.get(tableId);
    if (!stream || !stream.recording) return false;
    stream.recording.status = 'completed';
    stream.recording.stoppedAt = new Date();
    this.logger.info(`Recording stopped for stream ${tableId}`);
    return true;
  }

  // ----------------------------------------------------------------
  // Health monitoring
  // ----------------------------------------------------------------
  _monitorStream(tableId) {
    const stream = this.activeStreams.get(tableId);
    if (!stream) return;

    const healthCheck = setInterval(async () => {
      const s = this.activeStreams.get(tableId);
      if (!s || s.status !== 'active') return;
      const age = Date.now() - new Date(s.startedAt).getTime();
      if (age > this.streamTimeout) {
        this.logger.warn(`Stream timeout for table ${tableId}`);
        await this.stopStream(tableId, 'timeout');
        clearInterval(healthCheck);
      }
    }, 30_000);

    stream._healthCheck = healthCheck;
  }

  // ----------------------------------------------------------------
  // Archival
  // ----------------------------------------------------------------
  async _archiveMetadata(stream) {
    const archiveKey = `archived_stream:${stream.id}`;
    await this.redis.set(archiveKey, JSON.stringify({
      ...stream,
      archivedAt: new Date(),
      peakViewers: stream.stats?.peakViewers || 0
    }));
  }

  // ----------------------------------------------------------------
  // Stats
  // ----------------------------------------------------------------
  async getStreamingStats() {
    const streams = Array.from(this.activeStreams.values()).filter(s => s.status === 'active');
    const totalViewers = streams.reduce((sum, s) => sum + s.viewers, 0);
    return {
      activeStreams: streams.length,
      totalViewers,
      averageViewersPerStream: streams.length > 0 ? totalViewers / streams.length : 0
    };
  }
}

module.exports = { StreamingManager };
