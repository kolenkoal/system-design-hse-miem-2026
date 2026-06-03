// Usage:
//   K6_SCENARIO=storm k6 run k6/scripts/scenarios.js
//   K6_SCENARIO=wave  k6 run k6/scripts/scenarios.js
//   K6_SCENARIO=pulse k6 run k6/scripts/scenarios.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('error_rate');
const createLatency = new Trend('create_order_latency');
const getLatency = new Trend('get_order_latency');

const SCENARIO = __ENV.K6_SCENARIO || 'storm';

const stagesMap = {
  storm: [
    { duration: '10s', target: 1000 },
    { duration: '30s', target: 1000 },
    { duration: '10s', target: 0 },
  ],
  wave: [
    { duration: '2m', target: 500 },
    { duration: '1m', target: 500 },
    { duration: '30s', target: 0 },
  ],
  pulse: [
    { duration: '15s', target: 300 },
    { duration: '30s', target: 0 },
    { duration: '15s', target: 300 },
    { duration: '30s', target: 0 },
    { duration: '15s', target: 300 },
    { duration: '30s', target: 0 },
  ],
};

export const options = {
  stages: stagesMap[SCENARIO],
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<2000'],
    error_rate: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

export default function () {
  const isWrite = Math.random() < 0.8;

  if (isWrite) {
    const payload = JSON.stringify({
      user_id: randomInt(1, 2),
      amount: Math.random() * 100,
      description: 'k6 load',
    });
    const res = http.post(`${BASE_URL}/api/orders`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });
    createLatency.add(res.timings.duration);
    errorRate.add(res.status >= 400);
    check(res, { 'POST 2xx': (r) => r.status >= 200 && r.status < 300 });
  } else {
    const res = http.get(`${BASE_URL}/api/orders`);
    getLatency.add(res.timings.duration);
    errorRate.add(res.status >= 400);
    check(res, { 'GET 2xx': (r) => r.status === 200 });
  }

  sleep(Math.random() * 0.5 + 0.1);
}
