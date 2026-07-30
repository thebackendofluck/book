// Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # iGaming HSM Platform — Service Entry Point
//!
//! ## Startup sequence
//!
//! ```text
//! 1. HsmClient::new()           → connect to yubihsm-connector via PKCS#11
//! 2. CertifiedRng::run_nist_self_test()
//!                               → statistical sanity check (software only)
//! 3. SeedPool::new().warmup()   → 1 HSM call: generate 1 000 seeds in batch
//! 4. EpochManager::new()        → 1 HSM call: derive KeyHierarchy via HKDF
//! 5. EpochManager::rotate_if_expired()
//!                               → rotate immediately if epoch expired at rest
//! 6. SessionKeyManager::new()   → 2 HSM calls: Ed25519 seed + endorsement
//! 7. AuditChain::new()          → connect DB + epoch + HSM
//! 8. WalletEngine::new()        → connect DB + HSM
//! 9. Background schedulers      → epoch rotation (30d), session rotation (1h)
//! 10. axum::serve()             → ready to serve — 0 HSM calls per request
//! ```
//!
//! **Total HSM calls at startup: ≈ 4** (seed pool + key hierarchy + session key)
//!
//! **HSM calls during normal operation:**
//! - Epoch rotation: 1× every 30 days
//! - Session rotation: 1× every hour per instance
//! - Audit checkpoint: 1× every 1 000 transactions
//! - Seed pool refill: 1× every ~950 spins (async, off critical path)
//!
//! ## Graceful shutdown
//!
//! On `SIGTERM` or `SIGINT` the Axum server stops accepting new connections,
//! drains in-flight requests (30-second timeout), then drops all service
//! handles. `ZeroizeOnDrop` on all key material ensures that epoch keys,
//! session signing keys, and seed pool contents are zeroed before the process
//! exits.
//!
//! ## Environment variables
//!
//! | Variable              | Default                    | Description                          |
//! |-----------------------|----------------------------|--------------------------------------|
//! | `PKCS11_LIB`          | (required)                 | Path to yubihsm_pkcs11.so            |
//! | `HSM_SLOT`            | `0`                        | PKCS#11 slot index                   |
//! | `HSM_PIN`             | (required)                 | YubiHSM auth PIN (`0001<password>`)  |
//! | `DATABASE_URL`        | (required)                 | PostgreSQL connection string         |
//! | `BIND_ADDR`           | `0.0.0.0:8080`             | TCP bind address                     |
//! | `SEED_POOL_MAX`       | `1000`                     | Maximum seed pool size               |
//! | `AUDIT_BATCH_SIZE`    | `1000`                     | Audit entries per HSM checkpoint     |

mod hsm;
mod rng;
mod wallet;

use std::{net::SocketAddr, sync::Arc};

use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::get,
    Json, Router,
};
use serde_json::json;
use sqlx::postgres::PgPoolOptions;
use tokio::signal;
use tower_http::{timeout::TimeoutLayer, trace::TraceLayer};

use crate::{
    hsm::{
        audit::AuditChain,
        epoch::EpochManager,
        seed_pool::SeedPool,
        session::SessionKeyManager,
        HsmClient,
    },
    rng::CertifiedRng,
    wallet::WalletEngine,
};

// ─────────────────────────────────────────────────────────────────────────────
// Shared application state
// ─────────────────────────────────────────────────────────────────────────────

/// State injected into every Axum handler via `State<Arc<AppState>>`.
///
/// All fields are `Arc`-wrapped so that cloning is cheap and the data lives
/// as long as the service is running. When the `AppState` is dropped (on
/// shutdown) all `ZeroizeOnDrop` fields are zeroed.
pub struct AppState {
    pub epoch:   Arc<EpochManager>,
    pub rng:     CertifiedRng,
    pub wallet:  WalletEngine,
    pub audit:   Arc<tokio::sync::Mutex<AuditChain>>,
    pub session: Arc<SessionKeyManager>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // ── Observability ─────────────────────────────────────────────────────────
    tracing_subscriber::fmt()
        .json()
        .with_target(true)
        .with_thread_ids(true)
        .init();

    tracing::info!(
        version = env!("CARGO_PKG_VERSION"),
        "iGaming HSM platform starting"
    );

    // ── Environment ───────────────────────────────────────────────────────────
    let pkcs11_lib  = std::env::var("PKCS11_LIB")
        .expect("PKCS11_LIB must be set to the yubihsm_pkcs11.so path");
    let hsm_slot: u64 = std::env::var("HSM_SLOT")
        .unwrap_or_else(|_| "0".into())
        .parse()
        .expect("HSM_SLOT must be a non-negative integer");
    let hsm_pin = std::env::var("HSM_PIN")
        .expect("HSM_PIN must be set (format: 0001<password>)");
    let database_url = std::env::var("DATABASE_URL")
        .expect("DATABASE_URL must be set");
    let bind_addr: SocketAddr = std::env::var("BIND_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:8080".into())
        .parse()
        .expect("BIND_ADDR must be a valid socket address");
    let seed_pool_max: usize = std::env::var("SEED_POOL_MAX")
        .unwrap_or_else(|_| "1000".into())
        .parse()
        .unwrap_or(1000);
    let audit_batch_size: usize = std::env::var("AUDIT_BATCH_SIZE")
        .unwrap_or_else(|_| "1000".into())
        .parse()
        .unwrap_or(1000);

    // ── Step 1: Connect to YubiHSM 2 ─────────────────────────────────────────
    // This establishes the PKCS#11 session and verifies the PIN. If the HSM is
    // not reachable (e.g. yubihsm-connector not running) the process panics
    // here rather than starting in a degraded state.
    tracing::info!("connecting to YubiHSM 2 via PKCS#11");
    let hsm = HsmClient::new(&pkcs11_lib, hsm_slot, &hsm_pin)?;
    tracing::info!("YubiHSM 2 connected");

    // ── Step 2: NIST SP 800-22 self-test ─────────────────────────────────────
    // Runs in software against a fixed seed — does not require hardware.
    // A failure here is a software regression, not a hardware fault.
    CertifiedRng::run_nist_self_test()?;

    // ── Step 4: Derive key hierarchy (EpochManager) ───────────────────────────
    // One HSM call (64 bytes TRNG → HKDF → 6 sub-keys). The epoch manager
    // wraps the key hierarchy and handles rotation.
    tracing::info!("initialising epoch manager");
    let epoch = EpochManager::new(hsm.clone()).await?;

    // ── Step 3: Warm the seed pool ────────────────────────────────────────────
    // One HSM call for `seed_pool_max * 32` bytes of TRNG. After this, game
    // sessions acquire seeds with zero HSM calls.
    tracing::info!("warming seed pool");
    let pool = SeedPool::new(hsm.clone(), Arc::clone(&epoch));
    pool.warmup().await?;

    // ── Step 5: Rotate epoch if it expired while the service was down ─────────
    let rotated = epoch.rotate_if_expired().await?;
    if rotated {
        tracing::warn!("epoch was expired at startup — rotated immediately");
    }

    // ── Step 6: Session key manager ───────────────────────────────────────────
    // Two HSM calls: TRNG seed + ECDSA endorsement.
    tracing::info!("initialising session key manager");
    let epoch_id = epoch.current_epoch_id().await;
    let session = SessionKeyManager::new(hsm.clone(), epoch_id).await?;

    // ── Step 7: Audit chain ───────────────────────────────────────────────────
    tracing::info!("connecting to PostgreSQL");
    let db = PgPoolOptions::new()
        .max_connections(20)
        .connect(&database_url)
        .await?;

    let audit = Arc::new(tokio::sync::Mutex::new(AuditChain::new(
        hsm.clone(),
        Arc::clone(&epoch),
        db.clone(),
        audit_batch_size,
    )));

    // ── Step 8: Wallet engine ─────────────────────────────────────────────────
    let wallet = WalletEngine {
        db:  db.clone(),
        hsm: hsm.clone(),
    };

    // ── Step 9: Certified RNG ─────────────────────────────────────────────────
    let rng = CertifiedRng::new(pool, Arc::clone(&epoch));

    // ── Step 9b: Start background schedulers ─────────────────────────────────
    EpochManager::start_rotation_scheduler(Arc::clone(&epoch));
    SessionKeyManager::start_rotation(Arc::clone(&session), 3600);

    // ── Step 10: Build and serve ──────────────────────────────────────────────
    let state = Arc::new(AppState {
        epoch,
        rng,
        wallet,
        audit,
        session,
    });

    let app = Router::new()
        .route("/healthz",  get(health_handler))
        .route("/readyz",   get(readyz_handler))
        .with_state(state)
        .layer(TraceLayer::new_for_http())
        .layer(TimeoutLayer::new(std::time::Duration::from_secs(30)));

    tracing::info!(addr = %bind_addr, "service ready — listening");

    let listener = tokio::net::TcpListener::bind(bind_addr).await?;

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;

    tracing::info!("service shut down cleanly");
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Health endpoints
// ─────────────────────────────────────────────────────────────────────────────

/// Liveness probe — returns 200 if the process is alive.
///
/// Used by Kubernetes/systemd to detect crashes. Does not check HSM or DB
/// connectivity; those are checked by `/readyz`.
async fn health_handler() -> impl IntoResponse {
    (
        StatusCode::OK,
        Json(json!({
            "status":  "ok",
            "service": "igaming-hsm-platform",
            "version": env!("CARGO_PKG_VERSION"),
        })),
    )
}

/// Readiness probe — returns 200 only when all dependencies are healthy.
///
/// Checks:
/// - HSM connectivity (attempts a 1-byte TRNG read).
/// - Database connectivity (1-row ping query).
///
/// Kubernetes uses this to withhold traffic until the service is ready. If the
/// HSM is unavailable (e.g. USB disconnect) the probe fails and traffic is
/// routed to healthy replicas.
async fn readyz_handler(
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    // Check database.
    let db_ok = sqlx::query_scalar::<_, i64>("SELECT 1")
        .fetch_one(&state.wallet.db)
        .await
        .is_ok();

    // Check HSM (1 byte TRNG read).
    let hsm_ok = state.wallet.hsm.random_bytes(1).await.is_ok();

    if db_ok && hsm_ok {
        (
            StatusCode::OK,
            Json(json!({
                "status":  "ready",
                "db":      "ok",
                "hsm":     "ok",
            })),
        )
    } else {
        tracing::error!(db_ok, hsm_ok, "readiness check failed");
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({
                "status": "not ready",
                "db":     if db_ok  { "ok" } else { "error" },
                "hsm":    if hsm_ok { "ok" } else { "error" },
            })),
        )
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Graceful shutdown
// ─────────────────────────────────────────────────────────────────────────────

/// Wait for SIGTERM or SIGINT, then signal Axum to drain in-flight requests.
///
/// On receipt of the signal, Axum stops accepting new connections and waits
/// up to 30 seconds (set in the `TimeoutLayer`) for in-flight requests to
/// complete. After that, the `AppState` `Arc` drops and all `ZeroizeOnDrop`
/// fields are zeroed.
async fn shutdown_signal() {
    let ctrl_c = async {
        signal::ctrl_c()
            .await
            .expect("failed to install SIGINT handler");
    };

    #[cfg(unix)]
    let sigterm = async {
        signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let sigterm = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c  => { tracing::info!("received SIGINT — shutting down"); },
        _ = sigterm => { tracing::info!("received SIGTERM — shutting down"); },
    }
}
