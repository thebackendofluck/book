// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

use hsm_keymgr::keymgr;

use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    name = "hsm-keymgr",
    about = "YubiHSM + AEGIS key management CLI",
    version
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Generate a new data key and wrap it
    Generate {
        /// Label for the key
        #[arg(long)]
        label: String,

        /// Use dev mode (no physical HSM required); requires DEV_MASTER_KEY env var
        #[arg(long)]
        no_hsm: bool,

        /// Output path for the wrapped key blob
        #[arg(long)]
        output: PathBuf,
    },

    /// Unwrap a previously wrapped key
    Unwrap {
        /// Path to the wrapped key blob
        #[arg(long)]
        wrapped: PathBuf,

        /// Output path for the raw (unwrapped) key
        #[arg(long)]
        output: PathBuf,

        /// Use dev mode
        #[arg(long)]
        no_hsm: bool,
    },

    /// Encrypt a file using a key file
    Encrypt {
        /// Path to the (unwrapped) key file
        #[arg(long)]
        key: PathBuf,

        /// Input plaintext file
        #[arg(long)]
        input: PathBuf,

        /// Output ciphertext file
        #[arg(long)]
        output: PathBuf,
    },

    /// Decrypt a file using a key file
    Decrypt {
        /// Path to the (unwrapped) key file
        #[arg(long)]
        key: PathBuf,

        /// Input ciphertext file
        #[arg(long)]
        input: PathBuf,

        /// Output plaintext file
        #[arg(long)]
        output: PathBuf,
    },
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Generate { label, no_hsm, output } => {
            keymgr::cmd_generate(&label, no_hsm, &output)?;
        }
        Commands::Unwrap { wrapped, output, no_hsm } => {
            keymgr::cmd_unwrap(&wrapped, &output, no_hsm)?;
        }
        Commands::Encrypt { key, input, output } => {
            keymgr::cmd_encrypt(&key, &input, &output)?;
        }
        Commands::Decrypt { key, input, output } => {
            keymgr::cmd_decrypt(&key, &input, &output)?;
        }
    }

    Ok(())
}
