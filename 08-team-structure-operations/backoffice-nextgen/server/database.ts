// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Oracle Database Layer
// Dynamic SQL script loading, connection management, transaction support

import * as FileSystem from 'fs';
import * as Path from 'path';
import * as oracledb from 'oracledb';

(<any>oracledb).fetchAsString = [oracledb.CLOB];

var scripts = {};

function readDirR(dir): string[] {
  return FileSystem.statSync(dir).isDirectory()
    ? Array.prototype.concat(
        ...FileSystem.readdirSync(dir).map((f) => readDirR(Path.join(dir, f)))
      )
    : dir;
}

function getAllReMatches(re: RegExp, s: string) {
  let m;
  let result = [];
  do {
    m = re.exec(s);
    if (m) {
      result.push(m[1]);
    }
  } while (m);
  return result;
}

// Load all SQL scripts from the sql/ directory at startup
// Scripts are namespaced: sql/auth/getCurrentSession.sql -> scripts['auth']['getCurrentSession']
export function initSqlScripts() {
  let files = readDirR(__dirname + '/sql');

  for (let file of files) {
    let content = FileSystem.readFileSync(file, { encoding: 'utf8' });
    let path = file.replace(__dirname, '').replace(/[\/|\\]sql[\/|\\]/g, '');
    let parts = path.split(/[\/|\\]/g);

    let currentPath = '';
    let namespace = parts.shift();

    if (!scripts[namespace]) {
      scripts[namespace] = {};
    }

    for (let part of parts) {
      if (part.indexOf('.sql') > 0) {
        currentPath += part.substring(0, part.length - 4);
      } else {
        currentPath += part + '-';
      }
    }

    // Extract bind parameters (e.g., :id, :brands) from SQL content
    let params = getAllReMatches(/(:[a-z0-9_-]*)/g, content).map(function (o) {
      return o.replace(':', '');
    });
    params = params.filter(function (val, ind) {
      return params.indexOf(val) === ind;
    });

    console.log('load:', namespace, '-', currentPath);
    scripts[namespace][currentPath] = { content: content, params: params };
  }
}

export class Database {
  private readonly server: string;
  private readonly user: string;
  private readonly password: string;

  constructor(server: string, user: string, password: string) {
    if (!server || !user || !password) {
      throw new Error('server, user and password are required');
    }
    this.server = server;
    this.user = user;
    this.password = password;
  }

  // Execute multiple queries in a single transaction
  public async runMultipleQueriesInTransaction(
    callback: (
      runQuery: (
        namespace: string,
        scriptName: string,
        params: { [key: string]: any }
      ) => Promise<any>
    ) => void
  ) {
    let connection: oracledb.IConnection;

    try {
      connection = await this._getConnection();
    } catch (err) {
      throw err;
    }

    try {
      await callback(
        async (
          namespace: string,
          scriptName: string,
          params: { [key: string]: any }
        ): Promise<any> => {
          return await this.runQuery(namespace, scriptName, params, connection, false);
        }
      );
    } catch (e) {
      connection.rollback();
      throw e;
    }

    try {
      await this._commitTransaction(connection);
    } catch (err) {
      console.error(err);
      throw err;
    } finally {
      this._doRelease(connection);
    }
  }

  private async _commitTransaction(connection: oracledb.IConnection) {
    return new Promise<any>(async (resolve, reject) => {
      connection
        .commit()
        .then((res) => resolve())
        .catch((rej) => reject(rej));
    });
  }

  // Run a named SQL script with parameters
  // Automatically maps Oracle result rows to objects using column metadata
  public async runQuery(
    namespace: string,
    scriptName: string,
    params: { [key: string]: any },
    connection: oracledb.IConnection = null,
    doCommit: boolean = true
  ): Promise<any> {
    return new Promise<any>(async (resolve, reject) => {
      let closeConnection = false;

      if (connection == null) {
        closeConnection = true;
        try {
          connection = await this._getConnection();
        } catch (err) {
          reject(err);
          return;
        }
      }

      let script = this._getScript(namespace, scriptName);
      let params_to_send = {};

      // Initialize all expected params to null
      for (let param of script.params) {
        params_to_send[param] = null;
      }

      // Override with provided values
      for (let i in params) {
        if (params.hasOwnProperty(i)) {
          params_to_send[i] = params[i];
        }
      }

      connection.execute(script.content, params_to_send, async (err, value) => {
        if (doCommit) {
          await this._commitTransaction(connection);
        }

        if (err) {
          console.error(err.message);
          reject(err);
          if (closeConnection) {
            connection.rollback();
            this._doRelease(connection);
          }
          return;
        }

        if (!value.rows) {
          resolve(value);
          if (closeConnection) {
            this._doRelease(connection);
          }
          return;
        }

        // Map rows to objects using column metadata
        let results = [];
        for (let row of value.rows) {
          let rowObj = {};
          for (let i in row) {
            rowObj[value.metaData[i].name] = row[i];
          }
          results.push(rowObj);
        }

        resolve(results);
        if (closeConnection) {
          this._doRelease(connection);
        }
      });
    });
  }

  private _getScript(namespace: string, scriptName: string) {
    if (!scripts[namespace] || !scripts[namespace][scriptName]) {
      throw new Error(`cannot find script "${namespace}/${scriptName}" `);
    }
    return scripts[namespace][scriptName];
  }

  private _getConnection(): Promise<oracledb.IConnection> {
    return new Promise<oracledb.IConnection>((resolve, reject) => {
      oracledb.getConnection(
        {
          user: this.user,
          password: this.password,
          connectString: this.server,
        },
        function (err, connection) {
          if (err) {
            reject(err);
            return;
          }
          // Force UTC timezone for all sessions
          connection.execute("ALTER SESSION SET TIME_ZONE='UTC'", function (err) {
            if (err) {
              reject(err);
              return;
            }
            resolve(connection);
          });
        }
      );
    });
  }

  private _doRelease(connection) {
    connection.close(function (err) {
      if (err) {
        console.error(err.message);
      }
    });
  }
}

export default Database;
