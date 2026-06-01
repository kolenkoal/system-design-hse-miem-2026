import http from 'k6/http';
import { sleep, check, group } from 'k6';
import { Trend, Counter, Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

// Кастомные метрики для сравнения cache hit vs miss
const hitDuration  = new Trend('cache_hit_ms',  true);
const missDuration = new Trend('cache_miss_ms', true);
const hits         = new Counter('cache_hits');
const misses       = new Counter('cache_misses');

export const options = {
  scenarios: {
    // ── Фаза 1: курсы валют — лёгкий запрос, редко меняется ──────────────────
    exchange_rates: {
      executor: 'ramping-vus',
      startTime: '0s',
      stages: [
        { duration: '5s',  target: 30 },
        { duration: '20s', target: 30 },
        { duration: '5s',  target: 0  },
      ],
      exec: 'testRates',
      tags: { endpoint: 'rates' },
    },

    // ── Фаза 2: сводка по счёту — тяжёлая агрегация по 500k строк ─────────────
    account_summary: {
      executor: 'ramping-vus',
      startTime: '35s',
      stages: [
        { duration: '5s',  target: 10 },
        { duration: '20s', target: 10 },
        { duration: '5s',  target: 0  },
      ],
      exec: 'testSummary',
      tags: { endpoint: 'summary' },
    },

    // ── Фаза 3: кредитный скор — самый дорогой запрос (CTEs + регрессия) ──────
    credit_score: {
      executor: 'ramping-vus',
      startTime: '65s',
      stages: [
        { duration: '5s',  target: 5 },
        { duration: '20s', target: 5 },
        { duration: '5s',  target: 0 },
      ],
      exec: 'testCreditScore',
      tags: { endpoint: 'credit_score' },
    },
  },

  thresholds: {
    // После прогрева кэша 95-й перцентиль хитов должен быть < 50 мс
    cache_hit_ms: ['p(95)<50'],
    http_req_failed: ['rate<0.01'],
  },
};

export function setup() {
  // Очищаем кэш перед началом, чтобы первые запросы были cache miss
  const res = http.del(`${BASE_URL}/cache/clear`);
  check(res, { 'cache cleared': (r) => r.status === 200 });
  console.log('✓ Cache cleared — starting benchmark');
  sleep(1);
}

function track(res) {
  if (!check(res, { 'status 200': (r) => r.status === 200 })) return;
  const body = res.json();
  if (body.source === 'cache') {
    hitDuration.add(res.timings.duration);
    hits.add(1);
  } else {
    missDuration.add(res.timings.duration);
    misses.add(1);
  }
}

export function testRates() {
  group('exchange_rates', () => {
    const currencies = ['USD', 'EUR', 'RUB'];
    const cur = currencies[Math.floor(Math.random() * currencies.length)];
    track(http.get(`${BASE_URL}/rates/${cur}`));
    sleep(0.05);
  });
}

export function testSummary() {
  group('account_summary', () => {
    const id = Math.floor(Math.random() * 10) + 1;
    track(http.get(`${BASE_URL}/accounts/${id}/summary`));
    sleep(0.1);
  });
}

export function testCreditScore() {
  group('credit_score', () => {
    const id = Math.floor(Math.random() * 10) + 1;
    track(http.get(`${BASE_URL}/accounts/${id}/credit-score`));
    sleep(0.2);
  });
}

export function teardown() {
  const res = http.get(`${BASE_URL}/cache/stats`);
  if (res.status === 200) {
    const stats = res.json();
    console.log(`\n── Cache stats after benchmark ──`);
    console.log(`  Keys in Redis : ${stats.total_keys}`);
    console.log(`  Memory used   : ${stats.used_memory_human}`);
  }
}
