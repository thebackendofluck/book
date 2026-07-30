// Companion code for "The Backend of Luck" - Chapter 31b, Cache, DNS, and Traffic Surge Engineering.
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
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'https://staging.acmetocasino.com';
const TARGET_RPS = Number(__ENV.TARGET_RPS || '1000');
const DURATION = __ENV.DURATION || '10m';
const PREALLOCATED_VUS = Number(__ENV.PREALLOCATED_VUS || '200');
const MAX_VUS = Number(__ENV.MAX_VUS || '2000');

const CACHEABLE_PATHS = parsePaths(__ENV.CACHEABLE_PATHS || '/,/api/games,/api/games/categories');
const UNCACHED_PATHS = parsePaths(__ENV.UNCACHED_PATHS || '/api/v2/dash/health');
const MONEY_PATHS = parsePaths(__ENV.MONEY_PATHS || '/api/wallet/balance');

export const cacheHitHeaderPresent = new Rate('cache_hit_header_present');
export const cacheableDuration = new Trend('cacheable_duration');
export const uncachedDuration = new Trend('uncached_duration');
export const moneyPathDuration = new Trend('money_path_duration');

export const options = {
  scenarios: {
    cache_surge: {
      executor: 'constant-arrival-rate',
      rate: TARGET_RPS,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: PREALLOCATED_VUS,
      maxVUs: MAX_VUS,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.02'],
    http_req_duration: ['p(95)<1000', 'p(99)<3000'],
    cacheable_duration: ['p(95)<300'],
    uncached_duration: ['p(95)<1500'],
    money_path_duration: ['p(95)<1000'],
  },
};

function parsePaths(raw) {
  return raw
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

function url(path) {
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  return `${BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

function taggedParams(kind) {
  return {
    tags: { kind },
    headers: {
      'User-Agent': `chapter-31b-cache-surge/${kind}`,
      'X-Synthetic-Traffic': 'chapter-31b',
    },
  };
}

export default function () {
  const roll = Math.random();
  let kind;
  let path;

  if (roll < 0.75) {
    kind = 'cacheable';
    path = pick(CACHEABLE_PATHS);
  } else if (roll < 0.95) {
    kind = 'uncached';
    path = pick(UNCACHED_PATHS);
  } else {
    kind = 'money_path_probe';
    path = pick(MONEY_PATHS);
  }

  const res = http.get(url(path), taggedParams(kind));
  const ok = check(res, {
    'status is not 5xx': (r) => r.status < 500,
  });

  if (kind === 'cacheable') {
    cacheableDuration.add(res.timings.duration);
    cacheHitHeaderPresent.add(Boolean(
      res.headers['CF-Cache-Status']
        || res.headers['X-Cache']
        || res.headers['X-Dashboard-Cache']
        || res.headers['Age'],
    ));
  } else if (kind === 'uncached') {
    uncachedDuration.add(res.timings.duration);
  } else {
    moneyPathDuration.add(res.timings.duration);
  }

  if (!ok) {
    sleep(1);
  }
}
