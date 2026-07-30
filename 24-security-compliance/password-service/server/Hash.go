// Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

package server

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"strings"

	"golang.org/x/crypto/argon2"
	"golang.org/x/crypto/pbkdf2"
)

// Argon2id parameters (OWASP 2024 recommended)
// Why Argon2id: each hash attempt requires 64MB RAM, limiting GPU parallelism.
// NVIDIA A100 (24GB VRAM): ~375 parallel Argon2id vs millions of PBKDF2.
var (
	argon2Memory      uint32 = 65536 // 64 MiB
	argon2Iterations  uint32 = 3
	argon2Parallelism uint8  = 4
	argon2KeyLen      uint32 = 32
	argon2SaltLen     int    = 16
)

// Legacy PBKDF2 parameters (retained for migration)
var hashBytes = 32
var iterations = 100100

// HashArgon2id hashes a password with Argon2id (GPU-resistant, memory-hard).
// Returns a PHC-format string: $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>
func HashArgon2id(password string) (string, error) {
	salt := make([]byte, argon2SaltLen)
	if _, err := rand.Read(salt); err != nil {
		return "", fmt.Errorf("failed to generate salt: %w", err)
	}

	hash := argon2.IDKey(
		[]byte(password),
		salt,
		argon2Iterations,
		argon2Memory,
		argon2Parallelism,
		argon2KeyLen,
	)

	saltB64 := base64.RawStdEncoding.EncodeToString(salt)
	hashB64 := base64.RawStdEncoding.EncodeToString(hash)

	return fmt.Sprintf(
		"$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s",
		argon2.Version, argon2Memory, argon2Iterations, argon2Parallelism,
		saltB64, hashB64,
	), nil
}

// VerifyArgon2id verifies a password against an Argon2id PHC-format hash.
// Uses constant-time comparison to prevent timing attacks.
func VerifyArgon2id(password, encodedHash string) (bool, error) {
	parts := strings.Split(encodedHash, "$")
	if len(parts) != 6 || parts[1] != "argon2id" {
		return false, fmt.Errorf("invalid argon2id hash format")
	}

	var version int
	var memory, time uint32
	var parallelism uint8
	_, err := fmt.Sscanf(parts[2], "v=%d", &version)
	if err != nil {
		return false, err
	}
	_, err = fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &memory, &time, &parallelism)
	if err != nil {
		return false, err
	}

	salt, err := base64.RawStdEncoding.DecodeString(parts[4])
	if err != nil {
		return false, err
	}
	storedHash, err := base64.RawStdEncoding.DecodeString(parts[5])
	if err != nil {
		return false, err
	}

	computedHash := argon2.IDKey([]byte(password), salt, time, memory, parallelism, uint32(len(storedHash)))

	return subtle.ConstantTimeCompare(storedHash, computedHash) == 1, nil
}

// Hash is the legacy PBKDF2-SHA256 function (DEPRECATED — use HashArgon2id).
// Retained for backward compatibility during migration from PBKDF2 to Argon2id.
func Hash(password string, salt string) string {
	hashed := pbkdf2.Key([]byte(password), []byte(salt), iterations, hashBytes, sha256.New)
	return strings.ToUpper(hex.EncodeToString(hashed))
}
