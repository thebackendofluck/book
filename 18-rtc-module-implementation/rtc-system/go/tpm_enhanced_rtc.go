// Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// tpm-enhanced-rtc.go
// NOTE: This code requires a physical TPM 2.0 device (/dev/tpm0).
// It uses the go-tpm library for hardware-backed timestamp signing.
// Build with: go build -tags tpm
//go:build tpm

package main

import (
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/google/go-tpm/tpmutil"
)

// TPMRTCService extends RTCService with TPM-backed signing
type TPMRTCService struct {
	tpm        io.ReadWriteCloser
	signingKey *rsa.PrivateKey
	keyHandle  tpmutil.Handle
}

// NewTPMRTCService initializes TPM hardware for timestamp signing.
// Requires /dev/tpm0 to be available.
func NewTPMRTCService() (*TPMRTCService, error) {
	// Open TPM device
	tpm, err := os.OpenFile("/dev/tpm0", os.O_RDWR, 0)
	if err != nil {
		return nil, fmt.Errorf("failed to open TPM: %v", err)
	}

	service := &TPMRTCService{
		tpm: tpm,
	}

	return service, nil
}

// SignTimestamp signs a timestamp using the TPM hardware key
func (s *TPMRTCService) SignTimestamp(timestamp *Timestamp) error {
	data := fmt.Sprintf("%d:%d:%s", timestamp.Unix, timestamp.Nano, timestamp.Source)
	hash := sha256.Sum256([]byte(data))

	// In production, this would use tpm2.Sign with the hardware key
	// Placeholder: sign with hash for demonstration
	timestamp.Signature = base64.StdEncoding.EncodeToString(hash[:])
	return nil
}

// GetAttestation generates a TPM attestation quote
func (s *TPMRTCService) GetAttestation() ([]byte, error) {
	attestation := struct {
		Quote     []byte         `json:"quote"`
		Signature []byte         `json:"signature"`
		PCRValues map[int][]byte `json:"pcr_values"`
	}{
		Quote:     []byte("tpm-attestation-requires-hardware"),
		PCRValues: make(map[int][]byte),
	}

	return json.Marshal(attestation)
}

// Close releases the TPM device
func (s *TPMRTCService) Close() error {
	if s.tpm != nil {
		return s.tpm.Close()
	}
	return nil
}
