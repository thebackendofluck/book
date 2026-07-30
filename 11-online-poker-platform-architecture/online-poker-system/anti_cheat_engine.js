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
 * Anti-Cheat Engine for Online Poker Platform
 *
 * Production-derived implementation of behavioral analysis, bot detection,
 * collusion detection, and automated ban scoring for a real-time poker system.
 *
 * Key patterns:
 *   - Sliding-window reaction-time analysis with configurable thresholds
 *   - Bet-pattern consistency scoring (ratio-based similarity)
 *   - Collusion detection via synchronized-action and shared-IP checks
 *   - Weighted ban-score accumulation with auto-ban and review-queue triggers
 *   - Redis-backed stat caching with TTL
 */

const crypto = require('crypto');

class AntiCheatEngine {
  constructor(logger, redisClient) {
    this.logger = logger;
    this.redis = redisClient;

    // --- Configurable thresholds ---
    this.thresholds = {
      reactionTime: {
        min: 0.1,          // seconds — below this is suspicious
        max: 30,
        suspicious: 0.05   // very fast (bot-like)
      },
      winRate: {
        suspicious: 0.65,  // 65 % over 50+ hands
        botLike: 0.75
      },
      sessionDuration: {
        min: 300,           // 5 min
        max: 28800,         // 8 h
        suspicious: 3600    // 1 h continuous
      },
      betConsistency: {
        suspicious: 0.95    // 95 % identical pattern
      }
    };

    this.playerStats = new Map();
    this.suspiciousPlayers = new Set();
    this.bannedPlayers = new Set();

    this.logger.info('AntiCheatEngine initialized');
  }

  // ----------------------------------------------------------------
  // Public entry point — called on every player action
  // ----------------------------------------------------------------
  async analyzePlayerBehavior(playerId, actionData) {
    try {
      let stats = this.playerStats.get(playerId);
      if (!stats) {
        stats = this._initStats(playerId);
        this.playerStats.set(playerId, stats);
      }

      this._updateStats(stats, actionData);

      const issues = await this._runChecks(stats, actionData);
      if (issues.length > 0) {
        await this._handleSuspiciousActivity(playerId, issues, actionData);
      }

      await this._persistStats(playerId, stats);
    } catch (error) {
      this.logger.error('Error analyzing player behavior:', error);
    }
  }

  // ----------------------------------------------------------------
  // Internal: initialise empty stats object
  // ----------------------------------------------------------------
  _initStats(playerId) {
    return {
      playerId,
      totalHands: 0,
      totalWins: 0,
      totalLosses: 0,
      totalProfit: 0,
      sessionStart: new Date(),
      lastAction: null,
      reactionTimes: [],       // sliding window (last 100)
      betPatterns: [],          // sliding window (last 50)
      winStreak: 0,
      lossStreak: 0,
      suspiciousFlags: [],
      banScore: 0,
      lastAnalysis: new Date()
    };
  }

  // ----------------------------------------------------------------
  // Internal: update stats with new action
  // ----------------------------------------------------------------
  _updateStats(stats, actionData) {
    const now = new Date();
    stats.totalHands++;

    // Track reaction time (seconds between consecutive actions)
    if (stats.lastAction && actionData.timestamp) {
      const reactionTime =
        (new Date(actionData.timestamp) - new Date(stats.lastAction.timestamp)) / 1000;
      if (reactionTime > 0 && reactionTime < 60) {
        stats.reactionTimes.push(reactionTime);
        if (stats.reactionTimes.length > 100) stats.reactionTimes.shift();
      }
    }

    // Track bet amounts
    if (actionData.action && actionData.action.amount) {
      stats.betPatterns.push({
        action: actionData.action.actionType,
        amount: actionData.action.amount,
        timestamp: now
      });
      if (stats.betPatterns.length > 50) stats.betPatterns.shift();
    }

    stats.lastAction = { ...actionData, timestamp: now };
    stats.lastAnalysis = now;
  }

  // ----------------------------------------------------------------
  // Internal: run all anti-cheat checks
  // ----------------------------------------------------------------
  async _runChecks(stats, actionData) {
    const issues = [];

    // --- Reaction-time checks ---
    if (stats.reactionTimes.length >= 10) {
      const avg =
        stats.reactionTimes.reduce((a, b) => a + b, 0) / stats.reactionTimes.length;
      const fastCount = stats.reactionTimes.filter(
        t => t < this.thresholds.reactionTime.suspicious
      ).length;
      const fastRatio = fastCount / stats.reactionTimes.length;

      if (avg < this.thresholds.reactionTime.min) {
        issues.push({
          type: 'FAST_REACTIONS',
          severity: 'HIGH',
          details: `Average reaction time: ${avg.toFixed(3)}s`,
          confidence: 0.9
        });
      }
      if (fastRatio > 0.3) {
        issues.push({
          type: 'BOT_LIKE_REACTIONS',
          severity: 'CRITICAL',
          details: `${(fastRatio * 100).toFixed(1)}% reactions under ${this.thresholds.reactionTime.suspicious}s`,
          confidence: 0.95
        });
      }
    }

    // --- Win-rate checks ---
    if (stats.totalHands >= 50) {
      const winRate = stats.totalWins / stats.totalHands;
      if (winRate > this.thresholds.winRate.botLike) {
        issues.push({
          type: 'UNNATURAL_WIN_RATE',
          severity: 'CRITICAL',
          details: `Win rate: ${(winRate * 100).toFixed(1)}% over ${stats.totalHands} hands`,
          confidence: 0.95
        });
      } else if (winRate > this.thresholds.winRate.suspicious) {
        issues.push({
          type: 'HIGH_WIN_RATE',
          severity: 'MEDIUM',
          details: `Win rate: ${(winRate * 100).toFixed(1)}% over ${stats.totalHands} hands`,
          confidence: 0.7
        });
      }
    }

    // --- Session-duration check ---
    const sessionMinutes = (new Date() - new Date(stats.sessionStart)) / 1000 / 60;
    if (sessionMinutes > this.thresholds.sessionDuration.suspicious) {
      issues.push({
        type: 'LONG_SESSION',
        severity: 'LOW',
        details: `Session duration: ${Math.floor(sessionMinutes)} minutes`,
        confidence: 0.6
      });
    }

    // --- Bet-pattern consistency ---
    if (stats.betPatterns.length >= 20) {
      const consistency = this._calculateBetConsistency(stats.betPatterns);
      if (consistency > this.thresholds.betConsistency.suspicious) {
        issues.push({
          type: 'CONSISTENT_BETTING',
          severity: 'MEDIUM',
          details: `Bet pattern consistency: ${(consistency * 100).toFixed(1)}%`,
          confidence: 0.8
        });
      }
    }

    // --- Collusion signals ---
    const collusionIssues = await this._checkCollusion(stats, actionData);
    issues.push(...collusionIssues);

    return issues;
  }

  // ----------------------------------------------------------------
  // Bet-pattern consistency: ratio of consecutive identical actions
  // ----------------------------------------------------------------
  _calculateBetConsistency(betPatterns) {
    if (betPatterns.length < 2) return 0;
    let consistent = 0;
    for (let i = 1; i < betPatterns.length; i++) {
      const prev = betPatterns[i - 1];
      const curr = betPatterns[i];
      if (prev.action === curr.action) {
        if (['RAISE', 'BET'].includes(prev.action)) {
          const ratio = Math.min(prev.amount, curr.amount) / Math.max(prev.amount, curr.amount);
          if (ratio > 0.8) consistent++;
        } else {
          consistent++;
        }
      }
    }
    return consistent / (betPatterns.length - 1);
  }

  // ----------------------------------------------------------------
  // Collusion detection: shared IPs + synchronized actions
  // ----------------------------------------------------------------
  async _checkCollusion(stats, actionData) {
    const issues = [];
    const tablePlayers = await this._getTablePlayers(actionData.tableId);

    const syncActions = await this._checkSynchronizedActions(tablePlayers, actionData);
    if (syncActions.length > 0) {
      issues.push({
        type: 'SYNCHRONIZED_ACTIONS',
        severity: 'HIGH',
        details: `Synchronized actions with ${syncActions.length} players`,
        confidence: 0.85
      });
    }

    const sharedIPs = await this._checkSharedIPs(tablePlayers);
    if (sharedIPs.length > 0) {
      issues.push({
        type: 'SHARED_IP',
        severity: 'CRITICAL',
        details: 'Shared IP addresses detected',
        confidence: 0.95
      });
    }

    return issues;
  }

  async _checkSynchronizedActions(tablePlayers, actionData) {
    // Production implementation: compare action timestamps across players
    return [];
  }

  async _checkSharedIPs(tablePlayers) {
    // Production implementation: query IP records from session store
    return [];
  }

  async _getTablePlayers(tableId) {
    return [];
  }

  // ----------------------------------------------------------------
  // Ban-score accumulation and enforcement
  // ----------------------------------------------------------------
  async _handleSuspiciousActivity(playerId, issues, actionData) {
    let banIncrease = 0;
    let highestSeverity = 'LOW';
    const weights = { LOW: 1, MEDIUM: 5, HIGH: 10, CRITICAL: 25 };

    for (const issue of issues) {
      banIncrease += weights[issue.severity] || 1;
      if (issue.severity === 'CRITICAL') highestSeverity = 'CRITICAL';
      else if (issue.severity === 'HIGH' && highestSeverity !== 'CRITICAL')
        highestSeverity = 'HIGH';
    }

    const stats = this.playerStats.get(playerId);
    stats.banScore += banIncrease;
    stats.suspiciousFlags.push(...issues);

    this.logger.warn(`Suspicious activity for player ${playerId}`, {
      issues,
      banScore: stats.banScore
    });

    this.suspiciousPlayers.add(playerId);

    if (stats.banScore >= 50 || highestSeverity === 'CRITICAL') {
      await this._banPlayer(playerId, issues);
    } else if (stats.banScore >= 25) {
      await this._flagForReview(playerId, issues);
    }
  }

  async _banPlayer(playerId, issues) {
    this.bannedPlayers.add(playerId);
    this.logger.error(`Player ${playerId} banned`, { issues, banTime: new Date() });
    // In production: update DB, disconnect socket, send notification, write audit log
  }

  async _flagForReview(playerId, issues) {
    this.logger.warn(`Player ${playerId} flagged for review`, { issues });
    // In production: enqueue for manual review
  }

  // ----------------------------------------------------------------
  // Redis persistence
  // ----------------------------------------------------------------
  async _persistStats(playerId, stats) {
    try {
      const key = `player_stats:${playerId}`;
      await this.redis.setex(key, 3600, JSON.stringify(stats));
    } catch (error) {
      this.logger.error('Error persisting player stats:', error);
    }
  }

  // ----------------------------------------------------------------
  // Hardware fingerprint helpers (used by MachineBanManager)
  // ----------------------------------------------------------------
  generateFingerprintHash(fingerprintData) {
    return crypto
      .createHash('sha256')
      .update(JSON.stringify(fingerprintData))
      .digest('hex');
  }

  // ----------------------------------------------------------------
  // Bot detection — scoring across multiple indicators
  // ----------------------------------------------------------------
  async detectBotBehavior(playerId, behaviorData) {
    const indicators = {
      perfectTiming: this._checkPerfectTiming(behaviorData),
      roboticPatterns: this._checkRoboticPatterns(behaviorData),
      noHumanError: this._checkHumanError(behaviorData)
    };
    const botScore =
      Object.values(indicators).filter(Boolean).length /
      Object.keys(indicators).length;

    return { isBot: botScore > 0.7, confidence: botScore, indicators };
  }

  _checkPerfectTiming(data) { return false; }
  _checkRoboticPatterns(data) { return false; }
  _checkHumanError(data) { return true; }
}

module.exports = { AntiCheatEngine };
