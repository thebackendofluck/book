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
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	oddsKeyPrefix    = "odds:"
	sessionKeyPrefix = "session:"
	limitsKeyPrefix  = "limits:"
	oddsTTL          = 5 * time.Second
	sessionTTL       = 30 * time.Minute
	limitsTTL        = 1 * time.Minute
)

// Cache provides Redis-backed caching for odds and sessions.
type Cache struct {
	client *redis.Client
}

// NewCache creates a new Cache with the given Redis client.
func NewCache(client *redis.Client) *Cache {
	return &Cache{client: client}
}

// GetOdds retrieves cached odds for a given selection.
func (c *Cache) GetOdds(ctx context.Context, selectionID string) (*Odds, error) {
	key := oddsKeyPrefix + selectionID
	data, err := c.client.Get(ctx, key).Bytes()
	if err == redis.Nil {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("redis get odds: %w", err)
	}

	var odds Odds
	if err := json.Unmarshal(data, &odds); err != nil {
		return nil, fmt.Errorf("unmarshal odds: %w", err)
	}
	return &odds, nil
}

// SetOdds caches odds for a selection with a short TTL.
func (c *Cache) SetOdds(ctx context.Context, selectionID string, odds *Odds) error {
	data, err := json.Marshal(odds)
	if err != nil {
		return fmt.Errorf("marshal odds: %w", err)
	}
	return c.client.Set(ctx, oddsKeyPrefix+selectionID, data, oddsTTL).Err()
}

// GetSession retrieves a cached player session.
func (c *Cache) GetSession(ctx context.Context, sessionID string) (map[string]string, error) {
	key := sessionKeyPrefix + sessionID
	result, err := c.client.HGetAll(ctx, key).Result()
	if err != nil {
		return nil, fmt.Errorf("redis hgetall session: %w", err)
	}
	if len(result) == 0 {
		return nil, nil
	}
	return result, nil
}

// SetSession caches a player session.
func (c *Cache) SetSession(ctx context.Context, sessionID string, fields map[string]any) error {
	key := sessionKeyPrefix + sessionID
	pipe := c.client.Pipeline()
	pipe.HSet(ctx, key, fields)
	pipe.Expire(ctx, key, sessionTTL)
	_, err := pipe.Exec(ctx)
	return err
}

// InvalidateSession removes a session from cache (on logout or expiry).
func (c *Cache) InvalidateSession(ctx context.Context, sessionID string) error {
	return c.client.Del(ctx, sessionKeyPrefix+sessionID).Err()
}

// GetCachedLimits retrieves cached player limits.
func (c *Cache) GetCachedLimits(ctx context.Context, cpf string) (*PlayerLimits, error) {
	key := limitsKeyPrefix + cpf
	data, err := c.client.Get(ctx, key).Bytes()
	if err == redis.Nil {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("redis get limits: %w", err)
	}
	var limits PlayerLimits
	if err := json.Unmarshal(data, &limits); err != nil {
		return nil, fmt.Errorf("unmarshal limits: %w", err)
	}
	return &limits, nil
}

// SetCachedLimits caches player limits.
func (c *Cache) SetCachedLimits(ctx context.Context, cpf string, limits *PlayerLimits) error {
	data, err := json.Marshal(limits)
	if err != nil {
		return fmt.Errorf("marshal limits: %w", err)
	}
	return c.client.Set(ctx, limitsKeyPrefix+cpf, data, limitsTTL).Err()
}

// IncrementDailyStake atomically increments a player's stake counter for today.
// Returns the new total.
func (c *Cache) IncrementDailyStake(ctx context.Context, cpf string, amount float64) (float64, error) {
	key := fmt.Sprintf("daily_stake:%s:%s", cpf, time.Now().UTC().Format("2006-01-02"))
	pipe := c.client.Pipeline()
	incrCmd := pipe.IncrByFloat(ctx, key, amount)
	// Expire at midnight
	now := time.Now().UTC()
	midnight := time.Date(now.Year(), now.Month(), now.Day()+1, 0, 0, 0, 0, time.UTC)
	pipe.ExpireAt(ctx, key, midnight)

	if _, err := pipe.Exec(ctx); err != nil {
		return 0, fmt.Errorf("increment daily stake: %w", err)
	}
	return incrCmd.Val(), nil
}

// PublishBetPlaced publishes a bet-placed event to the Redis pub/sub channel.
func (c *Cache) PublishBetPlaced(ctx context.Context, betID string) error {
	return c.client.Publish(ctx, "bets:placed", betID).Err()
}

// Ping checks Redis connectivity.
func (c *Cache) Ping(ctx context.Context) error {
	if c == nil || c.client == nil {
		return fmt.Errorf("redis client not configured")
	}
	return c.client.Ping(ctx).Err()
}
