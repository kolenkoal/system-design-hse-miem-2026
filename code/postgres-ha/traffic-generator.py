import psycopg2
import time
import random
from datetime import datetime

DB_CONFIG = {
    "host": "localhost",
    "port": "5002",
    "sslmode": "disable",
    "dbname": "postgres",
    "user": "postgres",
    "password": "postgres",
    "target_session_attrs": "read-write"
}

EVENT_TYPES = ['login', 'logout', 'click', 'purchase', 'view_page', 'error']

def get_connection():
    """Создает и возвращает подключение к БД"""
    return psycopg2.connect(**DB_CONFIG)

def get_random_owner(cursor):
    """Получает случайного овнера из справочника"""
    cursor.execute("SELECT owner_name FROM owners ORDER BY RANDOM() LIMIT 1;")
    result = cursor.fetchone()
    return result[0] if result else "Unknown"

def main():
    print(f"--- STARTING LOAD GENERATOR ON PORT {DB_CONFIG['port']} ---")
    
    conn = None
    tick = 0

    while True:
        try:
            if conn is None or conn.closed:
                try:
                    conn = get_connection()
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] CONNECTED to Master Node")
                except Exception as e:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Connection failed: {e}")
                    time.sleep(1)
                    continue

            cur = conn.cursor()

            # --- 1. ВСТАВКА (Каждую секунду) ---
            owner = get_random_owner(cur)
            event = random.choice(EVENT_TYPES)
            
            insert_query = "INSERT INTO events (event_name, owner_name) VALUES (%s, %s)"
            cur.execute(insert_query, (event, owner))
            conn.commit()
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INSERT: {event} by {owner}")

            # --- 2. ЧТЕНИЕ (Каждые 2 секунды) ---
            if tick % 2 == 0:
                cur.execute("SELECT id, event_name, owner_name FROM events ORDER BY id DESC LIMIT 3")
                rows = cur.fetchall()
                print(f"READ check (Last 3 IDs): {[r[0] for r in rows]}")

            cur.close()
            
            tick += 1
            time.sleep(1)

        except psycopg2.OperationalError as e:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] CONNECTION LOST (Failover in progress?): {e}")
            conn = None
            time.sleep(1)
            
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Error: {e}")
            conn = None
            time.sleep(1)

if __name__ == "__main__":
    main()