// Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// pkg/mtls/client.go
package mtls

import (
	"crypto/tls"
	"net"
	"net/http"
	"time"
)

// NewMTLSClient creates an HTTP client that presents its own certificate
// and verifies the server certificate against the shared CA pool.
func NewMTLSClient(cw *CertWatcher) *http.Client {
	tlsCfg := &tls.Config{
		GetClientCertificate: cw.GetClientCertificate,
		RootCAs:              cw.ClientPool(),
		MinVersion:           tls.VersionTLS13,
	}

	transport := &http.Transport{
		TLSClientConfig: tlsCfg,
		DialContext: (&net.Dialer{
			Timeout:   5 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		MaxIdleConns:          100,
		MaxIdleConnsPerHost:   20,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
	}

	return &http.Client{
		Transport: transport,
		Timeout:   30 * time.Second,
	}
}
