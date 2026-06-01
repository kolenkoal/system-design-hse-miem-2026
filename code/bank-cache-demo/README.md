# Bank Cache Demo — пример кэширования для курса System Design

## Архитектура

```
┌──────────┐    ┌─────────────────────┐    ┌──────────────────────┐
│  Client  │───▶│  FastAPI :8000      │───▶│  PostgreSQL :5432    │
│  / k6    │    │                     │    │  10 accounts         │
└──────────┘    │  cache-aside        │    │  500 000 transactions│
                │  pattern            │    └──────────────────────┘
                │                     │
                │                     │◀──▶┌──────────────────────┐
                └─────────────────────┘    │  Redis :6379         │
                                           │  maxmemory 256mb     │
                                           │  allkeys-lru eviction│
                                           └──────────────────────┘
```

### Паттерны кэширования в примере

| Эндпоинт | Паттерн | TTL | Инвалидация |
|---|---|---|---|
| `GET /rates/{currency}` | Cache-aside | 60 мин | По TTL |
| `GET /accounts/{id}/summary` | Cache-aside | 5 мин | При переводе |
| `GET /accounts/{id}/credit-score` | Cache-aside | 24 часа | По TTL |

> **Про кредитный скоринг.** Реальный скоринг — это не один SQL-запрос, а ML-инференс
> и обращения в кредитные бюро, занимающие секунды. В примере эта дорогая часть
> симулируется через `pg_sleep` (по умолчанию 1.5 сек, переменная `SCORING_DELAY_SEC`).
> Именно поэтому кэш здесь даёт выигрыш в сотни раз. На 500k строк сам SQL быстрый
> (~10 мс): PostgreSQL отлично читает данные из RAM, и без симуляции разница была бы
> всего ~10x. Это тоже полезный вывод для студентов: кэш особенно оправдан там, где
> источник данных реально медленный.
| `GET /accounts/{id}/balance` | Cache-aside | 30 сек | При переводе |

---

## Быстрый старт

### 1. Запуск

```bash
docker compose up -d --build
# или через Makefile:
make up
```

Postgres при первом запуске наполняет базу **500 000 транзакций** — это займёт ~30–60 секунд. Бэкенд сам дожидается готовности БД и стартует после.

Проверить готовность:
```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### 2. Ручные запросы

**Курсы валют (лёгкий запрос, TTL 1 час)**
```bash
# Первый запрос — cache miss (~20–80 мс)
curl http://localhost:8000/rates/USD

# Второй и все последующие — cache hit (~1–3 мс)
curl http://localhost:8000/rates/USD
```

**Сводка по счёту (тяжёлый агрегат, TTL 5 мин)**
```bash
# cache miss — seq scan по 500k строк, ~1–4 сек
curl http://localhost:8000/accounts/1/summary

# cache hit — ~1–3 мс
curl http://localhost:8000/accounts/1/summary
```

**Кредитный скор (дорогая симуляция скоринга, TTL 24 ч)**
```bash
# cache miss — ~1.5 сек (симуляция ML-инференса + CTE с регрессией)
curl http://localhost:8000/accounts/1/credit-score

# cache hit — ~3 мс (в ~500 раз быстрее)
curl http://localhost:8000/accounts/1/credit-score
```

**Баланс с инвалидацией кэша**
```bash
# Запрашиваем баланс — кладётся в кэш (TTL 30 сек)
curl http://localhost:8000/accounts/1/balance
# {"source":"db", "balance":125000.0, ...}

curl http://localhost:8000/accounts/1/balance
# {"source":"cache", ...}

# Делаем перевод — кэш баланса обоих счетов инвалидируется
curl -X POST "http://localhost:8000/transfer?from_account=1&to_account=2&amount=500"
# {"cache_invalidated":["balance:1","balance:2","summary:1","summary:2"]}

# Снова запрашиваем — снова cache miss, но уже с новым балансом
curl http://localhost:8000/accounts/1/balance
# {"source":"db", "balance":124500.0, ...}
```

**Статистика Redis**
```bash
curl http://localhost:8000/cache/stats
```

**Очистить кэш**
```bash
curl -X DELETE http://localhost:8000/cache/clear
```

---

## Бенчмарк (k6)

### Запуск

```bash
make bench
# или:
docker compose --profile benchmark run --rm k6 run /scripts/benchmark.js
```

Бенчмарк состоит из трёх последовательных фаз (~90 секунд):

| Фаза | Время | Эндпоинт | VUs |
|---|---|---|---|
| 1 | 0–30 сек | `/rates/{currency}` | 30 |
| 2 | 35–65 сек | `/accounts/{id}/summary` | 10 |
| 3 | 65–95 сек | `/accounts/{id}/credit-score` | 5 |

### Чтение результатов

Реальный вывод прогона на этом примере (~90 сек, 500k транзакций):

```
cache_hit_ms ...: avg=4.16ms   med=2.16ms  p(90)=12.29ms  p(95)=16.15ms
cache_miss_ms ..: avg=915.35ms med=1.57s   p(90)=1.61s    p(95)=1.61s
cache_hits .....: 16746
cache_misses ...: 30
http_req_failed : 0.00%

── Cache stats after benchmark ──
  Keys in Redis : 23
  Memory used   : 1.02M
```

Ключевые кастомные метрики:

- **`cache_hit_ms`** — латентность запросов из Redis (медиана ~2 мс)
- **`cache_miss_ms`** — латентность запросов к источнику (медиана ~1.5 сек)
- **`cache_hits` / `cache_misses`** — соотношение хитов и промахов

Главный вывод: медиана cache hit (2.16 мс) против cache miss (1.57 с) — разница
в **~700 раз**. При этом всего **30 промахов на 16746 хитов**: после прогрева почти
все запросы обслуживаются из кэша, не нагружая базу. Цена — **~1 МБ ОЗУ** в Redis
на 23 ключа.

---

## Что демонстрирует пример

### 1. Польза кэша
Тяжёлые агрегаты по 500k строк (без индексов) занимают секунды. Redis отдаёт тот же результат за миллисекунды.

### 2. Стоимость кэша — память
```bash
# Посмотреть, сколько памяти занимает кэш
curl http://localhost:8000/cache/stats
```

### 3. Инвалидация
`POST /transfer` явно удаляет из Redis ключи `balance:{id}` и `summary:{id}` для обоих счетов. После этого следующий запрос снова идёт в БД и кладёт свежие данные в кэш.

### 4. TTL-based eviction
Разные TTL для разных данных: курсы валют — 1 час, баланс — 30 секунд. Чем чаще меняются данные, тем короче TTL.

### 5. LRU eviction
Redis настроен с `maxmemory 256mb` и политикой `allkeys-lru`. При заполнении памяти Redis сам вытесняет наименее используемые ключи.

---

## Остановка и очистка

```bash
# Остановить контейнеры (данные сохраняются в volume)
docker compose down

# Полная очистка с удалением volume (БД сбрасывается)
make clean
# или: docker compose down -v
```

## Логи бэкенда

```bash
make logs
```