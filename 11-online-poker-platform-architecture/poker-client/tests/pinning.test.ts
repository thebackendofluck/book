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
import { createHash } from "crypto";
import {
  pinCertificate,
  verifyCertificate,
  checkIntegrity,
  verifyUpdateSignature,
} from "../lib/security/pinning";
import { writeFileSync, mkdtempSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

describe("pinning", () => {
  it("rejects invalid sha256 pin", () => {
    expect(() => pinCertificate("example.com", "notahex")).toThrow();
  });

  it("verifies matching cert", () => {
    const der = Buffer.from("fake-der-bytes");
    const sha = createHash("sha256").update(der).digest("hex");
    pinCertificate("pin.test", sha);
    expect(verifyCertificate("pin.test", der)).toBe(true);
  });

  it("rejects mismatched cert", () => {
    const der = Buffer.from("good");
    const sha = createHash("sha256").update(der).digest("hex");
    pinCertificate("mm.test", sha);
    expect(verifyCertificate("mm.test", Buffer.from("bad"))).toBe(false);
  });

  it("checkIntegrity passes for matching file", () => {
    const dir = mkdtempSync(join(tmpdir(), "pint-"));
    const p = join(dir, "f.bin");
    writeFileSync(p, "hello");
    const sha = createHash("sha256").update("hello").digest("hex");
    expect(checkIntegrity(p, sha)).toBe(true);
  });

  it("verifyUpdateSignature rejects empty manifest", () => {
    expect(verifyUpdateSignature(Buffer.alloc(0), "abcd", "")).toBe(false);
  });
});
