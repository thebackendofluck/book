// Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// rtc-service/main.go
package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/mux"
	"github.com/gorilla/websocket"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	_ "google.golang.org/grpc"
	"periph.io/x/conn/v3/i2c"
	_ "periph.io/x/conn/v3/i2c/i2creg"
	"periph.io/x/host/v3"
)

var _ = websocket.Upgrader{}

const (
	// maxBatchTimestampCount caps a single batch request so a malicious or
	// buggy client can't force the server to allocate/hold an unbounded
	// number of timestamps in memory (OOM).
	maxBatchTimestampCount = 1000

	// wsReplayBufferSize is how many recent stream messages are retained so
	// a reconnecting client can resume without a full state resync.
	wsReplayBufferSize = 200

	// wsTicketMaxAge bounds how long a signed WebSocket upgrade ticket
	// remains valid after issuance.
	wsTicketMaxAge = 5 * time.Minute

	wsWriteWait  = 10 * time.Second
	wsPongWait   = 60 * time.Second
	wsPingPeriod = (wsPongWait * 9) / 10
	// wsMaxMessageSize limits inbound frames on the (otherwise server-push
	// only) stream connection; the read pump exists to process control
	// frames, not application data.
	wsMaxMessageSize = 512
)

// RTCService provides high-precision time services
type RTCService struct {
	mu              sync.RWMutex
	primaryRTC      *ChronoPi
	secondaryRTCs   []*ChronoPi
	consensusQuorum int
	driftThreshold  time.Duration
	metrics         *RTCMetrics
	secretKey       []byte
	allowedOrigins  []string
	streamBuffer    *replayBuffer
}

// ChronoPi represents a hardware RTC module
type ChronoPi struct {
	ID          string
	Device      i2c.Dev
	LastSync    time.Time
	DriftRate   float64
	Temperature float64
	Battery     float64
	mu          sync.Mutex
}

// Timestamp represents an RTC-validated timestamp
type Timestamp struct {
	Unix        int64             `json:"unix"`
	Nano        int64             `json:"nano"`
	ISO8601     string            `json:"iso8601"`
	Source      string            `json:"source"`
	Signature   string            `json:"signature"`
	Confidence  float64           `json:"confidence"`
	DriftMS     float64           `json:"drift_ms"`
	Temperature float64           `json:"temperature"`
	Metadata    map[string]string `json:"metadata"`
	Sequence    uint64            `json:"sequence"`
}

// RTCMetrics tracks performance and health metrics
type RTCMetrics struct {
	RequestsTotal   prometheus.Counter
	RequestDuration prometheus.Histogram
	DriftGauge      prometheus.Gauge
	ErrorsTotal     prometheus.Counter
	BatteryLevel    prometheus.Gauge
}

// Initialize RTC Service
func NewRTCService(config *Config) (*RTCService, error) {
	// Initialize hardware
	if _, err := host.Init(); err != nil {
		return nil, fmt.Errorf("failed to initialize hardware: %v", err)
	}

	service := &RTCService{
		consensusQuorum: config.ConsensusQuorum,
		driftThreshold:  time.Duration(config.DriftThresholdMS) * time.Millisecond,
		secretKey:       []byte(config.SecretKey),
		allowedOrigins:  config.AllowedOrigins,
		streamBuffer:    newReplayBuffer(wsReplayBufferSize),
	}

	// Initialize primary RTC
	primary, err := initializeChronoPi(config.PrimaryRTC)
	if err != nil {
		return nil, fmt.Errorf("failed to initialize primary RTC: %v", err)
	}
	service.primaryRTC = primary

	// Initialize secondary RTCs
	for _, rtcConfig := range config.SecondaryRTCs {
		rtc, err := initializeChronoPi(rtcConfig)
		if err != nil {
			log.Printf("Warning: failed to initialize secondary RTC %s: %v", rtcConfig.ID, err)
			continue
		}
		service.secondaryRTCs = append(service.secondaryRTCs, rtc)
	}

	// Initialize metrics
	service.metrics = initializeMetrics()

	return service, nil
}

// GetTimestamp returns a cryptographically signed timestamp
func (s *RTCService) GetTimestamp(ctx context.Context, metadata map[string]string) (*Timestamp, error) {
	startTime := time.Now()
	defer func() {
		s.metrics.RequestDuration.Observe(time.Since(startTime).Seconds())
		s.metrics.RequestsTotal.Inc()
	}()

	// Get consensus timestamp
	consensusTime, confidence, err := s.getConsensusTime()
	if err != nil {
		s.metrics.ErrorsTotal.Inc()
		return nil, fmt.Errorf("consensus failed: %v", err)
	}

	// Create timestamp
	ts := &Timestamp{
		Unix:        consensusTime.Unix(),
		Nano:        consensusTime.UnixNano(),
		ISO8601:     consensusTime.Format(time.RFC3339Nano),
		Source:      s.primaryRTC.ID,
		Confidence:  confidence,
		Temperature: s.primaryRTC.Temperature,
		Metadata:    metadata,
	}

	// Calculate drift
	systemTime := time.Now()
	ts.DriftMS = float64(consensusTime.Sub(systemTime)) / float64(time.Millisecond)
	s.metrics.DriftGauge.Set(ts.DriftMS)

	// Sign timestamp
	ts.Signature = s.signTimestamp(ts)

	return ts, nil
}

// getConsensusTime implements Byzantine Fault Tolerant consensus
func (s *RTCService) getConsensusTime() (time.Time, float64, error) {
	type rtcReading struct {
		Time   time.Time
		Source string
		Error  error
	}

	readings := make(chan rtcReading, len(s.secondaryRTCs)+1)

	// Collect readings in parallel
	var wg sync.WaitGroup

	// Primary RTC
	wg.Add(1)
	go func() {
		defer wg.Done()
		t, err := s.primaryRTC.GetTime()
		readings <- rtcReading{Time: t, Source: s.primaryRTC.ID, Error: err}
	}()

	// Secondary RTCs
	for _, rtc := range s.secondaryRTCs {
		wg.Add(1)
		go func(r *ChronoPi) {
			defer wg.Done()
			t, err := r.GetTime()
			readings <- rtcReading{Time: t, Source: r.ID, Error: err}
		}(rtc)
	}

	wg.Wait()
	close(readings)

	// Collect valid readings
	var validReadings []time.Time
	for reading := range readings {
		if reading.Error == nil {
			validReadings = append(validReadings, reading.Time)
		}
	}

	if len(validReadings) < s.consensusQuorum {
		return time.Time{}, 0, fmt.Errorf("insufficient RTCs for consensus: %d/%d",
			len(validReadings), s.consensusQuorum)
	}

	// Calculate median time
	medianTime := calculateMedianTime(validReadings)

	// Calculate confidence based on agreement
	confidence := calculateConfidence(validReadings, medianTime, s.driftThreshold)

	return medianTime, confidence, nil
}

// GetTime reads time from ChronoPi hardware
func (c *ChronoPi) GetTime() (time.Time, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	// Read from I2C device
	buf := make([]byte, 7)
	if err := c.Device.Tx([]byte{0x00}, buf); err != nil {
		return time.Time{}, err
	}

	// Parse BCD time values
	second := bcdToDec(buf[0] & 0x7F)
	minute := bcdToDec(buf[1])
	hour := bcdToDec(buf[2] & 0x3F)
	day := bcdToDec(buf[4])
	month := bcdToDec(buf[5] & 0x1F)
	year := 2000 + bcdToDec(buf[6])

	// Read nanosecond precision from extended registers
	nsecBuf := make([]byte, 4)
	if err := c.Device.Tx([]byte{0x10}, nsecBuf); err != nil {
		return time.Time{}, err
	}
	nsec := int(nsecBuf[0])<<24 | int(nsecBuf[1])<<16 | int(nsecBuf[2])<<8 | int(nsecBuf[3])

	// Update temperature and battery readings
	c.updateHealthMetrics()

	return time.Date(year, time.Month(month), day, hour, minute, second, nsec, time.UTC), nil
}

// signTimestamp creates HMAC-SHA256 signature
func (s *RTCService) signTimestamp(ts *Timestamp) string {
	data := fmt.Sprintf("%d:%d:%s", ts.Unix, ts.Nano, ts.Source)
	h := hmac.New(sha256.New, s.secretKey)
	h.Write([]byte(data))
	return hex.EncodeToString(h.Sum(nil))
}

// HTTP API Handlers
func (s *RTCService) handleGetTimestamp(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	// Extract metadata from headers
	metadata := make(map[string]string)
	metadata["request_id"] = r.Header.Get("X-Request-ID")
	metadata["game_id"] = r.Header.Get("X-Game-ID")
	metadata["user_id"] = r.Header.Get("X-User-ID")

	ts, err := s.GetTimestamp(ctx, metadata)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(ts)
}

// Batch timestamp operations
func (s *RTCService) handleBatchTimestamp(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Count    int               `json:"count"`
		Interval int               `json:"interval_ms"`
		Metadata map[string]string `json:"metadata"`
	}

	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	if request.Count <= 0 || request.Count > maxBatchTimestampCount {
		http.Error(w, fmt.Sprintf("count must be between 1 and %d", maxBatchTimestampCount), http.StatusBadRequest)
		return
	}

	timestamps := make([]*Timestamp, request.Count)
	interval := time.Duration(request.Interval) * time.Millisecond

	for i := 0; i < request.Count; i++ {
		ts, err := s.GetTimestamp(r.Context(), request.Metadata)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		timestamps[i] = ts

		if i < request.Count-1 {
			time.Sleep(interval)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(timestamps)
}

// authenticateUpgrade validates a signed ticket presented as either
// "Authorization: Bearer <unix>.<hex-hmac>" or a "?token=<unix>.<hex-hmac>"
// query parameter (needed because browser WebSocket clients cannot set
// arbitrary headers). The ticket is HMAC-SHA256 signed with the service's
// secret key, domain-separated from timestamp signing, and expires after
// wsTicketMaxAge.
func (s *RTCService) authenticateUpgrade(r *http.Request) bool {
	token := ""
	if auth := r.Header.Get("Authorization"); strings.HasPrefix(auth, "Bearer ") {
		token = strings.TrimPrefix(auth, "Bearer ")
	} else {
		token = r.URL.Query().Get("token")
	}
	if token == "" {
		return false
	}

	parts := strings.SplitN(token, ".", 2)
	if len(parts) != 2 {
		return false
	}
	return s.verifyWSTicket(parts[0], parts[1])
}

// signWSTicket signs a WebSocket upgrade ticket timestamp. The "wsauth:"
// prefix keeps this signature domain separate from signTimestamp's.
func (s *RTCService) signWSTicket(ts string) string {
	h := hmac.New(sha256.New, s.secretKey)
	h.Write([]byte("wsauth:" + ts))
	return hex.EncodeToString(h.Sum(nil))
}

func (s *RTCService) verifyWSTicket(ts, sig string) bool {
	if ts == "" || sig == "" {
		return false
	}
	tsUnix, err := strconv.ParseInt(ts, 10, 64)
	if err != nil {
		return false
	}
	age := time.Since(time.Unix(tsUnix, 0))
	if age < 0 || age > wsTicketMaxAge {
		return false
	}
	expected := s.signWSTicket(ts)
	return hmac.Equal([]byte(expected), []byte(sig))
}

// isAllowedOrigin implements a fail-closed CheckOrigin policy: requests with
// no Origin header (non-browser SDK clients) are treated as same-origin and
// allowed; browser requests are only allowed if an explicit allowlist is
// configured and the Origin matches an entry, or if no allowlist is
// configured and the Origin matches the request Host (same-origin).
func isAllowedOrigin(origin string, r *http.Request, allowed []string) bool {
	if origin == "" {
		return true
	}
	if len(allowed) == 0 {
		u, err := url.Parse(origin)
		if err != nil {
			return false
		}
		return u.Host == r.Host
	}
	for _, o := range allowed {
		if o == origin {
			return true
		}
	}
	return false
}

// WebSocket stream for real-time updates
func (s *RTCService) handleStreamTimestamp(w http.ResponseWriter, r *http.Request) {
	if !s.authenticateUpgrade(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	upgrader := websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool {
			return isAllowedOrigin(r.Header.Get("Origin"), r, s.allowedOrigins)
		},
	}

	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("WebSocket upgrade failed: %v", err)
		return
	}
	defer conn.Close()

	conn.SetReadLimit(wsMaxMessageSize)
	conn.SetReadDeadline(time.Now().Add(wsPongWait))
	conn.SetPongHandler(func(string) error {
		conn.SetReadDeadline(time.Now().Add(wsPongWait))
		return nil
	})

	// The stream is server-push only. This read pump's job is to process
	// control frames (pong/close) and notice a dead/closed peer so the
	// write loop below terminates instead of leaking a goroutine forever.
	closed := make(chan struct{})
	go func() {
		defer close(closed)
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}()

	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	pingTicker := time.NewTicker(wsPingPeriod)
	defer pingTicker.Stop()

	if resumeParam := r.URL.Query().Get("resume_from"); resumeParam != "" {
		if resumeSeq, err := strconv.ParseUint(resumeParam, 10, 64); err == nil {
			backlog, ok := s.streamBuffer.Since(resumeSeq)
			if !ok {
				conn.SetWriteDeadline(time.Now().Add(wsWriteWait))
				if err := conn.WriteJSON(map[string]bool{"resync_required": true}); err != nil {
					log.Printf("WebSocket write failed: %v", err)
					return
				}
			} else {
				for _, ts := range backlog {
					conn.SetWriteDeadline(time.Now().Add(wsWriteWait))
					if err := conn.WriteJSON(ts); err != nil {
						log.Printf("WebSocket write failed: %v", err)
						return
					}
				}
			}
		}
	}

	for {
		select {
		case <-ticker.C:
			ts, err := s.GetTimestamp(r.Context(), nil)
			if err != nil {
				log.Printf("Failed to get timestamp: %v", err)
				continue
			}
			s.streamBuffer.Append(ts)

			conn.SetWriteDeadline(time.Now().Add(wsWriteWait))
			if err := conn.WriteJSON(ts); err != nil {
				log.Printf("WebSocket write failed: %v", err)
				return
			}

		case <-pingTicker.C:
			conn.SetWriteDeadline(time.Now().Add(wsWriteWait))
			if err := conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				log.Printf("WebSocket ping failed: %v", err)
				return
			}

		case <-closed:
			return

		case <-r.Context().Done():
			return
		}
	}
}

func main() {
	config := loadConfig()

	if err := validateConfig(config); err != nil {
		log.Fatalf("invalid configuration: %v", err)
	}

	service, err := NewRTCService(config)
	if err != nil {
		log.Fatalf("Failed to initialize RTC service: %v", err)
	}

	router := mux.NewRouter()

	// API endpoints
	router.HandleFunc("/api/v1/timestamp", service.handleGetTimestamp).Methods("GET")
	router.HandleFunc("/api/v1/timestamp/batch", service.handleBatchTimestamp).Methods("POST")
	router.HandleFunc("/api/v1/timestamp/stream", service.handleStreamTimestamp).Methods("GET")
	router.HandleFunc("/api/v1/timestamp/validate", service.handleValidateTimestamp).Methods("POST")
	router.HandleFunc("/api/v1/health", service.handleHealth).Methods("GET")

	// Metrics endpoint
	router.Handle("/metrics", promhttp.Handler())

	// Start server
	log.Printf("RTC Service starting on :8080")
	log.Fatal(http.ListenAndServe(":8080", router))
}
