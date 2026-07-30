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
 * Online Poker WebSocket Protocol Definitions
 * Chapter 4 - Online Poker Platform Architecture
 *
 * Defines message format, opcodes, error codes, and serialization
 * for the poker WebSocket protocol layer.
 */

'use strict';

// ─── Protocol Version ────────────────────────────────────────────────
const PROTOCOL_VERSION = '2.1.0';

// ─── Message Opcodes ─────────────────────────────────────────────────
const OpCode = Object.freeze({
  // Connection lifecycle (0x01–0x0F)
  AUTH_REQUEST:       0x01,
  AUTH_RESPONSE:      0x02,
  AUTH_CHALLENGE:     0x03,  // MFA / device verification
  HEARTBEAT_PING:    0x04,
  HEARTBEAT_PONG:    0x05,
  RECONNECT_REQUEST: 0x06,
  RECONNECT_STATE:   0x07,
  DISCONNECT:        0x08,

  // Lobby operations (0x10–0x1F)
  LOBBY_LIST:        0x10,
  LOBBY_UPDATE:      0x11,
  TABLE_JOIN:        0x12,
  TABLE_LEAVE:       0x13,
  TABLE_INFO:        0x14,
  WAITLIST_JOIN:     0x15,
  WAITLIST_UPDATE:   0x16,

  // Game actions (0x20–0x3F)
  HAND_START:        0x20,
  DEAL_CARDS:        0x21,
  ACTION_REQUEST:    0x22,  // Server asks player to act
  ACTION_RESPONSE:   0x23,  // Player submits action
  COMMUNITY_CARDS:   0x24,
  POT_UPDATE:        0x25,
  SHOWDOWN:          0x26,
  HAND_RESULT:       0x27,
  PLAYER_TIMEOUT:    0x28,
  SIDE_POT_CREATED:  0x29,
  ALL_IN:            0x2A,
  SIT_OUT:           0x2B,
  SIT_IN:            0x2C,

  // State sync (0x40–0x4F)
  STATE_SNAPSHOT:    0x40,
  STATE_DELTA:       0x41,
  STATE_HASH_CHECK:  0x42,
  STATE_RESYNC:      0x43,

  // Chat / social (0x50–0x5F)
  CHAT_MESSAGE:      0x50,
  EMOJI_REACTION:    0x51,

  // Tournament (0x60–0x6F)
  TOURNAMENT_UPDATE: 0x60,
  TABLE_BREAK:       0x61,
  BLIND_INCREASE:    0x62,
  FINAL_TABLE:       0x63,

  // Admin (0xF0–0xFF)
  SERVER_MESSAGE:    0xF0,
  MAINTENANCE:       0xF1,
  FORCE_DISCONNECT:  0xF2,
  ERROR:             0xFF,
});

// ─── Error Codes ─────────────────────────────────────────────────────
const ErrorCode = Object.freeze({
  // Auth errors (1000–1099)
  AUTH_FAILED:              1000,
  AUTH_TOKEN_EXPIRED:       1001,
  AUTH_DEVICE_BLOCKED:      1002,
  AUTH_GEO_RESTRICTED:      1003,
  AUTH_SELF_EXCLUDED:       1004,
  AUTH_ACCOUNT_LOCKED:      1005,

  // Game errors (2000–2099)
  INVALID_ACTION:           2000,
  NOT_YOUR_TURN:            2001,
  INSUFFICIENT_CHIPS:       2002,
  BET_BELOW_MINIMUM:        2003,
  BET_ABOVE_MAXIMUM:        2004,
  TABLE_FULL:               2005,
  HAND_IN_PROGRESS:         2006,
  ALREADY_SEATED:           2007,
  INVALID_BUY_IN:           2008,

  // State errors (3000–3099)
  STATE_MISMATCH:           3000,
  STATE_VERSION_CONFLICT:   3001,
  SNAPSHOT_CORRUPTED:       3002,

  // Rate limit (4000–4099)
  RATE_LIMITED:             4000,
  TOO_MANY_CONNECTIONS:     4001,

  // Server errors (5000–5099)
  INTERNAL_ERROR:           5000,
  TABLE_SHUTTING_DOWN:      5001,
  SERVER_OVERLOADED:        5002,
});

// ─── Action Types ────────────────────────────────────────────────────
const ActionType = Object.freeze({
  FOLD:    'fold',
  CHECK:   'check',
  CALL:    'call',
  BET:     'bet',
  RAISE:   'raise',
  ALL_IN:  'all_in',
});

// ─── Game Phases ─────────────────────────────────────────────────────
const GamePhase = Object.freeze({
  WAITING:    'waiting',
  PREFLOP:    'preflop',
  FLOP:       'flop',
  TURN:       'turn',
  RIVER:      'river',
  SHOWDOWN:   'showdown',
  HAND_OVER:  'hand_over',
});

// ─── Message Envelope ────────────────────────────────────────────────
/**
 * All messages follow this envelope format:
 *
 * {
 *   v:    string,     // protocol version
 *   op:   number,     // opcode
 *   seq:  number,     // sequence number (monotonically increasing per connection)
 *   ts:   number,     // server timestamp (ms since epoch)
 *   rid:  string?,    // request ID for request/response correlation
 *   d:    object,     // payload data
 *   hash: string?,    // SHA-256 of payload for state sync messages
 * }
 */

class ProtocolMessage {
  static _seqCounter = 0;

  /**
   * Create a new protocol message.
   * @param {number} opcode - Message opcode from OpCode enum
   * @param {object} data - Payload data
   * @param {object} [options] - Optional fields
   * @param {string} [options.requestId] - Correlation ID
   * @param {string} [options.hash] - Payload hash
   * @returns {object} Serializable message envelope
   */
  static create(opcode, data, options = {}) {
    const msg = {
      v:   PROTOCOL_VERSION,
      op:  opcode,
      seq: ++ProtocolMessage._seqCounter,
      ts:  Date.now(),
      d:   data,
    };

    if (options.requestId) msg.rid = options.requestId;
    if (options.hash)      msg.hash = options.hash;

    return msg;
  }

  /**
   * Serialize message to binary (MessagePack-style length-prefixed JSON).
   * In production, swap JSON for MessagePack or FlatBuffers.
   * @param {object} msg - Message envelope
   * @returns {Buffer} Length-prefixed binary frame
   */
  static serialize(msg) {
    const json = JSON.stringify(msg);
    const payload = Buffer.from(json, 'utf8');
    const frame = Buffer.alloc(4 + payload.length);
    frame.writeUInt32BE(payload.length, 0);
    payload.copy(frame, 4);
    return frame;
  }

  /**
   * Deserialize binary frame to message object.
   * @param {Buffer} frame - Length-prefixed binary frame
   * @returns {object} Parsed message envelope
   * @throws {Error} On invalid frame or parse failure
   */
  static deserialize(frame) {
    if (!Buffer.isBuffer(frame) || frame.length < 4) {
      throw new Error('Invalid frame: too short');
    }
    const length = frame.readUInt32BE(0);
    if (frame.length < 4 + length) {
      throw new Error(`Incomplete frame: expected ${length} bytes, got ${frame.length - 4}`);
    }
    const json = frame.slice(4, 4 + length).toString('utf8');
    try {
      return JSON.parse(json);
    } catch (err) {
      throw new Error(`Frame parse error: ${err.message}`);
    }
  }

  /**
   * Validate a message envelope has required fields.
   * @param {object} msg - Message to validate
   * @returns {{ valid: boolean, errors: string[] }}
   */
  static validate(msg) {
    const errors = [];
    if (!msg || typeof msg !== 'object') {
      return { valid: false, errors: ['Message is not an object'] };
    }
    if (typeof msg.v !== 'string')   errors.push('Missing or invalid version (v)');
    if (typeof msg.op !== 'number')  errors.push('Missing or invalid opcode (op)');
    if (typeof msg.seq !== 'number') errors.push('Missing or invalid sequence (seq)');
    if (typeof msg.ts !== 'number')  errors.push('Missing or invalid timestamp (ts)');
    if (!msg.d || typeof msg.d !== 'object') errors.push('Missing or invalid data (d)');

    return { valid: errors.length === 0, errors };
  }

  /** Reset sequence counter (for testing). */
  static resetSequence() {
    ProtocolMessage._seqCounter = 0;
  }
}

// ─── Message Builders ────────────────────────────────────────────────

const MessageBuilder = {
  authRequest(token, deviceFingerprint, clientVersion) {
    return ProtocolMessage.create(OpCode.AUTH_REQUEST, {
      token,
      device_fingerprint: deviceFingerprint,
      client_version: clientVersion,
      capabilities: ['binary', 'compression', 'delta_sync'],
    });
  },

  authResponse(success, playerId, sessionId, serverTime, errorCode = null) {
    return ProtocolMessage.create(OpCode.AUTH_RESPONSE, {
      success,
      player_id: playerId,
      session_id: sessionId,
      server_time: serverTime,
      heartbeat_interval_ms: 15000,
      reconnect_window_s: 120,
      ...(errorCode && { error_code: errorCode }),
    });
  },

  heartbeatPing(sessionId) {
    return ProtocolMessage.create(OpCode.HEARTBEAT_PING, {
      session_id: sessionId,
    });
  },

  heartbeatPong(sessionId, serverLoad) {
    return ProtocolMessage.create(OpCode.HEARTBEAT_PONG, {
      session_id: sessionId,
      server_load: serverLoad,  // 0.0–1.0
    });
  },

  reconnectRequest(sessionId, lastSeq, stateVersion) {
    return ProtocolMessage.create(OpCode.RECONNECT_REQUEST, {
      session_id: sessionId,
      last_received_seq: lastSeq,
      state_version: stateVersion,
    });
  },

  reconnectState(tableState, missedMessages, stateVersion) {
    return ProtocolMessage.create(OpCode.RECONNECT_STATE, {
      table_state: tableState,
      missed_messages: missedMessages,
      state_version: stateVersion,
    });
  },

  actionRequest(tableId, handId, playerId, validActions, timeoutMs, potSize, currentBet) {
    return ProtocolMessage.create(OpCode.ACTION_REQUEST, {
      table_id: tableId,
      hand_id: handId,
      player_id: playerId,
      valid_actions: validActions,  // e.g. [{type:'call',amount:200},{type:'raise',min:400,max:10000}]
      timeout_ms: timeoutMs,
      pot_size: potSize,
      current_bet: currentBet,
    });
  },

  actionResponse(tableId, handId, playerId, action, amount = 0) {
    return ProtocolMessage.create(OpCode.ACTION_RESPONSE, {
      table_id: tableId,
      hand_id: handId,
      player_id: playerId,
      action,
      amount,
      client_timestamp: Date.now(),
    });
  },

  dealCards(tableId, handId, cards, phase) {
    return ProtocolMessage.create(OpCode.DEAL_CARDS, {
      table_id: tableId,
      hand_id: handId,
      cards,      // encrypted per-player
      phase,
    });
  },

  handResult(tableId, handId, winners, pots, showdownCards) {
    return ProtocolMessage.create(OpCode.HAND_RESULT, {
      table_id: tableId,
      hand_id: handId,
      winners,        // [{player_id, amount, hand_rank, hand_description}]
      pots,           // [{amount, eligible_players, winner}]
      showdown_cards: showdownCards,
      next_hand_delay_ms: 3000,
    });
  },

  stateSnapshot(tableId, state, version, hash) {
    return ProtocolMessage.create(OpCode.STATE_SNAPSHOT, state, {
      hash,
      requestId: `snap-${tableId}-${version}`,
    });
  },

  error(errorCode, message, details = {}) {
    return ProtocolMessage.create(OpCode.ERROR, {
      error_code: errorCode,
      message,
      details,
    });
  },
};

// ─── Exports ─────────────────────────────────────────────────────────
module.exports = {
  PROTOCOL_VERSION,
  OpCode,
  ErrorCode,
  ActionType,
  GamePhase,
  ProtocolMessage,
  MessageBuilder,
};
