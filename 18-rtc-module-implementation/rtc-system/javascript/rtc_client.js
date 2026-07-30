// Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// rtc-sdk-nodejs/index.js
const WebSocket = require('ws');
const crypto = require('crypto');

class CasinoRTC {
    constructor(options = {}) {
        this.baseUrl = options.baseUrl || 'https://rtc.casino-platform.com/api/v1';
        this.apiKey = options.apiKey;
        this.secretKey = options.secretKey;
        this.timeout = options.timeout || 5000;
    }

    async getTimestamp(metadata = {}) {
        const url = new URL(`${this.baseUrl}/timestamp`);
        if (Object.keys(metadata).length > 0) {
            url.searchParams.set('metadata', JSON.stringify(metadata));
        }

        const response = await fetch(url, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${this.apiKey}`,
                'Content-Type': 'application/json'
            },
            timeout: this.timeout
        });

        if (!response.ok) {
            throw new Error(`RTC API error: ${response.status}`);
        }

        const data = await response.json();
        return this._verifySignature(data);
    }

    async getBatchTimestamps(count, interval = 0, metadata = {}) {
        const response = await fetch(`${this.baseUrl}/timestamp/batch`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${this.apiKey}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                count,
                interval_ms: interval,
                metadata
            }),
            timeout: this.timeout
        });

        if (!response.ok) {
            throw new Error(`RTC API error: ${response.status}`);
        }

        const data = await response.json();
        return data.timestamps.map(ts => this._verifySignature(ts));
    }

    streamTimestamps(interval = 100, callback) {
        const wsUrl = this.baseUrl.replace('https://', 'wss://').replace('http://', 'ws://');
        const ws = new WebSocket(`${wsUrl}/timestamp/stream?interval=${interval}`, {
            headers: {
                'Authorization': `Bearer ${this.apiKey}`
            }
        });

        ws.on('message', (data) => {
            try {
                const timestamp = JSON.parse(data.toString());
                const verified = this._verifySignature(timestamp);
                callback(null, verified);
            } catch (error) {
                callback(error);
            }
        });

        ws.on('error', (error) => {
            callback(error);
        });

        return {
            close: () => ws.close(),
            isConnected: () => ws.readyState === WebSocket.OPEN
        };
    }

    async getSyncStatus() {
        const response = await fetch(`${this.baseUrl}/sync/status`, {
            headers: {
                'Authorization': `Bearer ${this.apiKey}`
            },
            timeout: this.timeout
        });

        if (!response.ok) {
            throw new Error(`RTC API error: ${response.status}`);
        }

        return response.json();
    }

    _verifySignature(timestamp) {
        const data = `${timestamp.unix}:${timestamp.nano}:${timestamp.source}`;
        const expectedSignature = crypto
            .createHmac('sha256', this.secretKey)
            .update(data)
            .digest('hex');

        if (timestamp.signature !== expectedSignature) {
            throw new Error('Invalid timestamp signature');
        }

        return timestamp;
    }
}

module.exports = CasinoRTC;
