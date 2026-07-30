// Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

package main

import (
	"crypto/hmac"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"periph.io/x/conn/v3/i2c"
	"periph.io/x/conn/v3/i2c/i2creg"
)

// ensure websocket import is used
var _ = websocket.Upgrader{}

// Config holds service configuration
type Config struct {
	ConsensusQuorum  int         `json:"consensus_quorum"`
	DriftThresholdMS int         `json:"drift_threshold_ms"`
	SecretKey        string      `json:"secret_key"`
	PrimaryRTC       RTCConfig   `json:"primary_rtc"`
	SecondaryRTCs    []RTCConfig `json:"secondary_rtcs"`
	ListenAddr       string      `json:"listen_addr"`
	// AllowedOrigins is the CORS/WebSocket origin allowlist. Empty means
	// only same-origin requests are accepted (fail closed).
	AllowedOrigins []string `json:"allowed_origins"`
}

// defaultSecretKey is the placeholder shipped in code; the service refuses
// to start if this (or an empty string) is still configured, since it would
// let anyone forge timestamp signatures and WebSocket upgrade tickets.
const defaultSecretKey = "change-me-in-production"

// validateConfig rejects unsafe configuration before the service starts.
func validateConfig(config *Config) error {
	if config.SecretKey == "" || config.SecretKey == defaultSecretKey {
		return fmt.Errorf("RTC_SECRET_KEY must be set to a strong, unique secret (set via env var or config file), refusing to start with an empty/default secret")
	}
	if len(config.SecretKey) < 16 {
		return fmt.Errorf("RTC_SECRET_KEY must be at least 16 bytes long")
	}
	return nil
}

// RTCConfig holds individual RTC module configuration
type RTCConfig struct {
	ID      string `json:"id"`
	Bus     string `json:"bus"`
	Address uint16 `json:"address"`
}

// websocket upgrader for streaming endpoint
var upgrader = websocket.Upgrader{}

// initializeChronoPi sets up a ChronoPi hardware RTC device
func initializeChronoPi(config RTCConfig) (*ChronoPi, error) {
	bus, err := i2creg.Open(config.Bus)
	if err != nil {
		return nil, err
	}

	dev := &i2c.Dev{Bus: bus, Addr: config.Address}

	return &ChronoPi{
		ID:       config.ID,
		Device:   *dev,
		LastSync: time.Now(),
	}, nil
}

// initializeMetrics sets up Prometheus metrics
func initializeMetrics() *RTCMetrics {
	return &RTCMetrics{
		RequestsTotal: promauto.NewCounter(prometheus.CounterOpts{
			Name: "rtc_requests_total",
			Help: "Total RTC timestamp requests",
		}),
		RequestDuration: promauto.NewHistogram(prometheus.HistogramOpts{
			Name:    "rtc_request_duration_seconds",
			Help:    "Duration of RTC timestamp requests",
			Buckets: prometheus.DefBuckets,
		}),
		DriftGauge: promauto.NewGauge(prometheus.GaugeOpts{
			Name: "rtc_drift_milliseconds",
			Help: "Current drift between RTC and system time",
		}),
		ErrorsTotal: promauto.NewCounter(prometheus.CounterOpts{
			Name: "rtc_errors_total",
			Help: "Total RTC errors",
		}),
		BatteryLevel: promauto.NewGauge(prometheus.GaugeOpts{
			Name: "rtc_battery_level",
			Help: "RTC battery level percentage",
		}),
	}
}

// bcdToDec converts BCD to decimal
func bcdToDec(val byte) int {
	return int(val/16*10 + val%16)
}

// calculateMedianTime returns median of time readings
func calculateMedianTime(readings []time.Time) time.Time {
	sort.Slice(readings, func(i, j int) bool {
		return readings[i].Before(readings[j])
	})
	mid := len(readings) / 2
	if len(readings)%2 == 0 {
		avg := readings[mid-1].Add(readings[mid].Sub(readings[mid-1]) / 2)
		return avg
	}
	return readings[mid]
}

// calculateConfidence scores consensus agreement
func calculateConfidence(readings []time.Time, median time.Time, threshold time.Duration) float64 {
	agreeing := 0
	for _, r := range readings {
		if math.Abs(float64(r.Sub(median))) < float64(threshold) {
			agreeing++
		}
	}
	return float64(agreeing) / float64(len(readings))
}

// updateHealthMetrics reads temperature and battery from the RTC
func (c *ChronoPi) updateHealthMetrics() {
	// Read temperature register (0x11-0x12 on DS3231)
	tempBuf := make([]byte, 2)
	if err := c.Device.Tx([]byte{0x11}, tempBuf); err == nil {
		c.Temperature = float64(int8(tempBuf[0])) + float64(tempBuf[1]>>6)*0.25
	}
	c.Battery = 100.0 // DS3231 doesn't report battery directly
}

// handleValidateTimestamp validates a previously issued timestamp
func (s *RTCService) handleValidateTimestamp(w http.ResponseWriter, r *http.Request) {
	var ts Timestamp
	if err := json.NewDecoder(r.Body).Decode(&ts); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	expected := s.signTimestamp(&ts)
	valid := hmac.Equal([]byte(expected), []byte(ts.Signature))

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"valid": valid})
}

// handleHealth returns service health status
func (s *RTCService) handleHealth(w http.ResponseWriter, r *http.Request) {
	health := map[string]interface{}{
		"status":    "ok",
		"primary":   s.primaryRTC.ID,
		"secondary": len(s.secondaryRTCs),
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(health)
}

// loadConfig reads configuration from file or environment. The secret key
// intentionally has no hardcoded fallback: validateConfig refuses to start
// the service if it ends up empty (or left as the historical default), so
// operators must supply RTC_SECRET_KEY or set secret_key in RTC_CONFIG.
func loadConfig() *Config {
	config := &Config{
		ConsensusQuorum:  3,
		DriftThresholdMS: 50,
		SecretKey:        os.Getenv("RTC_SECRET_KEY"),
		ListenAddr:       ":8080",
		PrimaryRTC: RTCConfig{
			ID:      "rtc-primary",
			Bus:     "1",
			Address: 0x68,
		},
	}

	if origins := os.Getenv("RTC_ALLOWED_ORIGINS"); origins != "" {
		for _, o := range strings.Split(origins, ",") {
			if o = strings.TrimSpace(o); o != "" {
				config.AllowedOrigins = append(config.AllowedOrigins, o)
			}
		}
	}

	configFile := os.Getenv("RTC_CONFIG")
	if configFile != "" {
		f, err := os.Open(configFile)
		if err == nil {
			defer f.Close()
			data, _ := io.ReadAll(f)
			if err := json.Unmarshal(data, config); err != nil {
				log.Printf("Warning: failed to parse config: %v", err)
			}
		}
	}

	return config
}

// replayBuffer is a bounded ring buffer of the most recently streamed
// timestamps, keyed by a monotonically increasing sequence number. It lets
// a WebSocket client that reconnects resume from where it left off instead
// of always needing a full state resync.
type replayBuffer struct {
	mu      sync.Mutex
	entries []sequencedTimestamp
	cap     int
	nextSeq uint64
}

type sequencedTimestamp struct {
	seq uint64
	ts  *Timestamp
}

func newReplayBuffer(capacity int) *replayBuffer {
	return &replayBuffer{cap: capacity}
}

// Append assigns the next sequence number to ts, stores it, and returns the
// assigned sequence.
func (b *replayBuffer) Append(ts *Timestamp) uint64 {
	b.mu.Lock()
	defer b.mu.Unlock()

	b.nextSeq++
	seq := b.nextSeq
	ts.Sequence = seq

	b.entries = append(b.entries, sequencedTimestamp{seq: seq, ts: ts})
	if len(b.entries) > b.cap {
		b.entries = b.entries[len(b.entries)-b.cap:]
	}
	return seq
}

// Since returns every buffered message after lastSeq. ok is false when
// lastSeq has already fallen out of the buffer (the gap can't be closed
// from history), meaning the caller must perform a full state resync
// instead of trusting a partial replay.
func (b *replayBuffer) Since(lastSeq uint64) (out []*Timestamp, ok bool) {
	b.mu.Lock()
	defer b.mu.Unlock()

	if len(b.entries) == 0 {
		return nil, true
	}
	oldest := b.entries[0].seq
	if lastSeq != 0 && lastSeq < oldest-1 {
		return nil, false
	}
	for _, e := range b.entries {
		if e.seq > lastSeq {
			out = append(out, e.ts)
		}
	}
	return out, true
}
