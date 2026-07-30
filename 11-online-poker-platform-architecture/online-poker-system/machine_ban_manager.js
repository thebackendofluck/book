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
 * Machine Ban Manager for Online Poker Platform
 *
 * Hardware fingerprinting, IP banning, similarity-based detection,
 * ban appeals, and expiry cleanup — all backed by Redis sets.
 *
 * Production patterns:
 *   - Weighted fingerprint components (CPU 30%, storage 25%, memory 20%,
 *     network 15%, BIOS 10%)
 *   - Component-level MD5 hashes combined into a SHA-256 composite
 *   - 80 % similarity threshold for "similar banned machine" detection
 *   - Appeal queue with pending/approved/rejected lifecycle
 *   - Automatic cleanup of time-limited bans
 */

const crypto = require('crypto');

class MachineBanManager {
  constructor(logger, redisClient) {
    this.logger = logger;
    this.redis = redisClient;

    this.bannedFingerprintsKey = 'banned_fingerprints';
    this.bannedIPsKey = 'banned_ips';

    // How much each component contributes to overall fingerprint match
    this.fingerprintWeights = {
      cpu: 0.3,
      memory: 0.2,
      storage: 0.25,
      network: 0.15,
      bios: 0.1
    };

    this.logger.info('MachineBanManager initialized');
  }

  // ----------------------------------------------------------------
  // Connection-time ban check (IP then fingerprint then similarity)
  // ----------------------------------------------------------------
  async checkMachineBan(clientData) {
    try {
      const { fingerprint, ipAddress } = clientData;

      if (ipAddress && await this._isIPBanned(ipAddress)) {
        this.logger.warn(`Banned IP attempted connection: ${ipAddress}`);
        return true;
      }
      if (fingerprint && await this._isFingerprintBanned(fingerprint)) {
        this.logger.warn('Banned hardware fingerprint attempted connection');
        return true;
      }
      if (fingerprint && await this._checkSimilarBanned(fingerprint)) {
        this.logger.warn('Similar banned machine detected');
        return true;
      }
      return false;
    } catch (error) {
      this.logger.error('Error checking machine ban:', error);
      return false; // fail-open to avoid false positives
    }
  }

  // ----------------------------------------------------------------
  // Fingerprint generation
  // ----------------------------------------------------------------
  generateHardwareFingerprint(data) {
    const components = {
      cpu: this._hashComponent(data.hardware?.cpu, ['manufacturer', 'brand', 'cores', 'speed']),
      memory: this._hashComponent(data.hardware?.memory, ['total', 'free', 'used']),
      storage: this._hashArray(data.hardware?.storage, ['device', 'type', 'serial', 'size']),
      network: this._hashArray(
        (data.network || []).filter(i => !i.internal),
        ['mac', 'ip4', 'ip6']
      ),
      bios: this._hashComponent(data.hardware?.bios, ['vendor', 'version', 'releaseDate'])
    };

    const combined = Object.entries(components)
      .map(([k, v]) => `${k}:${v}:${this.fingerprintWeights[k] || 0.1}`)
      .join('|');

    const hash = crypto.createHash('sha256').update(combined).digest('hex');
    const confidence = Object.values(components).filter(h => h !== 'unknown').length /
      Object.keys(components).length;

    return { hash, components, collectedAt: new Date(), confidence };
  }

  _hashComponent(obj, fields) {
    if (!obj) return 'unknown';
    const str = fields.map(f => obj[f] ?? '').join('|');
    return crypto.createHash('md5').update(str).digest('hex');
  }

  _hashArray(arr, fields) {
    if (!arr || !Array.isArray(arr) || arr.length === 0) return 'unknown';
    const str = arr.map(item => fields.map(f => item[f] ?? '').join('|')).join(';');
    return crypto.createHash('md5').update(str).digest('hex');
  }

  // ----------------------------------------------------------------
  // Ban / unban
  // ----------------------------------------------------------------
  async banMachine(fingerprint, reason, banData) {
    const entry = {
      fingerprint: fingerprint.hash,
      reason,
      bannedAt: new Date(),
      bannedBy: banData.bannedBy || 'system',
      banDuration: banData.duration || null,
      components: fingerprint.components,
      confidence: fingerprint.confidence
    };
    await this.redis.sadd(this.bannedFingerprintsKey, fingerprint.hash);
    await this.redis.set(`ban_details:${fingerprint.hash}`, JSON.stringify(entry));

    if (banData.ipAddress) {
      await this.banIP(banData.ipAddress, reason, banData);
    }
    this.logger.warn(`Machine banned: ${fingerprint.hash} — ${reason}`);
    return true;
  }

  async banIP(ipAddress, reason, banData) {
    await this.redis.sadd(this.bannedIPsKey, ipAddress);
    await this.redis.set(`ip_ban_details:${ipAddress}`, JSON.stringify({
      ipAddress, reason,
      bannedAt: new Date(),
      bannedBy: banData.bannedBy || 'system',
      banDuration: banData.duration || null
    }));
    this.logger.warn(`IP banned: ${ipAddress}`);
  }

  async unbanMachine(identifier, reason) {
    const isIP = identifier.includes('.');
    if (isIP) {
      await this.redis.srem(this.bannedIPsKey, identifier);
      await this.redis.del(`ip_ban_details:${identifier}`);
    } else {
      await this.redis.srem(this.bannedFingerprintsKey, identifier);
      await this.redis.del(`ban_details:${identifier}`);
    }
    this.logger.info(`Unbanned: ${identifier} — ${reason}`);
  }

  // ----------------------------------------------------------------
  // Similarity check — weighted component comparison
  // ----------------------------------------------------------------
  async _checkSimilarBanned(newFingerprint) {
    const bannedHashes = await this.redis.smembers(this.bannedFingerprintsKey);
    for (const hash of bannedHashes) {
      const raw = await this.redis.get(`ban_details:${hash}`);
      if (!raw) continue;
      const details = JSON.parse(raw);
      if (!details.components) continue;

      const similarity = this._calculateSimilarity(
        newFingerprint.components, details.components
      );
      if (similarity > 0.8) return true;
    }
    return false;
  }

  _calculateSimilarity(c1, c2) {
    let totalSim = 0, totalWeight = 0;
    for (const [component, weight] of Object.entries(this.fingerprintWeights)) {
      totalWeight += weight;
      if (c1[component] && c2[component] &&
          c1[component] !== 'unknown' && c2[component] !== 'unknown') {
        totalSim += (c1[component] === c2[component] ? 1 : 0) * weight;
      }
    }
    return totalWeight > 0 ? totalSim / totalWeight : 0;
  }

  // ----------------------------------------------------------------
  // Ban appeal lifecycle
  // ----------------------------------------------------------------
  async submitBanAppeal(identifier, appealData) {
    const appeal = {
      identifier,
      reason: appealData.reason,
      submittedAt: new Date(),
      status: 'pending',
      appealId: crypto.randomUUID()
    };
    await this.redis.setex(`ban_appeal:${appeal.appealId}`, 86400 * 30, JSON.stringify(appeal));
    await this.redis.sadd('ban_appeals_pending', appeal.appealId);
    this.logger.info(`Ban appeal submitted: ${appeal.appealId}`);
    return appeal.appealId;
  }

  async reviewBanAppeal(appealId, decision, reviewer, reason) {
    const raw = await this.redis.get(`ban_appeal:${appealId}`);
    if (!raw) throw new Error('Appeal not found');
    const appeal = JSON.parse(raw);
    appeal.status = decision;
    appeal.reviewedAt = new Date();
    appeal.reviewer = reviewer;
    appeal.reviewReason = reason;

    await this.redis.setex(`ban_appeal:${appealId}`, 86400 * 30, JSON.stringify(appeal));
    await this.redis.srem('ban_appeals_pending', appealId);

    if (decision === 'approved') {
      await this.unbanMachine(appeal.identifier, `Appeal approved: ${reason}`);
    }
    return true;
  }

  // ----------------------------------------------------------------
  // Cleanup expired bans
  // ----------------------------------------------------------------
  async cleanupExpiredBans() {
    const hashes = await this.redis.smembers(this.bannedFingerprintsKey);
    const now = Date.now();
    for (const hash of hashes) {
      const raw = await this.redis.get(`ban_details:${hash}`);
      if (!raw) continue;
      const ban = JSON.parse(raw);
      if (ban.banDuration && ban.bannedAt) {
        const expiry = new Date(ban.bannedAt).getTime() + ban.banDuration * 1000;
        if (now > expiry) await this.unbanMachine(hash, 'Ban expired');
      }
    }
  }

  // ----------------------------------------------------------------
  // Helpers
  // ----------------------------------------------------------------
  async _isIPBanned(ip) {
    return this.redis.sismember(this.bannedIPsKey, ip);
  }

  async _isFingerprintBanned(fp) {
    const hash = typeof fp === 'string' ? fp : fp.hash;
    return this.redis.sismember(this.bannedFingerprintsKey, hash);
  }

  async getBanStatistics() {
    return {
      bannedMachines: await this.redis.scard(this.bannedFingerprintsKey),
      bannedIPs: await this.redis.scard(this.bannedIPsKey),
      pendingAppeals: await this.redis.scard('ban_appeals_pending')
    };
  }
}

module.exports = { MachineBanManager };
