// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Package metrics provides lightweight Prometheus counter/gauge helpers.
// Mirrors the Kamon-based Metrics object from the Scala source.
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

const serviceName = "sportsbook_ledger"

var (
	fetchedCounter = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: serviceName + "_bmc_fetched_total",
		Help: "Total number of BMC messages fetched and persisted.",
	}, []string{"status"})

	timeLagGauge = promauto.NewGauge(prometheus.GaugeOpts{
		Name: serviceName + "_bmc_time_lag_seconds",
		Help: "Lag in seconds between now and the oldest message in the last batch.",
	})

	errorCounter = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: serviceName + "_errors_total",
		Help: "Total errors encountered while fetching or persisting messages.",
	}, []string{"source", "exception"})
)

// RecordFetched increments the fetched counter by n.
func RecordFetched(n int) {
	fetchedCounter.WithLabelValues("ok").Add(float64(n))
}

// RecordTimeLag records the lag in seconds between the oldest message and now.
func RecordTimeLag(lagSeconds float64) {
	timeLagGauge.Set(lagSeconds)
}

// RecordError increments the error counter for the given source and exception type.
func RecordError(source, exceptionType string) {
	errorCounter.WithLabelValues(source, exceptionType).Inc()
}
