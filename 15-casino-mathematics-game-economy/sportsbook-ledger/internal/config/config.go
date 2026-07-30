// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Package config loads runtime configuration from environment variables.
// Mirrors reference.conf from the Scala source.
package config

import (
	"os"
	"strconv"
	"strings"
)

// Config holds all runtime parameters for the sportsbook ledger service.
type Config struct {
	Host                  string
	Port                  int
	DatabaseURL           string
	DatabaseUser          string
	DatabasePassword      string
	BMCURL                string
	BMCSchedulerRateS     int
	BMCBatchSize          int
	BMCHTTPTimeoutMs      int
	CertPath              string
	JurisdictionFiltering bool
	Jurisdiction          string // comma-separated list
	StartFrom             int64
}

// Load reads configuration from environment variables with defaults.
func Load() *Config {
	return &Config{
		Host:                  envStr("HOST", "0.0.0.0"),
		Port:                  envInt("PORT", 8080),
		DatabaseURL:           envStr("PLATFORM_DB_URL", "postgres://localhost:5432/Sandbox"),
		DatabaseUser:          envStr("PLATFORM_DB_USER", "postgres"),
		DatabasePassword:      envStr("PLATFORM_DB_PASSWORD", "passwd"),
		BMCURL:                envStr("BMC_URL", "https://ctn-operator.api.sportsbook.com/message-feed/api/betstream/acme/v1"),
		BMCSchedulerRateS:     envInt("BMC_SCHEDULER_RATE", 30),
		BMCBatchSize:          envInt("BMC_BATCH_SIZE", 1000),
		BMCHTTPTimeoutMs:      10000,
		CertPath:              envStr("CERT_NAME", "/opt/sportsbook-dev.p12"),
		JurisdictionFiltering: envBool("JURISDICTION_FILTERING", true),
		Jurisdiction:          envStr("JURISDICTION", "US_MICHIGAN,US_PENNSYLVANIA"),
		StartFrom:             int64(envInt("START_FROM", 0)),
	}
}

// JurisdictionList returns the jurisdiction allowlist as a lookup map.
func (c *Config) JurisdictionList() map[string]bool {
	m := make(map[string]bool)
	for _, j := range strings.Split(c.Jurisdiction, ",") {
		m[strings.TrimSpace(j)] = true
	}
	return m
}

func envStr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envBool(key string, def bool) bool {
	if v := os.Getenv(key); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return def
}
