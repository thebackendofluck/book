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
 * Rendering abstraction. MVP uses HTML Canvas (Chromium GPU-accelerates it).
 * The interface is kept narrow so a native DirectX/Vulkan/Metal backend can
 * later replace it without touching callers.
 *
 * Asset bundle lives in data/assets/ (CC0). Every file is SHA-256-checked
 * against data/assets/manifest.json at startup so a tampered on-disk card
 * image is detected before render.
 */

import { createHash } from "crypto";
import { readFileSync, existsSync } from "fs";
import * as path from "path";

export type Suit = "S" | "H" | "D" | "C";
export interface Card {
  rank: string; // "A","2".."K"
  suit: Suit;
}

export interface Seat {
  name: string;
  stack: number;
  cards: Card[];
  isActive: boolean;
}

export interface TableState {
  community: Card[];
  pot: number;
  seats: Seat[];
}

export interface Renderer {
  renderCard(ctx: CanvasRenderingContext2D, card: Card, x: number, y: number): void;
  renderTable(ctx: CanvasRenderingContext2D, state: TableState): void;
}

export interface AssetIntegrityResult {
  ok: boolean;
  missing: string[];
  mismatched: string[];
}

interface AssetManifest {
  version: number;
  license: string;
  generator: string;
  files: Record<string, string>;
}

const SUIT_DIR: Record<Suit, string> = {
  S: "spades",
  H: "hearts",
  D: "diamonds",
  C: "clubs",
};

const RANK_DIR: Record<string, string> = {
  A: "ace",
  K: "king",
  Q: "queen",
  J: "jack",
  "10": "10",
  T: "10",
  "9": "9", "8": "8", "7": "7", "6": "6", "5": "5", "4": "4", "3": "3", "2": "2",
};

export function cardAssetPath(assetsRoot: string, card: Card): string {
  const rank = RANK_DIR[card.rank.toUpperCase()];
  const suit = SUIT_DIR[card.suit];
  if (!rank || !suit) throw new Error(`unknown card: ${card.rank}${card.suit}`);
  return path.join(assetsRoot, "cards", `${rank}_of_${suit}.svg`);
}

/**
 * Verify every asset listed in manifest.json matches its recorded SHA-256.
 * Returns a structured result; callers decide whether a mismatch is fatal.
 */
export function verifyAssetIntegrity(assetsRoot: string): AssetIntegrityResult {
  const manifestPath = path.join(assetsRoot, "manifest.json");
  const missing: string[] = [];
  const mismatched: string[] = [];
  if (!existsSync(manifestPath)) {
    return { ok: false, missing: [manifestPath], mismatched };
  }
  const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as AssetManifest;
  for (const [rel, expected] of Object.entries(manifest.files)) {
    const abs = path.join(assetsRoot, rel);
    if (!existsSync(abs)) {
      missing.push(rel);
      continue;
    }
    const actual = createHash("sha256").update(readFileSync(abs)).digest("hex");
    if (actual !== expected) mismatched.push(rel);
  }
  return { ok: missing.length === 0 && mismatched.length === 0, missing, mismatched };
}

/**
 * Tiny in-memory cache for decoded SVG strings (keyed by relative path).
 * The renderer can rasterise these onto Canvas via Image + data URL, but
 * since cards are tiny SVGs we just hold the text and let the UI layer
 * build an Image when needed.
 */
const svgCache = new Map<string, string>();

export function loadSvg(assetsRoot: string, rel: string): string {
  const key = path.posix.normalize(rel);
  const cached = svgCache.get(key);
  if (cached) return cached;
  const body = readFileSync(path.join(assetsRoot, rel), "utf-8");
  svgCache.set(key, body);
  return body;
}

export function clearAssetCache(): void {
  svgCache.clear();
}

export const canvasRenderer: Renderer = {
  renderCard(ctx, card, x, y) {
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "#222";
    ctx.fillRect(x, y, 60, 84);
    ctx.strokeRect(x, y, 60, 84);
    ctx.fillStyle = card.suit === "H" || card.suit === "D" ? "#c00" : "#111";
    ctx.font = "bold 20px sans-serif";
    ctx.fillText(`${card.rank}${card.suit}`, x + 6, y + 24);
  },

  renderTable(ctx, state) {
    const { width, height } = ctx.canvas;
    ctx.fillStyle = "#0b5d2e";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#fff";
    ctx.font = "18px sans-serif";
    ctx.fillText(`Pot: ${state.pot}`, 20, 30);

    const cx = width / 2 - (state.community.length * 65) / 2;
    state.community.forEach((c, i) => this.renderCard(ctx, c, cx + i * 65, height / 2 - 42));

    state.seats.forEach((s, i) => {
      const sx = 20 + i * 260;
      const sy = height - 140;
      ctx.fillStyle = s.isActive ? "#ffdd44" : "#ddd";
      ctx.fillRect(sx, sy, 240, 120);
      ctx.fillStyle = "#111";
      ctx.fillText(`${s.name}  $${s.stack}`, sx + 8, sy + 22);
      s.cards.forEach((c, j) => this.renderCard(ctx, c, sx + 8 + j * 65, sy + 30));
    });
  },
};
