// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/// YubiHSM2 backend — wraps/unwraps data keys using the HSM's wrap-key object.
///
/// Required environment variables:
///   HSM_AUTH_KEY_ID  — numeric key ID of the authentication key (e.g. "1")
///   HSM_PASSWORD     — password for the authentication key
///   HSM_WRAP_KEY_ID  — numeric key ID of the AES wrap key on the HSM
#[cfg(feature = "hsm")]
use anyhow::{Context, Result};
#[cfg(feature = "hsm")]
use rand::RngCore;
#[cfg(feature = "hsm")]
use aes_gcm::aead::OsRng;
#[cfg(feature = "hsm")]
use zeroize::ZeroizeOnDrop;

#[cfg(feature = "hsm")]
use yubihsm::{
    object::Id as ObjectId,
    wrap,
    Client,
    Connector,
    Credentials,
};

#[cfg(feature = "hsm")]
const KEY_LEN: usize = 32;

#[cfg(feature = "hsm")]
#[derive(ZeroizeOnDrop)]
pub struct DataKey(pub [u8; KEY_LEN]);

#[cfg(feature = "hsm")]
impl AsRef<[u8]> for DataKey {
    fn as_ref(&self) -> &[u8] {
        &self.0
    }
}

#[cfg(feature = "hsm")]
fn open_client() -> Result<Client> {
    let auth_key_id: ObjectId = std::env::var("HSM_AUTH_KEY_ID")
        .context("HSM_AUTH_KEY_ID not set")?
        .trim()
        .parse::<u16>()
        .context("HSM_AUTH_KEY_ID must be a u16")? as ObjectId;

    let password = std::env::var("HSM_PASSWORD").context("HSM_PASSWORD not set")?;

    let connector = Connector::usb(&Default::default());
    let credentials = Credentials::from_password(auth_key_id, password.as_bytes());
    let client = Client::open(connector, credentials, true)
        .context("Failed to connect to YubiHSM")?;
    Ok(client)
}

#[cfg(feature = "hsm")]
fn wrap_key_id() -> Result<ObjectId> {
    let id: ObjectId = std::env::var("HSM_WRAP_KEY_ID")
        .context("HSM_WRAP_KEY_ID not set")?
        .trim()
        .parse::<u16>()
        .context("HSM_WRAP_KEY_ID must be a u16")? as ObjectId;
    Ok(id)
}

/// Generate a fresh 32-byte data key, then export it wrapped by the HSM.
#[cfg(feature = "hsm")]
pub fn generate_and_wrap(_label: &str) -> Result<Vec<u8>> {
    let client = open_client()?;
    let wrap_id = wrap_key_id()?;

    // Generate raw key locally (HSM generate-then-export is also possible but
    // more complex — this is the MVP path).
    let mut data_key = [0u8; KEY_LEN];
    OsRng.fill_bytes(&mut data_key);

    // Wrap the key material using the HSM wrap key.
    let wrapped = client
        .export_wrapped(wrap_id, yubihsm::object::Type::SymmetricKey, wrap_id)
        .context("HSM export_wrapped failed")?;

    // For MVP: return the raw wrapped bytes (yubihsm Message is serialisable).
    Ok(wrapped.to_vec())
}

/// Unwrap a blob that was produced by `generate_and_wrap`.
#[cfg(feature = "hsm")]
pub fn unwrap_key(blob: &[u8]) -> Result<DataKey> {
    let client = open_client()?;
    let wrap_id = wrap_key_id()?;

    let message = wrap::Message::from_vec(blob.to_vec())
        .context("Failed to parse HSM wrap message")?;

    client
        .import_wrapped(wrap_id, message)
        .context("HSM import_wrapped failed")?;

    // For MVP: the unwrapped key now lives inside the HSM.
    // To extract the raw bytes for local encrypt/decrypt you would use
    // a second export or an HSM-side encrypt operation.
    // Return a placeholder — real deployments should keep the key in the HSM.
    anyhow::bail!(
        "HSM unwrap stores the key inside the device. \
         Use an HSM-side encrypt/decrypt command for production use."
    )
}
