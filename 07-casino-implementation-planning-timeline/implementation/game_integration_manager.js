// Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Game Integration Manager - Chapter 22: Casino Implementation Planning and Timeline
 *
 * Game Provider Integration Layer managing multiple provider connections,
 * session lifecycle, bet/win processing, and WebSocket event routing.
 *
 * Part of the iGaming Platform Engineering book.
 */

class GameIntegrationManager {
  constructor() {
    this.providers = new Map();
    this.activeSessions = new Map();
    this.eventHandlers = new Map();
  }

  registerProvider(providerName, config) {
    this.providers.set(providerName, {
      config,
      client: this.createProviderClient(config),
      health: 'unknown'
    });
  }

  async launchGame(userId, gameId, providerName, options = {}) {
    const provider = this.providers.get(providerName);
    if (!provider) {
      throw new Error(`Provider ${providerName} not registered`);
    }

    try {
      // Get game configuration
      const gameConfig = await this.getGameConfig(gameId, providerName);

      // Create game session
      const sessionData = await provider.client.createSession({
        userId,
        gameId,
        currency: options.currency || 'EUR',
        language: options.language || 'en',
        demo: options.demo || false
      });

      // Store session
      const sessionId = sessionData.sessionId;
      this.activeSessions.set(sessionId, {
        userId,
        gameId,
        providerName,
        startTime: Date.now(),
        lastActivity: Date.now()
      });

      // Set up event listeners
      this.setupGameEvents(sessionId, provider);

      return {
        sessionId,
        launchUrl: sessionData.launchUrl,
        gameData: gameConfig
      };

    } catch (error) {
      console.error(`Failed to launch game ${gameId} on ${providerName}:`, error);
      throw new GameLaunchError(`Game launch failed: ${error.message}`);
    }
  }

  async getGameConfig(gameId, providerName) {
    const provider = this.providers.get(providerName);

    // Check cache first
    const cacheKey = `game_config:${providerName}:${gameId}`;
    let config = await this.cache.get(cacheKey);

    if (!config) {
      // Fetch from provider API
      config = await provider.client.getGameDetails(gameId);

      // Cache for 1 hour
      await this.cache.set(cacheKey, config, 3600);
    }

    return config;
  }

  setupGameEvents(sessionId, provider) {
    const eventHandler = (event) => {
      this.handleGameEvent(sessionId, event);
    };

    provider.client.on('gameEvent', eventHandler);
    this.eventHandlers.set(sessionId, eventHandler);
  }

  async handleGameEvent(sessionId, event) {
    const session = this.activeSessions.get(sessionId);
    if (!session) return;

    // Update session activity
    session.lastActivity = Date.now();

    // Process event based on type
    switch (event.type) {
      case 'bet':
        await this.processBet(session, event.data);
        break;
      case 'win':
        await this.processWin(session, event.data);
        break;
      case 'gameEnd':
        await this.endSession(sessionId);
        break;
      default:
        console.log(`Unhandled game event: ${event.type}`);
    }

    // Emit to WebSocket clients
    this.websocketService.emitToUser(session.userId, 'gameEvent', {
      sessionId,
      event: event.type,
      data: event.data
    });
  }

  async processBet(session, betData) {
    // Validate bet limits
    await this.validateBetLimits(session.userId, betData.amount);

    // Update wallet
    await this.walletService.debit(session.userId, betData.amount, {
      type: 'bet',
      gameId: session.gameId,
      provider: session.providerName,
      reference: betData.roundId
    });

    // Log transaction
    await this.auditService.logTransaction({
      userId: session.userId,
      type: 'bet',
      amount: betData.amount,
      gameId: session.gameId,
      provider: session.providerName
    });
  }

  async processWin(session, winData) {
    // Credit winnings
    await this.walletService.credit(session.userId, winData.amount, {
      type: 'win',
      gameId: session.gameId,
      provider: session.providerName,
      reference: winData.roundId
    });

    // Log transaction
    await this.auditService.logTransaction({
      userId: session.userId,
      type: 'win',
      amount: winData.amount,
      gameId: session.gameId,
      provider: session.providerName
    });
  }

  async endSession(sessionId) {
    const session = this.activeSessions.get(sessionId);
    if (!session) return;

    // Clean up event handlers
    const eventHandler = this.eventHandlers.get(sessionId);
    if (eventHandler) {
      const provider = this.providers.get(session.providerName);
      provider.client.off('gameEvent', eventHandler);
      this.eventHandlers.delete(sessionId);
    }

    // Calculate session duration
    const duration = Date.now() - session.startTime;

    // Log session end
    await this.analyticsService.logGameSession({
      userId: session.userId,
      gameId: session.gameId,
      provider: session.providerName,
      duration,
      endTime: Date.now()
    });

    // Remove session
    this.activeSessions.delete(sessionId);
  }

  async validateBetLimits(userId, amount) {
    const userLimits = await this.userService.getBetLimits(userId);
    const dailyTotal = await this.transactionService.getDailyBetTotal(userId);

    if (amount > userLimits.maxBet) {
      throw new BetLimitError('Bet exceeds maximum allowed amount');
    }

    if (dailyTotal + amount > userLimits.dailyLimit) {
      throw new BetLimitError('Daily bet limit would be exceeded');
    }
  }

  createProviderClient(config) {
    // Factory method - implement per provider
    throw new Error('createProviderClient must be implemented by subclass');
  }
}

module.exports = { GameIntegrationManager };
