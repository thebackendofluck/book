// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

const glob = require('glob');
const yaml = require('js-yaml');
const fs = require('fs');
const swaggerToTS = require('@manifoldco/swagger-to-ts').default;
const path = require('path');

const dir = __dirname + '/../dist/';

// options is optional
glob(__dirname + '/../types/*.y?(a)ml', {}, function (er, files) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir);
  }

  files.forEach((file) => {
    try {
      const doc = yaml.safeLoad(fs.readFileSync(file, 'utf8'));
      const output = swaggerToTS(doc, { wrapper: false });
      fs.writeFileSync(dir + path.parse(file).name + '.ts', output);
    } catch (error) {
      console.log("Unable to generate types for ", file);
    }
  });
});
