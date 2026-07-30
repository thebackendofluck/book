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
 * crash-reporter.exe — minidump capture, upload only with --consent.
 */
import { app, crashReporter } from "electron";

function parseArgs(argv: string[]): { consent: boolean; submitUrl: string } {
  const consent = argv.includes("--consent");
  const urlIdx = argv.indexOf("--url");
  const submitUrl =
    urlIdx >= 0 && argv[urlIdx + 1]
      ? argv[urlIdx + 1]
      : "https://crash.acmetocasino.com/submit";
  return { consent, submitUrl };
}

async function main(): Promise<void> {
  const { consent, submitUrl } = parseArgs(process.argv);

  await app.whenReady();
  crashReporter.start({
    productName: "poker-client",
    submitURL: submitUrl,
    uploadToServer: consent,
    compress: true,
  });

  console.log(
    `crash-reporter started (consent=${consent}, dumps=${app.getPath("crashDumps")})`,
  );

  if (process.env.POKER_SMOKE === "1") {
    console.log("SMOKE_CRASH_REPORTER_READY");
    setTimeout(() => app.exit(0), 500);
    return;
  }

  setTimeout(() => app.exit(0), 1000);
}

main();
