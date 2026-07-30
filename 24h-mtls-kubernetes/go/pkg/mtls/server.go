// Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// pkg/mtls/server.go
package mtls

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"net/http"
	"os"
	"sync/atomic"
	"unsafe"
	"time"

	"github.com/fsnotify/fsnotify"
	"go.uber.org/zap"
)

// CertWatcher watches certificate files and provides atomic updates
// so we can hot-reload certs without restarting the service.
type CertWatcher struct {
	certFile string
	keyFile  string
	caFile   string
	logger   *zap.Logger
	cert     unsafe.Pointer  // *tls.Certificate
	pool     unsafe.Pointer  // *x509.CertPool
}

func NewCertWatcher(certFile, keyFile, caFile string, logger *zap.Logger) (*CertWatcher, error) {
	cw := &CertWatcher{
		certFile: certFile,
		keyFile:  keyFile,
		caFile:   caFile,
		logger:   logger,
	}
	if err := cw.reload(); err != nil {
		return nil, fmt.Errorf("initial cert load: %w", err)
	}
	return cw, nil
}

func (cw *CertWatcher) reload() error {
	cert, err := tls.LoadX509KeyPair(cw.certFile, cw.keyFile)
	if err != nil {
		return fmt.Errorf("load key pair: %w", err)
	}

	caData, err := os.ReadFile(cw.caFile)
	if err != nil {
		return fmt.Errorf("read CA bundle: %w", err)
	}

	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caData) {
		return fmt.Errorf("failed to parse any certs from CA bundle")
	}

	atomic.StorePointer(&cw.cert, unsafe.Pointer(&cert))
	atomic.StorePointer(&cw.pool, unsafe.Pointer(pool))

	cw.logger.Info("TLS certificates reloaded",
		zap.String("cert", cw.certFile),
		zap.Time("not_after", cert.Leaf.NotAfter),
	)
	return nil
}

func (cw *CertWatcher) GetCertificate(*tls.ClientHelloInfo) (*tls.Certificate, error) {
	cert := (*tls.Certificate)(atomic.LoadPointer(&cw.cert))
	return cert, nil
}

func (cw *CertWatcher) GetClientCertificate(*tls.CertificateRequestInfo) (*tls.Certificate, error) {
	cert := (*tls.Certificate)(atomic.LoadPointer(&cw.cert))
	return cert, nil
}

func (cw *CertWatcher) ClientPool() *x509.CertPool {
	return (*x509.CertPool)(atomic.LoadPointer(&cw.pool))
}

// Watch starts an inotify watcher on the cert files and reloads on change.
func (cw *CertWatcher) Watch() error {
	watcher, err := fsnotify.NewWatcher()
	if err != nil {
		return fmt.Errorf("create watcher: %w", err)
	}

	for _, f := range []string{cw.certFile, cw.keyFile, cw.caFile} {
		if err := watcher.Add(f); err != nil {
			return fmt.Errorf("watch %s: %w", f, err)
		}
	}

	go func() {
		defer watcher.Close()
		for {
			select {
			case event, ok := <-watcher.Events:
				if !ok {
					return
				}
				// cert-manager writes via rename (atomic write pattern)
				if event.Has(fsnotify.Write) || event.Has(fsnotify.Create) {
					// Small delay to ensure both cert and key are fully written
					time.Sleep(100 * time.Millisecond)
					if err := cw.reload(); err != nil {
						cw.logger.Error("cert reload failed", zap.Error(err))
					}
					// Re-add the watch since rename removes the inotify watch
					_ = watcher.Add(cw.certFile)
					_ = watcher.Add(cw.keyFile)
				}
			case err, ok := <-watcher.Errors:
				if !ok {
					return
				}
				cw.logger.Error("watcher error", zap.Error(err))
			}
		}
	}()

	return nil
}

// NewMTLSServer creates an HTTP server enforcing mutual TLS. allowedCallers
// is the explicit allowlist of caller identities (SPIFFE URI SAN or CN)
// permitted to connect; presenting a certificate signed by a trusted CA
// proves the caller belongs to the mesh, not that this specific caller is
// authorized to reach this service, so the allowlist check fails closed.
func NewMTLSServer(addr string, handler http.Handler, cw *CertWatcher, allowedCallers []string) *http.Server {
	allowed := make(map[string]struct{}, len(allowedCallers))
	for _, c := range allowedCallers {
		allowed[c] = struct{}{}
	}

	tlsCfg := &tls.Config{
		MinVersion: tls.VersionTLS13,
		// TLS 1.3 cipher suites are fixed; this has no effect but documents intent
		CurvePreferences: []tls.CurveID{
			tls.X25519,
			tls.CurveP256,
		},
		// GetConfigForClient runs on every handshake, so GetCertificate and
		// ClientCAs are read fresh from the reloading CertWatcher each time
		// instead of being captured once at server start. A rotated or
		// revoked trust bundle takes effect on the next handshake without
		// a restart.
		GetConfigForClient: func(*tls.ClientHelloInfo) (*tls.Config, error) {
			return &tls.Config{
				GetCertificate: cw.GetCertificate,
				ClientAuth:     tls.RequireAndVerifyClientCert,
				ClientCAs:      cw.ClientPool(),
				MinVersion:     tls.VersionTLS13,
				CurvePreferences: []tls.CurveID{
					tls.X25519,
					tls.CurveP256,
				},
				// VerifyConnection (rather than VerifyPeerCertificate) also
				// runs on resumed sessions, so the caller allowlist cannot
				// be bypassed via session resumption.
				VerifyConnection: func(cs tls.ConnectionState) error {
					if len(cs.VerifiedChains) == 0 || len(cs.VerifiedChains[0]) == 0 {
						return fmt.Errorf("mtls: no verified client certificate chain")
					}
					leaf := cs.VerifiedChains[0][0]
					if callerAllowed(leaf, allowed) {
						return nil
					}
					return fmt.Errorf("mtls: caller %s is not in the allowed caller list", callerIdentity(leaf))
				},
			}, nil
		},
	}

	return &http.Server{
		Addr:         addr,
		Handler:      handler,
		TLSConfig:    tlsCfg,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  120 * time.Second,
	}
}

// callerAllowed matches the client certificate's SPIFFE URI SAN (preferred)
// or Common Name against the allowlist.
func callerAllowed(leaf *x509.Certificate, allowed map[string]struct{}) bool {
	for _, uri := range leaf.URIs {
		if _, ok := allowed[uri.String()]; ok {
			return true
		}
	}
	_, ok := allowed[leaf.Subject.CommonName]
	return ok
}

func callerIdentity(leaf *x509.Certificate) string {
	if len(leaf.URIs) > 0 {
		return leaf.URIs[0].String()
	}
	return leaf.Subject.CommonName
}
