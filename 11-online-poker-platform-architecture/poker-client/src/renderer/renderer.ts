// Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Renderer script. Kept standalone (no imports) so a bundler isn't required;
// the graphics primitives duplicate the types in ../../lib/graphics/render.ts
// which remains the source of truth for tests and future native backends.

interface Card { rank: string; suit: "S" | "H" | "D" | "C"; }
interface Seat { name: string; stack: number; cards: Card[]; isActive: boolean; }
interface TableState { community: Card[]; pot: number; seats: Seat[]; }

interface PokerApi {
  getConfig(): Promise<{ server: { url: string } }>;
  connect(url: string): Promise<{ ok: boolean; error?: string }>;
}
const pokerApi: PokerApi = (window as unknown as { pokerApi: PokerApi }).pokerApi;

function renderCard(ctx: CanvasRenderingContext2D, card: Card, x: number, y: number): void {
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#222";
  ctx.fillRect(x, y, 60, 84);
  ctx.strokeRect(x, y, 60, 84);
  ctx.fillStyle = card.suit === "H" || card.suit === "D" ? "#c00" : "#111";
  ctx.font = "bold 20px sans-serif";
  ctx.fillText(`${card.rank}${card.suit}`, x + 6, y + 24);
}

function renderTable(ctx: CanvasRenderingContext2D, s: TableState): void {
  const { width, height } = ctx.canvas;
  ctx.fillStyle = "#0b5d2e";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#fff";
  ctx.font = "18px sans-serif";
  ctx.fillText(`Pot: ${s.pot}`, 20, 30);
  const cx = width / 2 - (s.community.length * 65) / 2;
  s.community.forEach((c, i) => renderCard(ctx, c, cx + i * 65, height / 2 - 42));
  s.seats.forEach((seat, i) => {
    const sx = 20 + i * 260;
    const sy = height - 140;
    ctx.fillStyle = seat.isActive ? "#ffdd44" : "#ddd";
    ctx.fillRect(sx, sy, 240, 120);
    ctx.fillStyle = "#111";
    ctx.fillText(`${seat.name}  $${seat.stack}`, sx + 8, sy + 22);
    seat.cards.forEach((c, j) => renderCard(ctx, c, sx + 8 + j * 65, sy + 30));
  });
}

const state: TableState = {
  community: [
    { rank: "A", suit: "S" },
    { rank: "K", suit: "H" },
    { rank: "7", suit: "D" },
  ],
  pot: 150,
  seats: [
    { name: "You", stack: 1000, isActive: true,
      cards: [{ rank: "Q", suit: "S" }, { rank: "J", suit: "S" }] },
    { name: "Opponent", stack: 1000, isActive: false,
      cards: [{ rank: "?", suit: "C" }, { rank: "?", suit: "C" }] },
  ],
};

function draw(): void {
  const canvas = document.getElementById("table") as HTMLCanvasElement;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  renderTable(ctx, state);
}

async function init(): Promise<void> {
  draw();
  const status = document.getElementById("status")!;
  try {
    const cfg = await pokerApi.getConfig();
    status.textContent = `configured for ${cfg.server.url}`;
    const res = await pokerApi.connect(cfg.server.url);
    status.textContent = res.ok ? `connected to ${cfg.server.url}` : `offline (${res.error})`;
  } catch (e) {
    status.textContent = `init error: ${(e as Error).message}`;
  }
  document.querySelectorAll<HTMLButtonElement>("#controls button").forEach((btn) => {
    btn.addEventListener("click", () => {
      status.textContent = `action: ${btn.dataset.action}`;
    });
  });
}

window.addEventListener("DOMContentLoaded", init);
