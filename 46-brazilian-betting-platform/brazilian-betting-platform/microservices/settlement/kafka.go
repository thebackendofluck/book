// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
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
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"github.com/segmentio/kafka-go"
)

const (
	topicBetPlaced  = "bets.placed"
	topicBetSettled = "bets.settled"
)

// BetPlacedEvent is the Kafka message payload for a newly placed bet.
type BetPlacedEvent struct {
	BetID     string    `json:"bet_id"`
	CPF       string    `json:"cpf"`
	EventID   string    `json:"event_id"`
	Stake     float64   `json:"stake"`
	Odds      float64   `json:"combined_odds"`
	PlacedAt  time.Time `json:"placed_at"`
}

// BetSettledEvent is the Kafka message payload published after settlement.
type BetSettledEvent struct {
	BetID       string    `json:"bet_id"`
	CPF         string    `json:"cpf"`
	EventID     string    `json:"event_id"`
	Outcome     string    `json:"outcome"`
	Stake       float64   `json:"stake"`
	NetPayout   float64   `json:"net_payout"`
	TaxWithheld float64   `json:"tax_withheld"`
	SettledAt   time.Time `json:"settled_at"`
}

// KafkaConsumer reads bet events from Kafka for settlement processing.
type KafkaConsumer struct {
	reader *kafka.Reader
	store  *Store
	sigap  *SIGAPSettlementClient
	ggr    *GGRCalculator
	logger *slog.Logger
}

// NewKafkaConsumer creates a KafkaConsumer connected to the given brokers.
func NewKafkaConsumer(brokers []string, groupID string, store *Store,
	sigap *SIGAPSettlementClient, ggr *GGRCalculator, logger *slog.Logger) *KafkaConsumer {

	reader := kafka.NewReader(kafka.ReaderConfig{
		Brokers:        brokers,
		GroupID:        groupID,
		Topic:          topicBetPlaced,
		MinBytes:       10e3,  // 10 KB
		MaxBytes:       10e6,  // 10 MB
		CommitInterval: time.Second,
		StartOffset:    kafka.LastOffset,
		ErrorLogger:    kafka.LoggerFunc(func(msg string, args ...interface{}) {
			logger.Error("kafka reader error", "msg", msg)
		}),
	})

	return &KafkaConsumer{
		reader: reader,
		store:  store,
		sigap:  sigap,
		ggr:    ggr,
		logger: logger,
	}
}

// KafkaProducer publishes settlement events to Kafka.
type KafkaProducer struct {
	writer *kafka.Writer
	logger *slog.Logger
}

// NewKafkaProducer creates a KafkaProducer.
func NewKafkaProducer(brokers []string, logger *slog.Logger) *KafkaProducer {
	writer := &kafka.Writer{
		Addr:         kafka.TCP(brokers...),
		Topic:        topicBetSettled,
		Balancer:     &kafka.LeastBytes{},
		BatchTimeout: 50 * time.Millisecond,
		RequiredAcks: kafka.RequireOne,
	}
	return &KafkaProducer{writer: writer, logger: logger}
}

// PublishSettlement publishes a BetSettledEvent to Kafka.
func (p *KafkaProducer) PublishSettlement(ctx context.Context, s *Settlement) error {
	event := BetSettledEvent{
		BetID:       s.BetID,
		CPF:         maskCPF(s.CPF),
		EventID:     s.EventID,
		Outcome:     string(s.Outcome),
		Stake:       s.Stake,
		NetPayout:   s.NetPayout,
		TaxWithheld: s.TaxWithheld,
		SettledAt:   s.SettledAt,
	}

	data, err := json.Marshal(event)
	if err != nil {
		return err
	}

	return p.writer.WriteMessages(ctx, kafka.Message{
		Key:   []byte(s.BetID),
		Value: data,
	})
}

// Close shuts down the Kafka producer.
func (p *KafkaProducer) Close() error {
	return p.writer.Close()
}

// Run starts the Kafka consumer loop. Blocks until ctx is cancelled.
func (c *KafkaConsumer) Run(ctx context.Context) {
	c.logger.Info("kafka consumer started", "topic", topicBetPlaced)
	for {
		msg, err := c.reader.ReadMessage(ctx)
		if err != nil {
			if ctx.Err() != nil {
				c.logger.Info("kafka consumer shutting down")
				return
			}
			c.logger.Error("kafka read error", "error", err)
			continue
		}

		var event BetPlacedEvent
		if err := json.Unmarshal(msg.Value, &event); err != nil {
			c.logger.Error("failed to unmarshal bet event",
				"offset", msg.Offset, "error", err)
			continue
		}

		c.logger.Info("bet event received",
			"bet_id", event.BetID,
			"event_id", event.EventID,
			"stake", event.Stake,
		)
		// Real-time settlement processing would happen here;
		// settlement is typically triggered by the POST /settle/event/{id} endpoint.
	}
}

// Close gracefully shuts down the Kafka consumer.
func (c *KafkaConsumer) Close() error {
	return c.reader.Close()
}
