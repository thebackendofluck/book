// Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Load Test Script (K6) - Chapter 22: Casino Implementation Planning and Timeline
 *
 * Performance testing script using K6 that simulates user registration,
 * login, and profile access under various load stages.
 *
 * Run with: k6 run load_test.js
 *
 * Part of the iGaming Platform Engineering book.
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

export let options = {
  stages: [
    { duration: '2m', target: 100 }, // Ramp up
    { duration: '5m', target: 100 }, // Stay at 100 users
    { duration: '2m', target: 1000 }, // Ramp up to 1000
    { duration: '5m', target: 1000 }, // Stay at 1000
    { duration: '2m', target: 10000 }, // Spike to 10000
    { duration: '5m', target: 10000 }, // Stay at 10000
    { duration: '2m', target: 0 }, // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'], // 95% of requests should be below 1000ms
    http_req_failed: ['rate<0.1'], // Error rate should be below 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'https://api.casino.com';

export default function () {
  // User registration
  let registrationResponse = http.post(`${BASE_URL}/auth/register`, {
    email: `user_${__VU}_${Date.now()}@test.com`,
    password: process.env.LOADTEST_PASSWORD || 'changeme',
    firstName: 'Test',
    lastName: 'User',
  });

  check(registrationResponse, {
    'registration status is 201': (r) => r.status === 201,
    'registration response time < 500ms': (r) => r.timings.duration < 500,
  });

  errorRate.add(registrationResponse.status !== 201);

  sleep(1);

  // Login
  let loginResponse = http.post(`${BASE_URL}/auth/login`, {
    email: `user_${__VU}_${Date.now()}@test.com`,
    password: process.env.LOADTEST_PASSWORD || 'changeme',
  });

  check(loginResponse, {
    'login status is 200': (r) => r.status === 200,
    'login response time < 300ms': (r) => r.timings.duration < 300,
  });

  errorRate.add(loginResponse.status !== 200);

  if (loginResponse.status === 200) {
    const token = loginResponse.json().token;

    // Get user profile
    let profileResponse = http.get(`${BASE_URL}/user/profile`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    check(profileResponse, {
      'profile status is 200': (r) => r.status === 200,
      'profile response time < 200ms': (r) => r.timings.duration < 200,
    });

    errorRate.add(profileResponse.status !== 200);
  }

  sleep(Math.random() * 3 + 1); // Random sleep between 1-4 seconds
}
