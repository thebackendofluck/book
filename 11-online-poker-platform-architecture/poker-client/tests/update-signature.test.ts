// Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import { describe, it, expect, beforeAll } from "vitest";
import { generateKeyPairSync, sign, KeyObject } from "crypto";
import { writeFileSync, mkdtempSync, readFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { execFileSync } from "child_process";
import { verifyUpdateSignature } from "../lib/security/pinning";

function pem(key: KeyObject, type: "public" | "private"): string {
  return key.export({
    type: type === "public" ? "spki" : "pkcs8",
    format: "pem",
  }) as string;
}

describe("verifyUpdateSignature (ed25519)", () => {
  let privPem: string;
  let pubPem: string;

  beforeAll(() => {
    const { publicKey, privateKey } = generateKeyPairSync("ed25519");
    privPem = pem(privateKey, "private");
    pubPem = pem(publicKey, "public");
  });

  it("accepts a valid signature", () => {
    const manifest = Buffer.from(JSON.stringify({ v: "0.1.0", sha: "abc" }));
    const { createPrivateKey } = require("crypto");
    const sig = sign(null, manifest, createPrivateKey(privPem)).toString("hex");
    expect(verifyUpdateSignature(manifest, sig, pubPem)).toBe(true);
  });

  it("rejects a tampered manifest", () => {
    const manifest = Buffer.from("original-payload");
    const { createPrivateKey } = require("crypto");
    const sig = sign(null, manifest, createPrivateKey(privPem)).toString("hex");
    const tampered = Buffer.from("tampered-payload");
    expect(verifyUpdateSignature(tampered, sig, pubPem)).toBe(false);
  });

  it("rejects a wrong/garbage signature", () => {
    const manifest = Buffer.from("payload");
    const bogus = "00".repeat(64);
    expect(verifyUpdateSignature(manifest, bogus, pubPem)).toBe(false);
  });

  it("rejects empty manifest", () => {
    expect(verifyUpdateSignature(Buffer.alloc(0), "ab", pubPem)).toBe(false);
  });

  it("rejects non-hex signatures", () => {
    expect(verifyUpdateSignature(Buffer.from("x"), "not-hex!", pubPem)).toBe(
      false,
    );
  });

  it("rejects signatures of the wrong length", () => {
    // 32 bytes of hex instead of the required 64.
    const shortSig = "ab".repeat(32);
    expect(verifyUpdateSignature(Buffer.from("x"), shortSig, pubPem)).toBe(
      false,
    );
  });

  it("build/sign-release.js produces verifiable signatures", () => {
    const dir = mkdtempSync(join(tmpdir(), "signrel-"));
    const manifestPath = join(dir, "manifest.json");
    const keyPath = join(dir, "priv.pem");
    const sigPath = join(dir, "manifest.json.sig");
    const body = Buffer.from(JSON.stringify({ v: "1.2.3" }));
    writeFileSync(manifestPath, body);
    writeFileSync(keyPath, privPem);

    const cli = join(__dirname, "..", "build", "sign-release.js");
    execFileSync(process.execPath, [cli, manifestPath, keyPath, sigPath], {
      stdio: "pipe",
    });
    const sig = readFileSync(sigPath, "utf-8").trim();
    expect(verifyUpdateSignature(body, sig, pubPem)).toBe(true);
  });
});
