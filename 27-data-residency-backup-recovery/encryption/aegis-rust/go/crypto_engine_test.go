// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

package cryptoengine

import (
	"bytes"
	"crypto/rand"
	"errors"
	"testing"
)

func TestRoundtripAegis128L(t *testing.T) {
	key := make([]byte, 16)
	rand.Read(key)
	e, err := NewAegis128L(key)
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()

	pt := []byte("high-throughput game event")
	aad := []byte("ctx=demo")
	payload, err := e.Encrypt(pt, aad)
	if err != nil {
		t.Fatal(err)
	}
	if payload[0] != AlgAegis128L {
		t.Fatalf("unexpected alg_id: %02x", payload[0])
	}
	out, err := e.Decrypt(payload, aad)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(out, pt) {
		t.Fatalf("roundtrip mismatch")
	}
}

func TestWrongAADRejected(t *testing.T) {
	key := make([]byte, 16)
	rand.Read(key)
	e, _ := NewAegis128L(key)
	defer e.Close()

	payload, _ := e.Encrypt([]byte("balance=5000"), []byte("user=A"))
	_, err := e.Decrypt(payload, []byte("user=B"))
	if !errors.Is(err, ErrAuthFailed) {
		t.Fatalf("expected ErrAuthFailed, got %v", err)
	}
}

func TestAllAlgorithms(t *testing.T) {
	key16 := make([]byte, 16)
	key32 := make([]byte, 32)
	rand.Read(key16)
	rand.Read(key32)

	cases := []struct {
		name string
		eng  func() (*Engine, error)
	}{
		{"aegis128l", func() (*Engine, error) { return NewAegis128L(key16) }},
		{"aegis256", func() (*Engine, error) { return NewAegis256(key32) }},
		{"aes256gcm", func() (*Engine, error) { return NewAes256Gcm(key32) }},
		{"chacha20poly1305", func() (*Engine, error) { return NewChaCha20Poly1305(key32) }},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e, err := c.eng()
			if err != nil {
				t.Fatal(err)
			}
			defer e.Close()
			payload, err := e.Encrypt([]byte("hello"), []byte("ctx"))
			if err != nil {
				t.Fatal(err)
			}
			out, err := e.Decrypt(payload, []byte("ctx"))
			if err != nil {
				t.Fatal(err)
			}
			if string(out) != "hello" {
				t.Fatalf("got %q", out)
			}
		})
	}
}
