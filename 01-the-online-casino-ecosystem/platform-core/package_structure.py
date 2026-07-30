# Companion code for "The Backend of Luck" - Chapter 01, The Online Casino Ecosystem.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Platform Package Structure Overview
# Source: Production casino platform (sanitized)
# Chapter 1 - The Online Casino Ecosystem
#
# This file illustrates how a real-world multi-brand casino platform
# organizes its codebase. The package hierarchy reflects the domain:
# game suppliers, accounts, gateways, compliance, and integrations.
# =============================================================================

# ---------------------------------------------------------------------------
# TOP-LEVEL PACKAGE LAYOUT
# ---------------------------------------------------------------------------
#
# platform/
# +-- acmetocasino/
# |   +-- gameservice/              <-- Game supplier integration layer
# |   |   +-- accounts/             <-- Internal accounts provider
# |   |   +-- suppliers/            <-- Per-supplier implementations
# |   |       +-- evolution/        <-- Evolution Gaming (live casino)
# |   |       +-- netent/           <-- NetEnt (slots)
# |   |       +-- microgaming/      <-- Microgaming (slots/table)
# |   |       +-- pragmaticplay/    <-- Pragmatic Play
# |   |       +-- igt/              <-- IGT (International Game Technology)
# |   |       |   +-- handler/
# |   |       |       +-- init/
# |   |       |       +-- play/
# |   |       |       +-- get_player_balance/
# |   |       |       +-- end_game_session/
# |   |       |       +-- heartbeat/
# |   |       +-- blueprint/        <-- Blueprint Gaming
# |   |       +-- redtiger/         <-- Red Tiger
# |   |       +-- playngo/          <-- Play'n GO
# |   |       +-- quickspin/        <-- Quickspin
# |   |       +-- relax/            <-- Relax Gaming
# |   +-- platform/                 <-- Core platform services
# |   |   +-- accounts/             <-- Transaction processing, balances
# |   |   +-- brand/                <-- Multi-brand configuration
# |   |   +-- bonus/                <-- Bonus engine
# |   |   +-- compliance/           <-- Regulatory compliance
# |   |   |   +-- alert/
# |   |   +-- config/               <-- Hierarchical settings system
# |   |   +-- currency/             <-- Multi-currency support
# |   |   +-- database/             <-- Data access layer
# |   |   |   +-- brands/
# |   |   |   +-- players/
# |   |   |   +-- currency/
# |   |   |   +-- affiliates/
# |   |   +-- integration/          <-- External service integrations
# |   |   |   +-- payment/          <-- Payment providers
# |   |   |   |   +-- adyen/
# |   |   |   |   +-- trustly/
# |   |   |   |   +-- pxp/
# |   |   |   +-- shuftipro/       <-- KYC verification
# |   |   |   +-- transunion/       <-- Identity checks
# |   |   +-- jurisdictions/        <-- Per-jurisdiction logic
# |   |   |   +-- sweden/
# |   |   |   +-- germany/
# |   |   |   +-- usa/
# |   |   |   +-- finland/
# |   |   |   +-- latam/
# |   |   |   +-- india/
# |   |   +-- launch/               <-- Game launch orchestration
# |   |   +-- messaging/            <-- Kafka event system
# |   |   +-- multistate/           <-- Hub/Spoke architecture
# |   |   |   +-- hub/
# |   |   |   +-- spoke/
# |   |   |   +-- accounts/
# |   |   +-- registration/         <-- User registration
# |   |   +-- security/             <-- Auth, tokens, sessions
# |   |   |   +-- kafka/            <-- Session replication
# |   |   +-- userservices/         <-- User-facing services
# |   |   |   +-- accounts/
# |   |   |   +-- auth/
# |   |   |   +-- bonus/
# |   |   |   +-- deposit/
# |   |   |   +-- withdraw/
# |   |   |   +-- kyc/
# |   |   |   +-- responsiblegambling/
# |   |   |   +-- loyalty/
# |   |   |   +-- leaderboard/
# |   |   +-- webapp/               <-- HTTP endpoints, health checks
# |   +-- security/                 <-- Authentication framework
# |   +-- util/                     <-- Shared utilities

# ---------------------------------------------------------------------------
# KEY ARCHITECTURAL OBSERVATIONS
# ---------------------------------------------------------------------------
#
# 1. SINGLE-LANGUAGE CODEBASE: Platform is Python throughout.
#    FastAPI for HTTP services, kafka-python for Kafka consumers,
#    SQLAlchemy for database access.
#
# 2. SUPPLIER ISOLATION: Each game supplier gets its own package with
#    dedicated endpoint, game_launcher, settings, and free_rounds modules.
#    This prevents one supplier's quirks from affecting others.
#
# 3. JURISDICTION-AWARE: Separate packages per jurisdiction (Sweden,
#    Germany, USA, etc.) handle market-specific compliance rules
#    like reality checks, deposit limits, and tax reporting.
#
# 4. HUB/SPOKE TOPOLOGY: The multistate package supports running
#    a central Hub instance with jurisdiction-specific Spoke instances,
#    connected via Kafka for event propagation.
#
# 5. SETTINGS HIERARCHY: Configuration flows from
#    Global -> Jurisdiction -> Brand -> Brand+Jurisdiction -> Brand+Country
#    allowing fine-grained overrides per market and operator.
