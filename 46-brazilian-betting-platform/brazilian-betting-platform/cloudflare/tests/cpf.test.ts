// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Unit tests for CPF validation utility.
 *
 * Tests cover:
 *  - Valid CPFs (formatted and bare)
 *  - All-identical-digit sequences (e.g., 111.111.111-11)
 *  - Incorrect check digits
 *  - Wrong length
 *  - Non-numeric input
 *  - Formatting and masking helpers
 */

import { describe, it, expect } from 'vitest';
import {
  validateCPF,
  normalizeCPF,
  formatCPF,
  maskCPF,
  assertValidCPF,
} from '../src/utils/cpf.js';

// ── validateCPF ───────────────────────────────────────────────────────────────

describe('validateCPF', () => {
  it('accepts a known-valid CPF (bare)', () => {
    expect(validateCPF('52998224725')).toBe(true);
  });

  it('accepts a known-valid CPF (formatted)', () => {
    expect(validateCPF('529.982.247-25')).toBe(true);
  });

  it('accepts another valid CPF', () => {
    expect(validateCPF('11144477735')).toBe(true);
  });

  it('rejects all-zeros', () => {
    expect(validateCPF('00000000000')).toBe(false);
  });

  it('rejects all-ones', () => {
    expect(validateCPF('11111111111')).toBe(false);
  });

  it('rejects all-nines', () => {
    expect(validateCPF('99999999999')).toBe(false);
  });

  it('rejects CPF with wrong first check digit', () => {
    // Valid CPF 529.982.247-25 with first check digit changed to 3
    expect(validateCPF('52998224735')).toBe(false);
  });

  it('rejects CPF with wrong second check digit', () => {
    // Valid CPF 529.982.247-25 with second check digit changed to 6
    expect(validateCPF('52998224726')).toBe(false);
  });

  it('rejects CPF shorter than 11 digits', () => {
    expect(validateCPF('1234567890')).toBe(false);
  });

  it('rejects CPF longer than 11 digits', () => {
    expect(validateCPF('123456789012')).toBe(false);
  });

  it('rejects empty string', () => {
    expect(validateCPF('')).toBe(false);
  });

  it('rejects alphabetic string', () => {
    expect(validateCPF('abcdefghijk')).toBe(false);
  });

  it('ignores formatting punctuation', () => {
    // Same CPF with dots and dash
    expect(validateCPF('111.444.777-35')).toBe(true);
  });
});

// ── normalizeCPF ──────────────────────────────────────────────────────────────

describe('normalizeCPF', () => {
  it('strips dots and dash', () => {
    expect(normalizeCPF('529.982.247-25')).toBe('52998224725');
  });

  it('strips spaces', () => {
    expect(normalizeCPF('529 982 247 25')).toBe('52998224725');
  });

  it('passes through bare CPF unchanged', () => {
    expect(normalizeCPF('52998224725')).toBe('52998224725');
  });
});

// ── formatCPF ─────────────────────────────────────────────────────────────────

describe('formatCPF', () => {
  it('formats a bare CPF', () => {
    expect(formatCPF('52998224725')).toBe('529.982.247-25');
  });

  it('formats an already-formatted CPF (normalises first)', () => {
    expect(formatCPF('529.982.247-25')).toBe('529.982.247-25');
  });
});

// ── maskCPF ───────────────────────────────────────────────────────────────────

describe('maskCPF', () => {
  it('masks the first 6 digits', () => {
    const masked = maskCPF('52998224725');
    expect(masked).toBe('***.***247-25');
  });

  it('returns generic mask for invalid-length input', () => {
    expect(maskCPF('123')).toBe('***.***.***-**');
  });
});

// ── assertValidCPF ────────────────────────────────────────────────────────────

describe('assertValidCPF', () => {
  it('returns normalised CPF for valid input', () => {
    expect(assertValidCPF('529.982.247-25')).toBe('52998224725');
  });

  it('throws for an invalid CPF', () => {
    expect(() => assertValidCPF('00000000000')).toThrow(/CPF inválido/);
  });

  it('throws for a CPF with wrong check digits', () => {
    expect(() => assertValidCPF('52998224726')).toThrow(/CPF inválido/);
  });
});
