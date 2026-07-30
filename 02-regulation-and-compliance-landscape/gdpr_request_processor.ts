// Companion code for "The Backend of Luck" - Chapter 02, Regulation and Compliance Landscape.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// =============================================================================
// REGULATORY REQUIREMENT: GDPR (EU) — Art. 12, 15, 17, 20
// Regulation:  GDPR (EU) 2016/679 Art. 12 — Transparent information; Art. 15 —
//              Right of Access; Art. 17 — Right to Erasure; Art. 20 — Portability
//              UK GDPR + Data Protection Act 2018 (identical obligations)
//              LGPD Lei No. 13.709/2018 Art. 18 (Brazilian data subjects)
// Purpose:     Automated DSAR processing service. Polls for pending requests,
//              runs all 7 data domain extractions, and marks requests complete.
//              This service is the operational backbone for meeting GDPR deadlines:
//                30-day hard deadline (GDPR Art. 12(3))
//                90-day absolute maximum (with player notification of extension)
//              Operators who fail DSAR deadlines face direct ICO/IDPC enforcement.
// Polling:     minutesBetweenChecks config controls polling interval — should be
//              ≤ 60 minutes to ensure timely processing and avoid deadline breaches
// Penalty:     GDPR Art. 83(4): up to €10M or 2% global annual turnover for
//              Art. 12 procedural failures (e.g., missed deadlines)
//              Art. 83(5): up to €20M or 4% for systemic rights failures
// Jurisdictions: All EU/EEA, UK, Brazil (LGPD 15-day deadline is stricter)
//
// References:
//   GDPR Full Text: https://gdpr-info.eu/
//   Art. 15 (Right of Access): https://gdpr-info.eu/art-15-gdpr/
//   Art. 17 (Right to Erasure): https://gdpr-info.eu/art-17-gdpr/
//   Art. 20 (Data Portability): https://gdpr-info.eu/art-20-gdpr/
//   Art. 83 (Penalties): https://gdpr-info.eu/art-83-gdpr/
//   UK GDPR: https://www.legislation.gov.uk/uksi/2019/419/contents
//   LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/L13709.htm
// =============================================================================
// Chapter 02 - Regulation & Compliance Landscape
// GDPR Data Subject Access Request (DSAR) Service
//
// Under GDPR Article 15, players have the right to request all personal data
// held by the operator. This Node.js/TypeScript service automates the process:
//   1. Polls for pending DSAR requests in the backoffice database
//   2. Executes 7 SQL extracts covering all player data domains
//   3. Converts results to CSV and stores them for download
//   4. Marks the request as completed
//
// Data domains extracted: account details, transaction history, gaming stats,
// deposits, withdrawals, account locks, and communications.

import { PlatformDataProcessor } from './platformDataProcessor'
import { RequestProvider } from './requestProvider'

export class App {

    private _requestProvider: RequestProvider;
    private _platformDataProcessor: PlatformDataProcessor;
    private readonly _waitPeriod: number;

    constructor(private _settings: AppSettings) {
        this._waitPeriod = _settings.minutesBetweenChecks * 60 * 1000;
    }

    async Run() {
        this._platformDataProcessor = await PlatformDataProcessor.CreateAsync({
            platformDbConnectionSettings: this._settings.platformDbConnectionSettings,
            platformSqlPath: __dirname + '/../src/sql/platform'
        });

        this._requestProvider = new RequestProvider({
            platformDbConnectionSettings: this._settings.platformDbConnectionSettings
        });

        await this.processRequests();
    }

    // -----------------------------------------------------------------------
    // Main processing loop: poll for pending GDPR requests, extract all
    // player data domains, store results, and mark complete.
    // -----------------------------------------------------------------------
    private async processRequests() {
        console.log("Processing GDPR data subject access requests..");
        let requests = await this._requestProvider.GetRequests();

        console.log(`Found ${requests.length} pending DSAR requests`);

        for (let request of requests) {
            console.log(`Processing DSAR request id ${request.id} for user ${request.userId}`);

            let result: IDataResult[] = [];

            if (request.dataType == 'csv') {
                // Extract all 7 data domains as CSV
                for await (let r of this._platformDataProcessor.GetAllDataCsv(request.userId)) {
                    result.push(r);
                }
            }

            // Store CSV extracts and mark request completed
            await this._requestProvider.CompleteRequest(request, result);
            console.log(`DSAR request ${request.id} complete (${result.length} extracts)`);
        }

        console.log(`Waiting ${this._settings.minutesBetweenChecks} minutes before next check...`);
        setTimeout(() => this.processRequests(), this._waitPeriod);
    }
}

export interface AppSettings {
    platformDbConnectionSettings: {
        server: string;
        username: string;
        password: string;
    },
    minutesBetweenChecks: number
}
