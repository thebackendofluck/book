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
 * Chapter 4: Online Poker Platform Architecture
 * WebSocket Client Implementation
 *
 * This module implements the PokerWebSocketClient class for browser-based
 * poker clients. It handles:
 * - Secure WebSocket connection management
 * - Message queuing for connection drops
 * - Automatic reconnection with exponential backoff
 * - Server message routing (TABLE_UPDATE, DEAL_CARDS, ACTION_REQUEST)
 *
 * Reference: Chapter 4 - Network Communication section
 */

class PokerWebSocketClient {
    constructor(serverUrl) {
        this.ws = new WebSocket(serverUrl);
        this.messageQueue = [];
        this.reconnectAttempts = 0;

        this.setupEventHandlers();
    }

    setupEventHandlers() {
        this.ws.onopen = () => {
            console.log('Connected to poker server');
            this.flushMessageQueue();
        };

        this.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            this.handleServerMessage(message);
        };

        this.ws.onclose = () => {
            this.handleDisconnection();
        };
    }

    sendAction(action) {
        const message = {
            type: 'PLAYER_ACTION',
            action: action,
            timestamp: Date.now(),
            sessionId: this.sessionId
        };

        if (this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        } else {
            this.messageQueue.push(message);
        }
    }

    handleServerMessage(message) {
        switch(message.type) {
            case 'TABLE_UPDATE':
                this.updateTableState(message.data);
                break;
            case 'DEAL_CARDS':
                this.showPlayerCards(message.cards);
                break;
            case 'ACTION_REQUEST':
                this.enablePlayerActions(message.validActions);
                break;
            // ... handle other message types
        }
    }
}
