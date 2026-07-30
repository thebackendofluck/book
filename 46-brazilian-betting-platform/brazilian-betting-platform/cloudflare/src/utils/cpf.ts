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
 * CPF Validation Utility
 *
 * Implements the official Receita Federal mod-11 algorithm for Brazilian CPF
 * (Cadastro de Pessoas Físicas) numbers.
 *
 * Reference: https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/
 *            legislacao/documentos-e-formularios/cpf
 */

/**
 * Strip all non-digit characters from a CPF string.
 * Accepts formatted ("123.456.789-09") or bare ("12345678909") inputs.
 */
export function normalizeCPF(raw: string): string {
  return raw.replace(/\D/g, '');
}

/**
 * Format a bare CPF string as "NNN.NNN.NNN-DD".
 * Does not validate — use `validateCPF` first.
 */
export function formatCPF(cpf: string): string {
  const d = normalizeCPF(cpf);
  return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}-${d.slice(9)}`;
}

/**
 * Mask a CPF for display: "***.***789-09".
 * The last 5 characters (final 3 digits + separator + 2 check digits) are
 * kept visible, matching the LGPD minimal-disclosure principle.
 */
export function maskCPF(cpf: string): string {
  const d = normalizeCPF(cpf);
  if (d.length !== 11) return '***.***.***-**';
  return `***.***${ d.slice(6, 9) }-${ d.slice(9) }`;
}

/**
 * Validate a CPF number using the official mod-11 double-check-digit algorithm.
 *
 * Algorithm:
 *  1. Reject strings not exactly 11 digits after normalisation.
 *  2. Reject sequences of identical digits (000...0 through 999...9) which are
 *     syntactically valid but semantically invalid by Receita Federal rules.
 *  3. Compute first check digit from digits 0–8 (multiplied by weights 10–2).
 *  4. Compute second check digit from digits 0–9 (multiplied by weights 11–2).
 *
 * @param raw - CPF in any format (with or without punctuation).
 * @returns true if the CPF passes the mod-11 check.
 */
export function validateCPF(raw: string): boolean {
  const cpf = normalizeCPF(raw);

  if (cpf.length !== 11) return false;

  // Reject all-identical-digit sequences
  if (/^(\d)\1{10}$/.test(cpf)) return false;

  // First check digit
  const firstDigit = computeCheckDigit(cpf, 9);
  if (firstDigit !== parseInt(cpf[9], 10)) return false;

  // Second check digit
  const secondDigit = computeCheckDigit(cpf, 10);
  if (secondDigit !== parseInt(cpf[10], 10)) return false;

  return true;
}

/**
 * Compute a single CPF check digit for the first `length` digits.
 *
 * @param cpf    - Normalised 11-digit CPF string.
 * @param length - Number of digits to use (9 for first check, 10 for second).
 */
function computeCheckDigit(cpf: string, length: number): number {
  let sum = 0;
  for (let i = 0; i < length; i++) {
    sum += parseInt(cpf[i], 10) * (length + 1 - i);
  }
  const remainder = sum % 11;
  return remainder < 2 ? 0 : 11 - remainder;
}

/**
 * Assert that a CPF is valid, throwing a descriptive error if not.
 * Convenience wrapper for use in request handlers.
 *
 * @throws {Error} with a user-visible (Portuguese) message.
 */
export function assertValidCPF(raw: string): string {
  const cpf = normalizeCPF(raw);
  if (!validateCPF(cpf)) {
    throw new Error('CPF inválido. Verifique o número informado.');
  }
  return cpf;
}
