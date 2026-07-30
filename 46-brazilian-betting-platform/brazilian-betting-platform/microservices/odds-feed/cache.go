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
	eventKeyPrefix  = "event:"
	sportKeyPrefix  = "sport:"
	liveOddsChannel = "odds:live"
	eventTTL        = 24 * time.Hour
	liveEventTTL    = 5 * time.Minute
)

// Cache provides Redis-backed storage and pub/sub for the odds feed.
type Cache struct {
	client *redis.Client
}

// NewCache creates a new Cache.
func NewCache(client *redis.Client) *Cache {
	return &Cache{client: client}
}

// SetEvent stores an event in Redis.
// Live events use a shorter TTL to ensure stale data is purged promptly.
func (c *Cache) SetEvent(ctx context.Context, event *Event) error {
	data, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("marshal event: %w", err)
	}

	ttl := eventTTL
	if event.Status == EventStatusLive {
		ttl = liveEventTTL
	}

	key := eventKeyPrefix + event.ID
	return c.client.Set(ctx, key, data, ttl).Err()
}

// GetEvent retrieves an event by ID.
func (c *Cache) GetEvent(ctx context.Context, id string) (*Event, error) {
	data, err := c.client.Get(ctx, eventKeyPrefix+id).Bytes()
	if err == redis.Nil {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("redis get event: %w", err)
	}
	var event Event
	if err := json.Unmarshal(data, &event); err != nil {
		return nil, fmt.Errorf("unmarshal event: %w", err)
	}
	return &event, nil
}

// GetEventsBySpory retrieves all events for a given sport from a Redis set.
func (c *Cache) GetEventsBySport(ctx context.Context, sport Sport) ([]Event, error) {
	key := sportKeyPrefix + string(sport)
	ids, err := c.client.SMembers(ctx, key).Result()
	if err != nil {
		return nil, fmt.Errorf("smembers sport events: %w", err)
	}

	var events []Event
	for _, id := range ids {
		ev, err := c.GetEvent(ctx, id)
		if err != nil || ev == nil {
			// Remove stale reference from set.
			c.client.SRem(ctx, key, id) //nolint:errcheck
			continue
		}
		events = append(events, *ev)
	}
	return events, nil
}

// AddEventToSport adds an event ID to the sport index set.
func (c *Cache) AddEventToSport(ctx context.Context, sport Sport, eventID string) error {
	key := sportKeyPrefix + string(sport)
	return c.client.SAdd(ctx, key, eventID).Err()
}

// GetLiveEvents returns all currently live events across all sports.
func (c *Cache) GetLiveEvents(ctx context.Context) ([]Event, error) {
	pattern := eventKeyPrefix + "*"
	var cursor uint64
	var events []Event

	for {
		keys, nextCursor, err := c.client.Scan(ctx, cursor, pattern, 100).Result()
		if err != nil {
			return nil, fmt.Errorf("scan events: %w", err)
		}

		for _, key := range keys {
			data, err := c.client.Get(ctx, key).Bytes()
			if err != nil {
				continue
			}
			var ev Event
			if err := json.Unmarshal(data, &ev); err != nil {
				continue
			}
			if ev.Status == EventStatusLive {
				events = append(events, ev)
			}
		}

		cursor = nextCursor
		if cursor == 0 {
			break
		}
	}
	return events, nil
}

// UpdateOdds updates the odds for a specific selection within an event.
//
// The read-modify-write runs inside a Redis WATCH/MULTI transaction so two
// concurrent updates for the same event can't race each other: without this,
// an update that read the event before a market was suspended could still
// write its (now stale) odds back after the suspension, silently resurrecting
// a market that was just closed. Two more guards run inside the same
// transaction: updates whose UpdatedAt is not newer than what's already
// stored are rejected as out-of-order/duplicate, and updates targeting a
// closed market or inactive selection are rejected outright instead of
// being applied.
func (c *Cache) UpdateOdds(ctx context.Context, update *OddsUpdate) error {
	key := eventKeyPrefix + update.EventID

	txf := func(tx *redis.Tx) error {
		data, err := tx.Get(ctx, key).Bytes()
		if err == redis.Nil {
			return fmt.Errorf("event %s not found", update.EventID)
		}
		if err != nil {
			return fmt.Errorf("redis get event: %w", err)
		}
		var event Event
		if err := json.Unmarshal(data, &event); err != nil {
			return fmt.Errorf("unmarshal event: %w", err)
		}

		// Reject updates that are not newer than what's already stored, so
		// a delayed or duplicate feed message can't clobber fresher state.
		if !update.UpdatedAt.After(event.UpdatedAt) {
			return fmt.Errorf("stale odds update for event %s: update.updated_at=%s is not newer than stored updated_at=%s",
				update.EventID, update.UpdatedAt, event.UpdatedAt)
		}

		updated := false
		for mi, market := range event.Markets {
			if market.ID != update.MarketID {
				continue
			}
			if !market.IsOpen {
				return fmt.Errorf("market %s is suspended, rejecting odds update", update.MarketID)
			}
			for si, sel := range market.Selections {
				if sel.ID != update.SelectionID {
					continue
				}
				if !sel.IsActive {
					return fmt.Errorf("selection %s is inactive, rejecting odds update", update.SelectionID)
				}
				oldOdds := event.Markets[mi].Selections[si].Odds
				event.Markets[mi].Selections[si].Odds = update.NewOdds
				event.Markets[mi].Selections[si].OddsMovement = update.NewOdds - oldOdds
				event.Markets[mi].UpdatedAt = update.UpdatedAt
				updated = true
			}
		}

		if !updated {
			return fmt.Errorf("selection %s not found in event %s", update.SelectionID, update.EventID)
		}

		event.UpdatedAt = update.UpdatedAt
		newData, err := json.Marshal(&event)
		if err != nil {
			return fmt.Errorf("marshal event: %w", err)
		}

		ttl := eventTTL
		if event.Status == EventStatusLive {
			ttl = liveEventTTL
		}

		_, err = tx.TxPipelined(ctx, func(pipe redis.Pipeliner) error {
			pipe.Set(ctx, key, newData, ttl)
			return nil
		})
		return err
	}

	if err := c.client.Watch(ctx, txf, key); err != nil {
		return err
	}

	// Publish update to live subscribers only after the transaction commits.
	return c.PublishOddsUpdate(ctx, update)
}

// PublishOddsUpdate publishes an odds change to the Redis pub/sub channel.
func (c *Cache) PublishOddsUpdate(ctx context.Context, update *OddsUpdate) error {
	msg := LiveOddsMessage{
		Type:        "odds_update",
		EventID:     update.EventID,
		MarketID:    update.MarketID,
		SelectionID: update.SelectionID,
		Odds:        update.NewOdds,
		Timestamp:   update.UpdatedAt,
	}
	data, err := json.Marshal(msg)
	if err != nil {
		return fmt.Errorf("marshal live odds message: %w", err)
	}
	return c.client.Publish(ctx, liveOddsChannel, data).Err()
}

// Subscribe returns a Redis pub/sub subscription to the live odds channel.
func (c *Cache) Subscribe(ctx context.Context) *redis.PubSub {
	return c.client.Subscribe(ctx, liveOddsChannel)
}

// Ping checks Redis connectivity.
func (c *Cache) Ping(ctx context.Context) error {
	if c == nil || c.client == nil {
		return fmt.Errorf("redis client not configured")
	}
	return c.client.Ping(ctx).Err()
}

// GetAllEvents returns all events matching a key pattern (for feed listing).
func (c *Cache) GetAllEvents(ctx context.Context) ([]Event, error) {
	pattern := eventKeyPrefix + "*"
	var cursor uint64
	var events []Event

	for {
		keys, nextCursor, err := c.client.Scan(ctx, cursor, pattern, 100).Result()
		if err != nil {
			return nil, err
		}
		for _, key := range keys {
			data, err := c.client.Get(ctx, key).Bytes()
			if err != nil {
				continue
			}
			var ev Event
			if err := json.Unmarshal(data, &ev); err != nil {
				continue
			}
			events = append(events, ev)
		}
		cursor = nextCursor
		if cursor == 0 {
			break
		}
	}
	return events, nil
}
