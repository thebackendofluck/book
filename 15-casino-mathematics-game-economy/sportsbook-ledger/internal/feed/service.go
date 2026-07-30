// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Package feed fetches messages from the BMC betstream API and stores them.
// Mirrors FeedService.getMessage() from the Scala source.
package feed

import (
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"go.uber.org/zap"
	"sportsbook-ledger/internal/config"
	"sportsbook-ledger/internal/dao"
	"sportsbook-ledger/internal/models"
)

// Service polls the BMC betstream API and persists feed messages and bets.
type Service struct {
	cfg        *config.Config
	feedDAO    *dao.FeedDAO
	betsDAO    *dao.BetsDAO
	httpClient *http.Client
	lastMsgID  int64
	log        *zap.Logger
}

// NewService initialises the feed service with mTLS client certificate authentication.
func NewService(cfg *config.Config, feedDAO *dao.FeedDAO, betsDAO *dao.BetsDAO, log *zap.Logger) (*Service, error) {
	tlsCfg, err := buildTLSConfig(cfg.CertPath)
	if err != nil {
		return nil, fmt.Errorf("build TLS config: %w", err)
	}
	client := &http.Client{
		Timeout:   time.Duration(cfg.BMCHTTPTimeoutMs) * time.Millisecond,
		Transport: &http.Transport{TLSClientConfig: tlsCfg},
	}
	return &Service{
		cfg:        cfg,
		feedDAO:    feedDAO,
		betsDAO:    betsDAO,
		httpClient: client,
		log:        log,
	}, nil
}

// GetMessages fetches the next batch from BMC, filters by jurisdiction, and persists.
// Returns the list of persisted FeedMessages.
func (s *Service) GetMessages(batchSize int) ([]models.FeedMessage, error) {
	startFrom := s.cfg.StartFrom
	if startFrom > s.lastMsgID {
		s.log.Debug("setting lastMsgID from config", zap.Int64("startFrom", startFrom))
		s.lastMsgID = startFrom
	}

	raw, err := s.callBMC(batchSize, s.lastMsgID)
	if err != nil {
		return nil, fmt.Errorf("callBMC: %w", err)
	}

	var envelope map[string]interface{}
	if err := json.Unmarshal([]byte(raw), &envelope); err != nil {
		return nil, fmt.Errorf("parse BMC response: %w", err)
	}

	messages, _ := envelope["messages"].([]interface{})
	jurisdictions := s.cfg.JurisdictionList()
	filterEnabled := s.cfg.JurisdictionFiltering

	var slipsToSave []models.FeedMessage
	var maxMsgID int64

	for _, msg := range messages {
		m, _ := msg.(map[string]interface{})
		channelID, _ := m["channelId"].(float64)
		regulation, _ := m["regulation"].(string)

		// Skip channel 7 (internal/virtual) and non-matching jurisdictions
		if int(channelID) == 7 {
			continue
		}
		if filterEnabled && !jurisdictions[regulation] {
			continue
		}

		fm, err := ParseFeedMessage(m)
		if err != nil {
			s.log.Error("failed to parse feed message", zap.Error(err))
			continue
		}
		slipsToSave = append(slipsToSave, *fm)
		if fm.MessageID > maxMsgID {
			maxMsgID = fm.MessageID
		}

		// Extract individual bets from combination data
		if combo, ok := m["combination"].(map[string]interface{}); ok {
			bets := ExtractBets(combo, fm.MessageID)
			if err := s.betsDAO.Insert(bets); err != nil {
				s.log.Error("failed to insert bets", zap.Error(err))
			}
		}
	}

	if err := s.feedDAO.Insert(slipsToSave); err != nil {
		return nil, fmt.Errorf("insert feed messages: %w", err)
	}

	if maxMsgID > s.lastMsgID {
		s.lastMsgID = maxMsgID
	}
	s.log.Debug("getMessage complete", zap.Int64("lastMsgID", s.lastMsgID))
	return slipsToSave, nil
}

func (s *Service) callBMC(batchSize int, lastMsgID int64) (string, error) {
	startFrom := lastMsgID
	if startFrom == 0 {
		if last, err := s.feedDAO.GetLastMessage(); err == nil && last != nil {
			startFrom = last.MessageID
		} else {
			startFrom = 1
		}
	}

	url := fmt.Sprintf("%s/from/%d?batchSize=%d", s.cfg.BMCURL, startFrom, batchSize)
	s.log.Info("calling BMC", zap.String("url", url))

	resp, err := s.httpClient.Get(url)
	if err != nil {
		return "", fmt.Errorf("HTTP GET: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("read body: %w", err)
	}
	return string(body), nil
}

func buildTLSConfig(certPath string) (*tls.Config, error) {
	cert, err := tls.LoadX509KeyPair(certPath, certPath)
	if err != nil {
		return nil, fmt.Errorf("load cert %s: %w", certPath, err)
	}
	return &tls.Config{Certificates: []tls.Certificate{cert}}, nil
}
