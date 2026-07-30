// Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * updater.exe — standalone delta-patch updater using electron-updater.
 */
import { app } from "electron";
import { autoUpdater } from "electron-updater";

async function main(): Promise<void> {
  await app.whenReady();
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on("error", (err) => {
    console.error("updater error:", err);
    app.exit(2);
  });
  autoUpdater.on("update-not-available", () => {
    console.log("no update available");
    app.exit(0);
  });
  autoUpdater.on("update-downloaded", () => {
    console.log("update downloaded");
    autoUpdater.quitAndInstall(false, true);
  });

  try {
    await autoUpdater.checkForUpdates();
  } catch (err) {
    console.error("check failed:", err);
    app.exit(3);
  }

  if (process.env.POKER_SMOKE === "1") {
    console.log("SMOKE_UPDATER_READY");
    setTimeout(() => app.exit(0), 500);
  }
}

main();
