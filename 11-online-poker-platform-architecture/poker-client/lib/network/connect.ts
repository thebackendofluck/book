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
 * WebSocket transport with TLS 1.3 enforcement and certificate pinning.
 *
 * Server-side counterpart: chapter-11/poker/game_server.py (NetworkManager).
 */

import WebSocket from "ws";
import { URL } from "url";
import { verifyCertificate, getPin } from "../security/pinning";

export interface ConnectOptions {
  pinnedSha256?: string;
  minTlsVersion?: "TLSv1.3";
  timeoutMs?: number;
  headers?: Record<string, string>;
}

export interface Connection {
  socket: WebSocket;
  send(obj: unknown): void;
  close(): void;
}

export function connect(url: string, opts: ConnectOptions = {}): Promise<Connection> {
  const parsed = new URL(url);
  const isTls = parsed.protocol === "wss:";
  const minVersion = opts.minTlsVersion ?? "TLSv1.3";

  const wsOptions: Record<string, unknown> = {
    headers: opts.headers,
    handshakeTimeout: opts.timeoutMs ?? 15000,
    rejectUnauthorized: isTls,
  };
  if (isTls) {
    wsOptions.minVersion = minVersion;
    wsOptions.checkServerIdentity = (host: string, cert: { raw?: Buffer }): Error | undefined => {
      const pin = opts.pinnedSha256 ?? getPin(host);
      if (!pin) return undefined;
      const der = cert.raw;
      if (!der || !verifyCertificate(host, der)) {
        return new Error(`certificate pin mismatch for ${host}`);
      }
      return undefined;
    };
  }
  const ws = new WebSocket(url, wsOptions as never);

  return new Promise((resolve, reject) => {
    const to = setTimeout(() => {
      ws.terminate();
      reject(new Error("connect timeout"));
    }, opts.timeoutMs ?? 15000);

    ws.once("open", () => {
      clearTimeout(to);
      resolve({
        socket: ws,
        send: (o) => ws.send(JSON.stringify(o)),
        close: () => ws.close(),
      });
    });
    ws.once("error", (err) => {
      clearTimeout(to);
      reject(err);
    });
  });
}
