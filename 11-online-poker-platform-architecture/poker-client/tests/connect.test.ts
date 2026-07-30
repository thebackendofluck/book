// Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import { describe, it, expect } from "vitest";
import { WebSocketServer } from "ws";
import { connect } from "../lib/network/connect";

describe("connect", () => {
  it("connects to a plain ws server and echoes", async () => {
    const wss = new WebSocketServer({ port: 0 });
    const port = (wss.address() as { port: number }).port;

    wss.on("connection", (ws) => {
      ws.on("message", (m) => ws.send(m.toString()));
    });

    const conn = await connect(`ws://127.0.0.1:${port}`, { timeoutMs: 3000 });
    const echoed = await new Promise<string>((resolve) => {
      conn.socket.once("message", (d) => resolve(d.toString()));
      conn.send({ hello: "world" });
    });
    expect(JSON.parse(echoed).hello).toBe("world");
    conn.close();
    wss.close();
  });

  it("times out on bad host", async () => {
    await expect(
      connect("ws://127.0.0.1:1", { timeoutMs: 500 }),
    ).rejects.toThrow();
  });
});
