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
// REGULATORY REQUIREMENT: GDPR (EU) + UK GDPR + LGPD (Brazil) + PIPEDA (Canada)
// Regulation:  GDPR Art. 15 (Right of Access) — data must be provided in
//              "intelligible form" and "commonly used electronic format"
//              GDPR Art. 20 (Right to Data Portability) — machine-readable format
//              UK GDPR Art. 15/20 — identical obligations post-Brexit
//              LGPD Art. 18(I)(II)(V) — access and portability rights
//              PIPEDA Principle 9 — reasonable access within "reasonable timeframe"
// Purpose:     Dynamically extracts all 7 player data domains as CSV for DSAR
//              responses. The 7-domain approach ensures exhaustive coverage to
//              satisfy the "all personal data" requirement of GDPR Art. 15(1).
//              Missing any data domain in a DSAR response is an Art. 12 violation.
// Deadline:    EU/UK GDPR: response within 30 calendar days of request receipt
//              (extendable to 90 days for complex requests — must notify player)
//              LGPD (Brazil): 15 business days
//              PIPEDA (Canada): 30 calendar days
// Penalty:     GDPR Art. 83(5): up to €20M or 4% global annual turnover
//              UK GDPR: equivalent fines in GBP
//              LGPD: up to 2% of Brazilian revenue, max R$50M per infraction
// Oracle note: This service uses oracledb for database connections — this is the
//              Oracle-hosted variant of the DSAR extraction service. The schema
//              (gdpr_schema.sql) defines equivalent tables in both Oracle and PG.
// Jurisdictions: All EU/EEA (MGA, GGL, Sweden, Netherlands), UK (UKGC),
//              Brazil (SPA/MF), Canada (Ontario AGCO)
//
// References:
//   GDPR Full Text: https://gdpr-info.eu/
//   Art. 15 (Right of Access): https://gdpr-info.eu/art-15-gdpr/
//   Art. 20 (Data Portability): https://gdpr-info.eu/art-20-gdpr/
//   Art. 83 (Penalties): https://gdpr-info.eu/art-83-gdpr/
//   UK GDPR: https://www.legislation.gov.uk/uksi/2019/419/contents
//   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
//   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
//   LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/L13709.htm
//   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
//   iGaming Ontario: https://igamingontario.ca/en/operators
// =============================================================================
// Chapter 02 - Regulation & Compliance Landscape
// GDPR Data Extractor: Multi-Domain Data Extraction Engine
//
// Dynamically loads SQL extract files from the filesystem and executes them
// per-user, converting Oracle result sets to CSV. Uses async generators for
// memory-efficient streaming of potentially large datasets.
//
// Extract files cover 7 data domains required for GDPR compliance:
//   01-account-details.sql      - Profile, contact info, device registration
//   02-account-history.sql      - Financial transaction history
//   03-gaming-payouts-losses.sql - Aggregated gaming activity
//   04-deposits.sql             - Deposit transactions
//   05-withdrawals.sql          - Withdrawal transactions
//   06-lock-history.sql         - Account restrictions / self-exclusions
//   07-comments.sql             - Communications and task history

import { readdirAsync, readFileAsync } from './fsAsync';
import { extname, parse as pathParse } from 'path';
import { getConnection as getOracleConnection, Connection as OracleConnection } from 'oracledb';
import { EOL } from 'os';

export class PlatformDataProcessor implements IDataProcessor {

    private _extracts: { [id: string]: string };

    private constructor(private _config: PlatformDataProcessorConfig) {
        this.loadExtracts();
    }

    static async CreateAsync(config: PlatformDataProcessorConfig): Promise<PlatformDataProcessor> {
        let instance = new this(config);
        await instance.loadExtracts();
        return instance;
    }

    // Discover SQL extract files automatically from the configured directory
    private async loadExtracts() {
        let allFiles = await readdirAsync(this._config.platformSqlPath);
        let sqlFiles = allFiles.filter(f => extname(f).toLowerCase() == '.sql');

        this._extracts = {};
        for (let file of sqlFiles) {
            let details = pathParse(file);
            this._extracts[details.name] = file;
        }
    }

    // -----------------------------------------------------------------------
    // Async generator: yields CSV-formatted results for each data domain.
    // Each SQL file is executed with the userId as a bind parameter,
    // and results are streamed one extract at a time.
    // -----------------------------------------------------------------------
    async *GetAllDataCsv(userId: number): AsyncIterableIterator<CsvExtract> {
        let extracts = Object.keys(this._extracts);

        for (let extractName of extracts) {
            let connection: OracleConnection;

            try {
                connection = await getOracleConnection({
                    user: this._config.platformDbConnectionSettings.username,
                    password: this._config.platformDbConnectionSettings.password,
                    connectString: this._config.platformDbConnectionSettings.server
                });

                // Read the SQL file and execute with userId bind parameter
                let sql = (await readFileAsync(
                    this._config.platformSqlPath + '/' + this._extracts[extractName]
                )).toString();

                const sqlResult = await connection.execute(sql, [userId]);

                // Build CSV: header row from Oracle metadata
                let output = sqlResult.metaData.map(col => col.name).join(',');
                output += EOL;

                // Data rows with proper type handling
                for (let row of sqlResult.rows) {
                    let rowData: string[] = [];
                    for (let i in row) {
                        if ((<any>row)[i] instanceof Date) {
                            rowData.push((<Date>(<any>row)[i]).toISOString());
                        } else {
                            rowData.push((<any>row)[i]);
                        }
                    }
                    output += rowData.join(',') + EOL;
                }

                yield { name: extractName, data: output };

            } catch (err) {
                console.error(`Error extracting ${extractName} for user ${userId}:`, err);
                throw err;
            } finally {
                if (connection) {
                    await connection.close();
                }
            }
        }
    }

    async *GetExtractNames(): AsyncIterableIterator<string> {
        await this.loadExtracts();
        for (let name in this._extracts) {
            yield name;
        }
    }
}

export interface PlatformDataProcessorConfig {
    platformDbConnectionSettings: { server: string; username: string; password: string; };
    platformSqlPath: string;
}
