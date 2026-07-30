// Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// pkg/spiffe/client.go
package spiffe

import (
	"context"
	"crypto/tls"
	"fmt"
	"net/http"

	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/spiffetls"
	"github.com/spiffe/go-spiffe/v2/spiffetls/tlsconfig"
	"github.com/spiffe/go-spiffe/v2/workloadapi"
)

const agentSocket = "unix:///run/spire/sockets/agent.sock"

// NewMTLSServer creates an mTLS server using SPIFFE SVIDs.
// The server automatically refreshes its certificate when SPIRE rotates it.
// allowedCallers is the explicit allowlist of caller SPIFFE IDs permitted to
// connect. mTLS authenticates that a caller holds a valid SVID from the
// trust domain; that is not authorization. Without this allowlist, any
// workload in the mesh that can obtain an SVID could call this server.
func NewMTLSServer(ctx context.Context, addr string, handler http.Handler, allowedCallers ...string) (*http.Server, error) {
	if len(allowedCallers) == 0 {
		return nil, fmt.Errorf("NewMTLSServer requires at least one allowed caller SPIFFE ID")
	}

	// X509Source connects to the SPIRE agent and provides live SVIDs
	source, err := workloadapi.NewX509Source(ctx,
		workloadapi.WithClientOptions(workloadapi.WithAddr(agentSocket)),
	)
	if err != nil {
		return nil, fmt.Errorf("create X509 source: %w", err)
	}

	ids := make([]spiffeid.ID, 0, len(allowedCallers))
	for _, raw := range allowedCallers {
		id, err := spiffeid.FromString(raw)
		if err != nil {
			return nil, fmt.Errorf("parse allowed caller SPIFFE ID %q: %w", raw, err)
		}
		ids = append(ids, id)
	}

	tlsConfig := tlsconfig.MTLSServerConfig(
		source,
		source,
		tlsconfig.AuthorizeOneOf(ids...), // Only these caller identities may connect
	)
	tlsConfig.MinVersion = tls.VersionTLS13

	return &http.Server{
		Addr:      addr,
		Handler:   handler,
		TLSConfig: tlsConfig,
	}, nil
}

// NewMTLSClientFor creates an mTLS client that only connects to
// the specified SPIFFE workload ID. Rejects connections to any other identity.
func NewMTLSClientFor(ctx context.Context, targetID string) (*http.Client, error) {
	source, err := workloadapi.NewX509Source(ctx,
		workloadapi.WithClientOptions(workloadapi.WithAddr(agentSocket)),
	)
	if err != nil {
		return nil, fmt.Errorf("create X509 source: %w", err)
	}

	id, err := spiffeid.FromString(targetID)
	if err != nil {
		return nil, fmt.Errorf("parse SPIFFE ID: %w", err)
	}

	tlsConfig := tlsconfig.MTLSClientConfig(
		source,
		source,
		tlsconfig.AuthorizeID(id), // Strict: only accept this specific workload identity
	)
	tlsConfig.MinVersion = tls.VersionTLS13

	return &http.Client{
		Transport: &http.Transport{TLSClientConfig: tlsConfig},
	}, nil
}

// Ensure spiffetls import is used (it provides the MTLSServerConfig abstraction layer).
var _ = spiffetls.Listen
