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
	"fmt"
	"time"

	"github.com/google/uuid"
)

// BuilderEngine manages the lifecycle of bet builder betslips: adding
// and removing selections, validating compatibility, and requesting quotes.
type BuilderEngine struct {
	pricing   *BuilderPricingEngine
	catalogue *CompatibilityCatalogue
	config    BuilderConfig
}

// NewBuilderEngine creates a builder engine with the given dependencies.
func NewBuilderEngine(pricing *BuilderPricingEngine, catalogue *CompatibilityCatalogue, config BuilderConfig) *BuilderEngine {
	return &BuilderEngine{
		pricing:   pricing,
		catalogue: catalogue,
		config:    config,
	}
}

// NewBetslip creates a fresh builder betslip for an event.
func (e *BuilderEngine) NewBetslip(eventID string, sport Sport) *BuilderBetslip {
	now := time.Now()
	return &BuilderBetslip{
		ID:        uuid.New().String(),
		EventID:   eventID,
		Sport:     sport,
		CreatedAt: now,
		UpdatedAt: now,
	}
}

// AddSelection adds a selection to a builder betslip after validating
// compatibility with existing selections.
func (e *BuilderEngine) AddSelection(slip *BuilderBetslip, req AddSelectionRequest) error {
	// Must be same event.
	if req.EventID != slip.EventID {
		return fmt.Errorf("selection event %s does not match betslip event %s", req.EventID, slip.EventID)
	}

	// Check max selections.
	if len(slip.Selections) >= e.config.MaxSelections {
		return fmt.Errorf("maximum %d selections reached", e.config.MaxSelections)
	}

	// Check for duplicate market type.
	for _, existing := range slip.Selections {
		if existing.MarketType == req.MarketType {
			return fmt.Errorf("market type %s already in betslip; only one selection per market allowed", req.MarketType)
		}
	}

	// Check for duplicate selection ID.
	for _, existing := range slip.Selections {
		if existing.SelectionID == req.SelectionID {
			return fmt.Errorf("selection %s already in betslip", req.SelectionID)
		}
	}

	// Validate compatibility with all existing selections.
	newSel := BuilderSelection{
		ID:            uuid.New().String(),
		EventID:       req.EventID,
		MarketID:      req.MarketID,
		MarketType:    req.MarketType,
		SelectionID:   req.SelectionID,
		SelectionName: req.SelectionName,
		OddsValue:     req.OddsValue,
		AddedAt:       time.Now(),
	}

	for _, existing := range slip.Selections {
		allowed, reason := e.catalogue.IsCompatible(slip.Sport, existing.MarketType, req.MarketType)
		if !allowed {
			msg := fmt.Sprintf("market %s is incompatible with %s", req.MarketType, existing.MarketType)
			if reason != "" {
				msg += ": " + reason
			}
			return fmt.Errorf(msg)
		}
	}

	// Validate odds floor.
	if req.OddsValue < e.config.MinOddsPerLeg {
		return fmt.Errorf("odds %.2f below minimum %.2f", req.OddsValue, e.config.MinOddsPerLeg)
	}

	slip.Selections = append(slip.Selections, newSel)
	slip.UpdatedAt = time.Now()
	// Invalidate any cached quote.
	slip.Quote = nil
	return nil
}

// RemoveSelection removes a selection from a builder betslip by selection ID.
func (e *BuilderEngine) RemoveSelection(slip *BuilderBetslip, selectionID string) error {
	idx := -1
	for i, sel := range slip.Selections {
		if sel.SelectionID == selectionID {
			idx = i
			break
		}
	}
	if idx == -1 {
		return fmt.Errorf("selection %s not found in betslip", selectionID)
	}

	slip.Selections = append(slip.Selections[:idx], slip.Selections[idx+1:]...)
	slip.UpdatedAt = time.Now()
	slip.Quote = nil
	return nil
}

// GetQuote prices the current betslip selections and caches the quote.
func (e *BuilderEngine) GetQuote(slip *BuilderBetslip, stake float64) BuilderQuote {
	quote := e.pricing.Quote(slip.EventID, slip.Sport, slip.Selections, stake)
	slip.Quote = &quote
	return quote
}

// ValidateBetslip performs full validation on a betslip without pricing.
// Returns a list of errors; empty means valid.
func (e *BuilderEngine) ValidateBetslip(slip *BuilderBetslip) []string {
	var errors []string

	if len(slip.Selections) < e.config.MinSelections {
		errors = append(errors, fmt.Sprintf(
			"minimum %d selections required, got %d", e.config.MinSelections, len(slip.Selections)))
	}
	if len(slip.Selections) > e.config.MaxSelections {
		errors = append(errors, fmt.Sprintf(
			"maximum %d selections allowed, got %d", e.config.MaxSelections, len(slip.Selections)))
	}

	// Validate all selections are same event.
	for _, sel := range slip.Selections {
		if sel.EventID != slip.EventID {
			errors = append(errors, fmt.Sprintf(
				"selection %s belongs to event %s, expected %s",
				sel.SelectionID, sel.EventID, slip.EventID))
		}
	}

	// Check odds floor.
	for i, sel := range slip.Selections {
		if sel.OddsValue < e.config.MinOddsPerLeg {
			errors = append(errors, fmt.Sprintf(
				"selection %d: odds %.2f below minimum %.2f", i+1, sel.OddsValue, e.config.MinOddsPerLeg))
		}
	}

	// Compatibility validation.
	compatErrors := e.catalogue.ValidateSelections(slip.Sport, slip.Selections)
	errors = append(errors, compatErrors...)

	return errors
}

// ApplyTemplate populates a betslip from a template and provided odds.
// The caller must supply odds and selection details for each market in the template.
func (e *BuilderEngine) ApplyTemplate(slip *BuilderBetslip, template BuilderTemplate, selections []AddSelectionRequest) error {
	if len(template.Markets) != len(selections) {
		return fmt.Errorf("template requires %d selections, got %d", len(template.Markets), len(selections))
	}

	// Verify market types match template.
	for i, req := range selections {
		if req.MarketType != template.Markets[i] {
			return fmt.Errorf("selection %d: expected market type %s (from template), got %s",
				i+1, template.Markets[i], req.MarketType)
		}
	}

	// Clear existing selections.
	slip.Selections = nil
	slip.Quote = nil
	slip.UpdatedAt = time.Now()

	// Add each selection.
	for _, req := range selections {
		if err := e.AddSelection(slip, req); err != nil {
			// Rollback on failure.
			slip.Selections = nil
			return fmt.Errorf("applying template: %w", err)
		}
	}

	return nil
}
