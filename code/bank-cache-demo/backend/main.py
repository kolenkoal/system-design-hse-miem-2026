from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
import psycopg2
import redis as redis_lib
import json
import os
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "dbname": os.getenv("DB_NAME", "bank"),
    "user": os.getenv("DB_USER", "bank"),
    "password": os.getenv("DB_PASSWORD", "bank"),
}
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
SCORING_DELAY_SEC = float(os.getenv("SCORING_DELAY_SEC", "1.5"))


def get_db():
    return psycopg2.connect(**DB_CONFIG)


def get_redis():
    return redis_lib.Redis(host=REDIS_HOST, port=6379, decode_responses=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for i in range(60):
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM transactions")
                count = cur.fetchone()[0]
            conn.close()
            if count > 0:
                logger.info(f"DB ready: {count} transactions loaded")
                break
        except Exception as e:
            logger.info(f"Waiting for DB... attempt {i + 1}/60 ({e})")
            time.sleep(3)
    else:
        raise RuntimeError("Database not ready after 180 seconds")
    yield


app = FastAPI(title="Bank Cache Demo", lifespan=lifespan)


# ─── Exchange Rates ────────────────────────────────────────────────────────────
# Курсы меняются редко — кэшируем на 60 минут.
# Cache-aside pattern: сначала Redis, при промахе — в БД, результат кладём в Redis.

@app.get("/rates/{currency}")
def get_exchange_rates(currency: str):
    r = get_redis()
    key = f"rates:{currency.upper()}"

    cached = r.get(key)
    if cached:
        return {"source": "cache", "ttl_remaining": r.ttl(key), "data": json.loads(cached)}

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_currency, rate, updated_at FROM exchange_rates WHERE from_currency = %s ORDER BY to_currency",
                (currency.upper(),),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="Currency not found")

    data = [{"to": row[0], "rate": float(row[1]), "updated_at": str(row[2])} for row in rows]
    r.setex(key, 3600, json.dumps(data))
    return {"source": "db", "ttl_remaining": 3600, "data": data}


# ─── Account Summary ───────────────────────────────────────────────────────────
# Тяжёлый агрегат по 500k строк без индекса — seq scan занимает 1–4 секунды.
# Кэшируем на 5 минут. Инвалидируем при переводе.

@app.get("/accounts/{account_id}/summary")
def get_account_summary(account_id: int):
    r = get_redis()
    key = f"summary:{account_id}"

    cached = r.get(key)
    if cached:
        return {"source": "cache", "ttl_remaining": r.ttl(key), **json.loads(cached)}

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT account_number, balance, account_type, owner_name FROM accounts WHERE id = %s",
                (account_id,),
            )
            account = cur.fetchone()
            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE from_account_id = %(id)s)                        AS sent_count,
                    COUNT(*) FILTER (WHERE to_account_id   = %(id)s)                        AS recv_count,
                    COALESCE(SUM(amount) FILTER (WHERE from_account_id = %(id)s), 0)        AS total_sent,
                    COALESCE(SUM(amount) FILTER (WHERE to_account_id   = %(id)s), 0)        AS total_recv,
                    COALESCE(AVG(amount) FILTER (WHERE from_account_id = %(id)s
                                                    OR to_account_id   = %(id)s), 0)        AS avg_amount,
                    MAX(created_at)                                                          AS last_tx
                FROM transactions
                WHERE from_account_id = %(id)s OR to_account_id = %(id)s
                """,
                {"id": account_id},
            )
            stats = cur.fetchone()
    finally:
        conn.close()

    data = {
        "account_id": account_id,
        "account_number": account[0],
        "balance": float(account[1]),
        "account_type": account[2],
        "owner_name": account[3],
        "stats": {
            "sent_count": stats[0],
            "recv_count": stats[1],
            "total_sent": float(stats[2]),
            "total_recv": float(stats[3]),
            "avg_amount": round(float(stats[4]), 2),
            "last_transaction": str(stats[5]) if stats[5] else None,
        },
    }
    r.setex(key, 300, json.dumps(data))
    return {"source": "db", "ttl_remaining": 300, **data}


# ─── Credit Score ──────────────────────────────────────────────────────────────
# Самый дорогой запрос: несколько CTE + оконные функции + регрессия по 100k строк.
# Реальный кредитный скоринг мог бы занимать часы. Кэшируем на 24 часа.

@app.get("/accounts/{account_id}/credit-score")
def get_credit_score(account_id: int):
    r = get_redis()
    key = f"credit_score:{account_id}"

    cached = r.get(key)
    if cached:
        return {"source": "cache", "ttl_remaining": r.ttl(key), "account_id": account_id, "score": int(cached)}

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM accounts WHERE id = %s", (account_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Account not found")

            cur.execute("SELECT pg_sleep(%s)", (SCORING_DELAY_SEC,))

            cur.execute(
                """
                WITH tx_stats AS (
                    SELECT
                        COUNT(*)                                                                AS tx_count,
                        COALESCE(SUM(amount) FILTER (WHERE to_account_id   = %(id)s), 0)      AS total_income,
                        COALESCE(SUM(amount) FILTER (WHERE from_account_id = %(id)s), 0)      AS total_spent,
                        COALESCE(STDDEV(amount), 0)                                            AS stddev_amount,
                        COUNT(DISTINCT date_trunc('month', created_at))                        AS active_months
                    FROM transactions
                    WHERE from_account_id = %(id)s OR to_account_id = %(id)s
                ),
                monthly AS (
                    SELECT
                        date_trunc('month', created_at)                                        AS month,
                        COALESCE(SUM(amount) FILTER (WHERE to_account_id   = %(id)s), 0) -
                        COALESCE(SUM(amount) FILTER (WHERE from_account_id = %(id)s), 0)      AS net_flow
                    FROM transactions
                    WHERE from_account_id = %(id)s OR to_account_id = %(id)s
                    GROUP BY 1
                )
                SELECT
                    s.tx_count,
                    s.total_income,
                    s.total_spent,
                    s.stddev_amount,
                    s.active_months,
                    COALESCE(REGR_SLOPE(m.net_flow::float8, EXTRACT(EPOCH FROM m.month)), 0) AS trend
                FROM tx_stats s, monthly m
                GROUP BY s.tx_count, s.total_income, s.total_spent, s.stddev_amount, s.active_months
                """,
                {"id": account_id},
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        score = 300
    else:
        tx_count, total_income, total_spent, _, active_months, trend = row
        score = 500
        score += min(150, int((tx_count or 0) * 0.002))
        score += min(100, int((total_income or 0) / 50000))
        score -= min(75, int((total_spent or 0) / 100000))
        score += min(50, int((active_months or 0) * 4))
        score += 25 if (trend or 0) > 0 else -10
        score = max(300, min(850, score))

    r.setex(key, 86400, str(score))
    return {"source": "db", "ttl_remaining": 86400, "account_id": account_id, "score": score}


# ─── Balance ───────────────────────────────────────────────────────────────────
# Простой lookup — показывает инвалидацию при переводе.

@app.get("/accounts/{account_id}/balance")
def get_balance(account_id: int):
    r = get_redis()
    key = f"balance:{account_id}"

    cached = r.get(key)
    if cached:
        return {"source": "cache", "ttl_remaining": r.ttl(key), "account_id": account_id, "balance": float(cached)}

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM accounts WHERE id = %s", (account_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    balance = float(row[0])
    r.setex(key, 30, str(balance))
    return {"source": "db", "ttl_remaining": 30, "account_id": account_id, "balance": balance}


# ─── Transfer ──────────────────────────────────────────────────────────────────
# Запись → инвалидируем кэш баланса и сводки для обоих счетов.

@app.post("/transfer")
def transfer(from_account: int, to_account: int, amount: float):
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    if from_account == to_account:
        raise HTTPException(status_code=400, detail="Cannot transfer to the same account")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM accounts WHERE id = %s FOR UPDATE", (from_account,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Source account not found")
            if float(row[0]) < amount:
                raise HTTPException(status_code=400, detail="Insufficient funds")

            cur.execute("SELECT id FROM accounts WHERE id = %s", (to_account,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Destination account not found")

            cur.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, from_account))
            cur.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, to_account))
            cur.execute(
                "INSERT INTO transactions (from_account_id, to_account_id, amount, description) VALUES (%s, %s, %s, 'transfer')",
                (from_account, to_account, amount),
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

    r = get_redis()
    invalidated = [
        f"balance:{from_account}",
        f"balance:{to_account}",
        f"summary:{from_account}",
        f"summary:{to_account}",
    ]
    r.delete(*invalidated)

    return {
        "status": "ok",
        "transferred": amount,
        "from": from_account,
        "to": to_account,
        "cache_invalidated": invalidated,
    }


# ─── Cache Management ──────────────────────────────────────────────────────────

@app.delete("/cache/clear")
def clear_cache():
    get_redis().flushdb()
    return {"status": "cache cleared"}


@app.get("/cache/stats")
def cache_stats():
    r = get_redis()
    info = r.info("memory")
    keyspace = r.info("keyspace")
    keys = r.dbsize()
    all_keys = r.keys("*")
    return {
        "total_keys": keys,
        "keys": all_keys,
        "used_memory_human": info["used_memory_human"],
        "used_memory_mb": round(info["used_memory"] / 1024 / 1024, 3),
        "keyspace": keyspace,
    }


@app.get("/health")
def health():
    return {"status": "ok"}
