//! Server recovery after every observation.
//!
//! The reference lane proves, from a separate connection, that the server no
//! longer runs the worker's statement (tagged transaction listing plus targeted
//! termination). ArcadeDB has no HTTP listing or termination of running
//! statements yet (ArcadeData/arcadedb#9680). Until it does, this records an
//! explicitly unverified cancellation together with the independent scalar
//! probe, and the targeted-termination probe refuses to run. The audit must
//! not admit `cancellation_verified: false` for publication.
use std::time::{Duration, Instant};

use serde_json::{Value, json};

pub(super) const PENDING: &str = "ArcadeData/arcadedb#9680";

pub(super) fn verify(tag: &str) -> Result<Value, &'static str> {
    let started = Instant::now();
    let (mut observer, settings) = super::connect_with_timeout(Duration::from_secs(15))?;
    if super::scalar(&mut observer, &settings, "RETURN 42 AS count")? != 42 {
        return Err("native recovery scalar mismatch");
    }
    Ok(json!({"transaction_tag": tag, "cancellation_verified": false,
        "cancellation_pending": PENDING, "subsequent_scalar": 42,
        "server_recovery_ns": started.elapsed().as_nanos()}))
}

/// The probes run against their own empty database, never the benchmark one.
pub(super) fn probe_database() -> Result<(), &'static str> {
    let (mut connection, settings) = super::connect()?;
    let exists = super::ok(
        connection.request("GET", &format!("/api/v1/exists/{}", settings.database), None, b"")?,
        "ArcadeDB database existence check failed",
    )?;
    if exists.get("result") == Some(&json!(true)) {
        return Ok(());
    }
    super::ok(
        connection.json(
            "/api/v1/server",
            None,
            &json!({"command": format!("create database {}", settings.database)}),
        )?,
        "ArcadeDB probe database creation failed",
    )
    .map(|_| ())
}

pub(super) fn probe() -> Result<(), &'static str> {
    Err("targeted termination needs HTTP list/terminate of running statements (ArcadeData/arcadedb#9680)")
}
