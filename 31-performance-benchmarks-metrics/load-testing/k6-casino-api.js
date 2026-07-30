// Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const BASE_URL = __ENV.API_URL || 'https://new.acmetocasino.com/api/v2';
const errorRate = new Rate('errors');

export const options = {
  scenarios: {
    smoke: {
      executor: 'constant-vus',
      vus: 10,
      duration: '1m',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.01'],
  },
};

export default function () {
  // Health check
  let res = http.get(`${BASE_URL}/health`);
  check(res, { 'health 200': (r) => r.status === 200 });
  errorRate.add(res.status !== 200);

  // Get players
  res = http.get(`${BASE_URL}/pam/players?limit=10`);
  check(res, { 'players 200': (r) => r.status === 200 });
  errorRate.add(res.status !== 200);

  // Dashboard summary
  res = http.get(`${BASE_URL}/dashboard/summary`);
  check(res, { 'dashboard 200': (r) => r.status === 200 });
  errorRate.add(res.status !== 200);

  // RNG
  res = http.get(`${BASE_URL}/gal/rng`);
  check(res, { 'rng 200': (r) => r.status === 200 });
  errorRate.add(res.status !== 200);

  // Sports engine
  res = http.get(`${BASE_URL}/sports-engine/status`);
  check(res, { 'sports 200': (r) => r.status === 200 });

  // HSM status
  res = http.get(`${BASE_URL}/hsm/status`);
  check(res, { 'hsm 200': (r) => r.status === 200 });

  sleep(1);
}
