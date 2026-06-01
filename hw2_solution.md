# HW2 + HW3 — High Level Design: Concert Ticketing Platform

> Продолжение HW1. Используются ФТ/НФТ из предыдущего задания.

---

## Part 1 — Декомпозиция на сервисы и интеграции

### 1.1 Метод декомпозиции

Используем **Domain-Driven Design (DDD)** с техникой **Event Storming** для выделения доменов и границ сервисов.

**Домены системы:**

| Домен | Ключевые события | Сущности |
|---|---|---|
| **Identity** | UserRegistered, UserLoggedIn | User, Session |
| **Catalog** | EventCreated, VenueCreated, SeatMapUploaded | Event, Venue, SeatMap, PricingZone |
| **Inventory** | SeatHeld, SeatReleased, SeatSold | Seat, Hold, SeatStatus |
| **Orders** | OrderCreated, OrderConfirmed, OrderCancelled | Order, OrderItem |
| **Payments** | PaymentInitiated, PaymentConfirmed, PaymentFailed, RefundIssued | Payment, Refund |
| **Tickets** | TicketGenerated, TicketDelivered | Ticket, QRCode |
| **Notifications** | EmailSent, SMSSent | Notification, Template |
| **Queue** | UserEnqueued, UserAdmitted, QueuePositionUpdated | QueueEntry, QueueConfig |

---

### 1.2 Выделенные сервисы

| Сервис | Ответственность | Обоснование |
|---|---|---|
| **Auth Service** | Регистрация, вход, выдача JWT, OAuth 2.0 | Централизованная аутентификация — отдельный домен, не смешивается с бизнес-логикой. Все сервисы валидируют токен через него (или локально через shared secret) |
| **User Service** | Профиль пользователя, история заказов, анти-скальпинг (лимиты по user_id) | Управление данными пользователя отделено от аутентификации — разные ритмы изменений |
| **Event Service** | Каталог концертов, поиск, фильтрация, схема зала (seat map), управление событием организатором | Сценарии чтения и записи каталога принципиально разные по нагрузке и доступу |
| **Inventory Service** | Статус мест (available / held / sold), резервирование, освобождение по TTL | **Критический сервис**. Гарантирует отсутствие двойной продажи через Redis SET NX. Изолирован, так как нагрузка на него на порядок выше остальных в момент старта продаж |
| **Order Service** | Создание, подтверждение, отмена заказа; история заказов | Отвечает за жизненный цикл заказа. Оркестрирует Saga между Inventory, Payment, Ticket |
| **Payment Service** | Интеграция с внешними платёжными шлюзами (СБП, эквайринг), идемпотентность списаний, обработка webhook, возвраты | Платёжная интеграция требует изоляции: отдельный контур безопасности, аудит, PCI DSS-совместимость |
| **Ticket Service** | Генерация PDF-билетов, QR-кодов, отправка на email, личный кабинет | Тяжёлая CPU/IO операция (рендеринг PDF). Масштабируется независимо от критического пути бронирования |
| **Notification Service** | Email / SMS уведомления (подтверждение заказа, напоминание о концерте, отмена) | Асинхронная задача, не должна блокировать основной флоу. Отдельный сервис с retry-логикой |
| **Queue Service** | Virtual Waiting Room — управление очередью при старте продаж, выдача позиции, допуск пользователей пачками | Без изоляции в отдельный сервис нельзя включить/выключить очередь для конкретного события. Несёт нагрузку 10 000 RPS в первые секунды |
| **API Gateway** | Rate limiting, маршрутизация, аутентификация входящих запросов (JWT), WAF | Единая точка входа. Отделяет crosscutting concerns от бизнес-логики |

---

### 1.3 Интеграции между сервисами

#### Синхронные взаимодействия (REST / gRPC)

| Источник | Целевой сервис | Тип | Обоснование |
|---|---|---|---|
| API Gateway | Auth Service | REST (gRPC внутри) | Валидация JWT на каждый запрос. Требует низкой latency — кэш в Gateway |
| Order Service | Inventory Service | gRPC | Критический путь: резервирование места. Нужна надёжность и минимальная latency |
| Order Service | Payment Service | REST + Webhook | Инициирование платежа синхронно, но подтверждение — async через webhook |
| User Service | Auth Service | gRPC | Создание пользователя при регистрации |
| Event Service | Inventory Service | gRPC | Инициализация инвентаря при создании события |

#### Асинхронные взаимодействия (Kafka)

| Событие (топик) | Издатель | Подписчики | Обоснование |
|---|---|---|---|
| `order.confirmed` | Order Service | Ticket Service, Notification Service | Генерация билета и отправка email не блокируют пользователя |
| `order.cancelled` | Order Service | Inventory Service, Notification Service, Payment Service | Освобождение места и возврат средств — eventual consistency допустима |
| `payment.received` (webhook) | Payment Service | Order Service | Подтверждение оплаты приходит асинхронно от шлюза |
| `seat.held` | Inventory Service | Queue Service | Queue Service отслеживает доступность мест для регуляции очереди |
| `event.published` | Event Service | Queue Service, Notification Service | Старт продаж — триггер активации очереди |

#### Взаимодействие с внешними сервисами

| Внешний сервис | Протокол | Сервис-интегратор |
|---|---|---|
| Платёжный шлюз (СБП / банковский эквайринг) | REST + Webhook | Payment Service |
| Email / SMS провайдер (Yandex SES, SMSC) | REST | Notification Service |
| Anti-bot / CAPTCHA (Yandex SmartCaptcha) | REST | API Gateway, Queue Service |
| Облачное хранилище (S3-совместимое, Yandex Object Storage) | S3 API | Ticket Service, Event Service |
| CDN (Yandex CDN) | Pull / Push | Event Service (схема зала, статика) |

---

### 1.4 Верхнеуровневая схема HLD (C4, уровень Containers)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Клиенты                                      │
│   [Web SPA]  ──────────────────────────────────────────────────     │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ HTTPS
                    ┌───────▼────────┐
                    │   CDN / WAF    │  ← Статика SPA, изображения зала
                    │ (Yandex CDN)   │    Защита от DDoS, Cloudflare/Yandex WAF
                    └───────┬────────┘
                            │
                    ┌───────▼────────┐
                    │  L4 Balancer   │  ← Yandex Network Load Balancer
                    │  (TCP/TLS)     │    Terminates TLS, распределяет на Ingress
                    └───────┬────────┘
                            │
                    ┌───────▼────────┐
                    │  API Gateway   │  ← Rate limiting, JWT validation,
                    │  (NGINX/Kong)  │    маршрутизация, Anti-bot
                    └──┬──┬──┬──┬───┘
                       │  │  │  │
          ┌────────────┘  │  │  └──────────────────────────┐
          │               │  │                              │
   ┌──────▼──────┐  ┌─────▼──┴────┐  ┌──────────────┐  ┌──▼──────────┐
   │Auth Service │  │Event Service│  │Queue Service │  │ User Service│
   │(JWT,OAuth)  │  │(Каталог,    │  │(Virtual      │  │(Профиль,    │
   │             │  │ SeatMap)    │  │ Waiting Room)│  │ Анти-скальп)│
   └──────┬──────┘  └─────┬───────┘  └──────┬───────┘  └─────────────┘
          │               │                  │
          │         ┌─────▼────────────────────────────────────┐
          │         │         Inventory Service                 │
          │         │   (Redis SET NX: seat:{id}, TTL 600s)     │
          │         └──────────────────┬───────────────────────┘
          │                            │
          │                  ┌─────────▼────────┐
          │                  │  Order Service   │
          │                  │  (Saga Orchestr) │
          │                  └─────┬──────┬─────┘
          │                        │      │
          │               ┌────────▼──┐  ┌▼──────────────┐
          │               │ Payment   │  │ Ticket Service │
          │               │ Service   │  │ (PDF, QR-code) │
          │               └────┬──────┘  └───────┬────────┘
          │                    │                  │
          │            ┌───────▼──────────────────▼────────┐
          │            │        Kafka (Message Bus)         │
          │            │  topics: order.*, payment.*, seat.*│
          └────────────┴──────────────┬────────────────────┘
                                      │
                             ┌────────▼──────────┐
                             │ Notification Svc  │
                             │ (Email, SMS)      │
                             └───────────────────┘
```

---

## Part 2 — Базы данных

### 2.1 Алгоритм выбора БД (по методологии курса)

Для каждого сервиса применяем алгоритм:
1. Определяем **паттерн доступа**: OLTP / OLAP / Key-Value / Document / Search
2. Определяем **требования к согласованности**: Strong / Eventual
3. Определяем **нагрузку**: read-heavy / write-heavy / balanced
4. Определяем **масштабирование**: шардинг / репликация / партиционирование
5. Выбираем технологию

---

### 2.2 Выбор БД по сервисам

#### Auth Service — PostgreSQL

- **Паттерн**: OLTP, read-heavy (каждый запрос требует валидации)
- **Согласованность**: Strong (нельзя войти по устаревшим данным)
- **Нагрузка**: низкая запись (регистрация редка), высокое чтение (каждый запрос)
- **Решение**: PostgreSQL (надёжность, ACID) + **Redis Sentinel** (кэш сессий/JWT, TTL = время жизни токена)
- **Масштабирование**: read-реплика PostgreSQL для снятия нагрузки чтения; Redis Sentinel для HA кэша

#### User Service — PostgreSQL

- **Паттерн**: OLTP, balanced read/write
- **Согласованность**: Strong (лимиты на покупку должны быть точными — анти-скальпинг)
- **Решение**: PostgreSQL + Redis Sentinel (кэш профиля)
- **Масштабирование**: read-реплика

#### Event Service — PostgreSQL + Elasticsearch + Redis

- **Паттерн**: OLTP (управление событиями) + Full-text Search (поиск по каталогу)
- **Согласованность**: Eventual для поиска допустима (задержка индексирования 1–2 с)
- **Нагрузка**: read-heavy (схема зала читается тысячами одновременно)
- **Решение**:
  - PostgreSQL — источник истины: события, залы, схемы
  - Elasticsearch — поиск по каталогу (город, жанр, артист, дата)
  - Redis Sentinel — кэш схемы зала (агрессивное TTL = 5 с, инвалидация при изменении статуса мест)
- **Масштабирование**: ES-кластер 3 ноды; Redis Sentinel для HA

#### Inventory Service — Redis Cluster (primary) + PostgreSQL (persistence)

- **Паттерн**: Key-Value, экстремально write-heavy в момент старта продаж
- **Согласованность**: **Strong** (zero double-sell — приоритет CP в CAP)
- **Нагрузка**: 5 000 write ops/sec в пике (бронирование мест)
- **Решение**:
  - **Redis Cluster** — атомарное `SET NX seat:{seat_id} user:{user_id} EX 600`; шардинг по `seat_id`
  - PostgreSQL — персистентное хранилище статусов (синхронизируется через Kafka после подтверждения оплаты)
- **Масштабирование**: Redis Cluster (6 нод: 3 master + 3 replica); ~100 000 ops/sec на кластер — достаточно для 5 000 RPS бронирований
- **Обоснование выбора Redis**: атомарность SET NX на уровне команды исключает distributed lock overhead; TTL встроен в Redis нативно — таймер корзины реализуется без планировщика

#### Order Service — PostgreSQL (шардирование)

- **Паттерн**: OLTP, balanced
- **Согласованность**: Strong (статус заказа = финансовые данные)
- **Нагрузка**: пиковая запись в Event Day; история заказов — read-heavy
- **Решение**: PostgreSQL, шардинг по `user_id` (история заказов читается в контексте пользователя)
- **Масштабирование**: 4 шарда (масштабируются по мере роста)

#### Payment Service — PostgreSQL

- **Паттерн**: OLTP, write-heavy (каждый платёж = запись)
- **Согласованность**: Strong (финансовые операции, идемпотентность)
- **Решение**: PostgreSQL, отдельная БД (изолированный контур безопасности)
- **Идемпотентность**: таблица `payment_idempotency_keys` с UNIQUE constraint на `order_id + attempt_id`

#### Ticket Service — PostgreSQL + S3-совместимое хранилище

- **Паттерн**: OLTP (метаданные) + объектное хранилище (PDF, QR)
- **Решение**: PostgreSQL (метаданные билетов) + Yandex Object Storage S3 (PDF-файлы)

#### Notification Service — PostgreSQL (лог уведомлений)

- **Паттерн**: append-only write, редкое чтение
- **Решение**: PostgreSQL с партиционированием по дате

#### Queue Service — Redis Sorted Sets + Redis Pub/Sub

- **Паттерн**: очередь с приоритетами, real-time updates
- **Решение**: Redis Sorted Set (`ZADD queue:{event_id} timestamp user_id`) — атомарная операция добавления; Redis Pub/Sub для рассылки обновления позиции; WebSocket на клиенте

---

### 2.3 Сводная таблица баз данных

| Сервис | БД | Тип | Репликация | Шардинг |
|---|---|---|---|---|
| Auth Service | PostgreSQL | RDBMS | 1 read-реплика | Нет |
| Auth Service | Redis Sentinel | Key-Value | Sentinel (3 ноды) | Нет |
| User Service | PostgreSQL | RDBMS | 1 read-реплика | Нет |
| Event Service | PostgreSQL | RDBMS | 1 read-реплика | Нет |
| Event Service | Elasticsearch | Search Engine | 3-нодовый кластер (replication factor 1) | Нет (один индекс) |
| Event Service | Redis Sentinel | Key-Value | Sentinel (3 ноды) | Нет |
| **Inventory Service** | **Redis Cluster** | **Key-Value** | **3 master + 3 replica** | **По seat_id (hash slot)** |
| Inventory Service | PostgreSQL | RDBMS | 1 read-реплика | Нет |
| Order Service | PostgreSQL | RDBMS | 1 read-реплика | По user_id (4 шарда) |
| Payment Service | PostgreSQL | RDBMS | 1 read-реплика | Нет |
| Ticket Service | PostgreSQL | RDBMS | 1 read-реплика | Нет |
| Ticket Service | Yandex Object Storage | Object Storage | Built-in (managed) | Нет |
| Queue Service | Redis Sentinel | Key-Value | Sentinel (3 ноды) | Нет |

---

### 2.4 Репликация и шардинг — обоснование

**Репликация (read-реплики PostgreSQL):**
Все PostgreSQL-инстансы имеют минимум 1 синхронную read-реплику. Обоснование:
- Снижение нагрузки чтения на primary: для Order Service история заказов (~80% трафика) уходит на реплику
- Отказоустойчивость: при падении primary реплика переключается через pg_auto_failover / Patroni
- RPO < 30 сек обеспечивается синхронной репликацией для критичных сервисов (Inventory, Payment)

**Шардинг Order Service:**
Шардинг по `user_id` выбран потому, что основной паттерн доступа — "история заказов пользователя" — всегда содержит `user_id` в WHERE-условии. Запросы `ORDER BY event_id` (для организаторов) обрабатываются через агрегирующий запрос к нескольким шардам — редкий сценарий, задержка допустима.

**Redis Cluster для Inventory:**
Шардинг Redis по `seat_id` через встроенный hash slot механизм. Ключ `seat:{seat_id}` равномерно распределяется по 3 master-нодам. Это позволяет масштабировать Inventory Service горизонтально: каждая master-нода обслуживает ~1/3 мест и принимает ~1 700 ops/sec при 5 000 RPS бронирований.

---

### 2.5 Обновлённая схема HLD с базами данных

```
┌──────────────────┐         ┌─────────────────────────┐
│   Auth Service   │──────── │  PostgreSQL (auth_db)   │
│                  │──────── │  Redis Sentinel (sessions)│
└──────────────────┘         └─────────────────────────┘

┌──────────────────┐         ┌─────────────────────────┐
│   Event Service  │──────── │  PostgreSQL (events_db) │
│                  │──────── │  Elasticsearch (search) │
│                  │──────── │  Redis Sentinel (cache)  │
└──────────────────┘         └─────────────────────────┘

┌──────────────────┐         ┌─────────────────────────┐
│ Inventory Service│──────── │  Redis Cluster (seats)  │ ← PRIMARY: SET NX
│  [КРИТИЧЕСКИЙ]   │──────── │  PostgreSQL (inventory) │ ← PERSISTENCE
└──────────────────┘         └─────────────────────────┘

┌──────────────────┐         ┌─────────────────────────┐
│  Order Service   │──────── │  PostgreSQL (orders_db) │ ← 4 шарда по user_id
└──────────────────┘         └─────────────────────────┘

┌──────────────────┐         ┌─────────────────────────┐
│ Payment Service  │──────── │  PostgreSQL (payment_db)│
└──────────────────┘         └─────────────────────────┘

┌──────────────────┐         ┌──────────────────────────┐
│  Ticket Service  │──────── │  PostgreSQL (tickets_db) │
│                  │──────── │  Yandex Object Storage   │ ← PDF, QR
└──────────────────┘         └──────────────────────────┘

┌──────────────────┐         ┌─────────────────────────┐
│  Queue Service   │──────── │  Redis Sentinel (queue) │ ← Sorted Sets
└──────────────────┘         └─────────────────────────┘
```

---

## Part 3 — Дополнительные компоненты

### 3.1 Обязательные компоненты (MUST)

| Компонент | Обоснование | Реализация |
|---|---|---|
| **L4 Load Balancer** | Распределение входящего TCP-трафика между инстансами API Gateway / NGINX Ingress. Без него горизонтальное масштабирование невозможно — единая точка входа по IP. Также необходим для TLS termination. | Yandex Network Load Balancer (managed, Anycast IP) |
| **CDN** | Статика SPA (JS, CSS, HTML) и изображения схем залов (~5 МБ/событие) должны отдаваться с edge-узлов. Без CDN при 300 000 DAU в Event Day NGINX захлебнётся на статике. Снижает latency для пользователей в регионах. | Yandex CDN + Yandex Object Storage как origin |
| **WAF** | Защита от OWASP Top 10 (SQL Injection, XSS, CSRF). Особенно важно для тикетинговой платформы: финансовые транзакции, ПДн пользователей (152-ФЗ). Anti-bot правила на уровне WAF перехватывают скальперов ещё до попадания в API Gateway. | Yandex SmartWeb Security (WAF + Anti-bot) |
| **Кэш (Redis)** | Три изолированных кластера: (1) Inventory — SET NX для гарантии уникальности бронирования; (2) Session/JWT — снятие нагрузки с Auth Service; (3) SeatMap cache — схема зала читается 8 000 раз/сек в пике, без кэша PostgreSQL не справится. | Redis Cluster (Inventory), Redis Sentinel (Session, SeatMap) |
| **Message Broker (Kafka)** | Декаплинг между Order Service и downstream-сервисами (Ticket, Notification, Payment webhook). Без брокера: синхронный вызов Ticket Service из Order Service создаёт coupling и риск потери данных при падении. Kafka даёт гарантию at-least-once delivery, replay, и масштабирование consumers независимо. | Apache Kafka, 3 брокера, replication factor 3. Топики: order.events, payment.events, seat.events |
| **Virtual Waiting Room** | Без очереди 10 000 RPS в первую секунду старта продаж кладут Inventory Service и Redis. Очередь сглаживает нагрузку, пропуская пользователей пачками по мере освобождения capacity. Ключевой инвариант: пользователь не должен "провалиться" в систему раньше, чем она готова. | Queue Service на Redis Sorted Sets + WebSocket push через NGINX |
| **Observability (мониторинг + логирование)** | Без наблюдаемости невозможно диагностировать инциденты в Event Day. Нужно: метрики RPS/latency/error rate (особенно Inventory Service), логи транзакций (для аудита по 152-ФЗ), трейсы распределённых запросов (Saga через несколько сервисов). | **Метрики:** Victoria Metrics + Grafana. **Логи:** Loki. **Трейсы:** Jaeger (через OpenTelemetry Collector) |
| **CI/CD** | Без автоматизации деплоя: ручной деплой в Event Day — catastrophic failure risk. Canary deploy позволяет катить обновления без downtime. | GitLab CI: build → unit tests → security scan → canary deploy → full rollout |
| **Резервное копирование** | RPO < 30 сек для Inventory (финансовые данные). 152-ФЗ требует сохранности ПДн. Потеря данных о проданных билетах — критический бизнес-инцидент. | WAL-G для PostgreSQL (WAL streaming в Yandex Object Storage). Daily snapshots для всех БД. Redis AOF persistence для Inventory Redis |

---

### 3.2 Рекомендуемые компоненты (SHOULD)

| Компонент | Обоснование | Реализация |
|---|---|---|
| **Service Mesh** | При 8 сервисах управление retry, timeout, circuit breaker через код каждого сервиса — дублирование. Service Mesh выносит это на инфраструктурный уровень. Circuit breaker перед Inventory Service защищает от каскадного падения при перегрузке. | Istio (sidecar Envoy) или Linkerd (легче) |
| **API Rate Limiting (расширенный)** | Базовый rate limiting в API Gateway (по IP) недостаточен против скальперов с распределёнными ботами. Нужен: rate limit по user_id, по платёжным реквизитам, sliding window. | Nginx + Lua (rate-limit by user_id в Redis) или Kong с Rate Limiting Advanced plugin |
| **Feature Flags** | Позволяет включать Virtual Waiting Room только для конкретных событий (мегаконцерты), не для всех. Безопасный rollout новых фич без деплоя. | LaunchDarkly или самописная реализация на Redis |
| **Geo DNS / GeoDNS** | При расширении на несколько регионов России (Москва, СПб, Екатеринбург) пользователи маршрутизируются на ближайший кластер. Снижает latency на ~30–50 мс для региональных пользователей. | Yandex DNS с geo-routing правилами |
| **DLP / Data Masking** | ПДн пользователей в логах должны быть маскированы (152-ФЗ). Без этого Loki хранит email и телефоны в открытом виде. | OpenTelemetry Collector с processor для маскирования полей |

---

### 3.3 Итоговая архитектурная схема HLD (с дополнительными компонентами)

```
                    ┌──────────────────────────────┐
                    │   Пользователи (Web SPA)      │
                    └──────────────┬───────────────┘
                                   │ HTTPS
                    ┌──────────────▼───────────────┐
                    │     Yandex CDN + WAF          │
                    │  (статика, SmartWeb Security) │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  Yandex NLB (L4 балансер)    │
                    │  Anycast IP, TLS termination  │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │   NGINX Ingress (K8s)        │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │       API Gateway             │
                    │ (Rate Limit, JWT Auth,        │
                    │  маршрутизация, Anti-bot)     │
                    └───┬────┬────┬────┬────┬──────┘
                        │    │    │    │    │
         ┌──────────────┘    │    │    │    └──────────────────┐
         │              ┌────┘    │    └────┐                  │
         ▼              ▼         ▼         ▼                  ▼
   ┌───────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐
   │   Auth    │  │  Event   │  │    Queue     │  │      User        │
   │  Service  │  │  Service │  │   Service    │  │     Service      │
   │           │  │(Каталог, │  │(Virtual WR,  │  │(Профиль, лимиты) │
   │PostgreSQL │  │SeatMap,  │  │ Redis SortedSet│ │PostgreSQL,Redis │
   │Redis Sent.│  │Elastic,  │  │ WebSocket)   │  │                  │
   └───────────┘  │Redis)    │  └──────┬───────┘  └──────────────────┘
                  └─────┬────┘         │
                        │              │ (допуск в систему)
                  ┌─────▼──────────────▼──────────────────────┐
                  │              Inventory Service             │
                  │    Redis Cluster: SET NX seat:{id}         │
                  │    EX 600 → гарантия уникальности брони    │
                  │    PostgreSQL: персистентность             │
                  └─────────────────┬─────────────────────────┘
                                    │
                  ┌─────────────────▼─────────────────────────┐
                  │               Order Service                │
                  │      Saga Orchestrator (Choreography)      │
                  │      PostgreSQL (4 шарда по user_id)       │
                  └──────────┬──────────────┬──────────────────┘
                             │              │
              ┌──────────────▼──┐     ┌─────▼────────────────┐
              │ Payment Service │     │     Kafka             │
              │ PostgreSQL,     │     │  order.events         │
              │ Idempotency Key │     │  payment.events       │
              └─────────────────┘     │  seat.events          │
                                      └──┬──────────┬─────────┘
                                         │          │
                              ┌──────────▼──┐  ┌────▼──────────────┐
                              │   Ticket    │  │  Notification     │
                              │   Service   │  │    Service        │
                              │ (PDF, QR,   │  │  (Email, SMS)     │
                              │  S3/YOS)    │  │                   │
                              └─────────────┘  └───────────────────┘

─────────────────── Observability Layer ────────────────────────────
  Victoria Metrics ← метрики всех сервисов (OpenTelemetry Collector)
  Loki             ← логи (structured JSON)
  Jaeger           ← distributed traces (Saga, критический путь)
  Grafana          ← дашборды, алерты (SLO/SLA мониторинг)
─────────────────────────────────────────────────────────────────────

─────────────────── CI/CD ──────────────────────────────────────────
  GitLab CI: build → test → scan → canary(5%) → rollout(100%)
─────────────────────────────────────────────────────────────────────

─────────────────── Backup ─────────────────────────────────────────
  WAL-G → Yandex Object Storage (WAL streaming, daily snapshot)
  Redis AOF → Yandex Object Storage (для Inventory Redis Cluster)
─────────────────────────────────────────────────────────────────────
```

---

### 3.4 Обоснование ключевых архитектурных решений

#### Почему Microservices, а не Space-Based Architecture

Как показано в HW1, SBA даёт экстремальную throughput без очереди, но стоимость RAM и операционная сложность неприемлемы для MVP. Выбор Microservices с Virtual Waiting Room позволяет:
- Сгладить пиковую нагрузку (10 000 RPS → ~1 500 RPS на Inventory после очереди)
- Удержаться в рамках разумного бюджета
- Получить зрелую экосистему инструментов

Inventory Service реализует **локальный SBA-подход**: Redis Cluster держит весь инвентарь в памяти, а SET NX обеспечивает Single-Writer семантику на уровне ключа. Это гибрид: MSA-структура + SBA-механизм корректности.

#### Kafka vs синхронные вызовы

Order Service НЕ вызывает Ticket Service и Notification Service синхронно. Причина: если PDF-генерация или SMTP зависнут, пользователь получит timeout при оплате — критический UX-провал. Kafka обеспечивает: (1) decoupling, (2) retry без потери события, (3) независимое масштабирование воркеров.

#### Redis Sentinel vs Redis Cluster (для неинвентарных кэшей)

Для Session, SeatMap cache, Queue — объёмы данных небольшие (< 1 ГБ), шардинг не нужен. Redis Sentinel (3 ноды: 1 master + 2 replica + 3 sentinel) даёт HA с автоматическим failover без сложности Cluster-режима.

---

### 3.5 Соответствие НФТ

| НФТ | Как обеспечивается |
|---|---|
| Пиковый throughput 10 000 RPS | Virtual Waiting Room + Redis Cluster Inventory + горизонтальное масштабирование K8s |
| Latency P99 < 500 мс (бронь) | Redis SET NX < 1 мс + API Gateway < 5 мс + сеть < 10 мс; итого << 500 мс |
| Zero double-sell | Redis SET NX — атомарная операция, невозможна гонка |
| Доступность 99.99% | Redis Sentinel/Cluster HA, PostgreSQL с failover (Patroni), мульти-AZ Kubernetes |
| RPO < 30 сек | WAL-G streaming + Redis AOF |
| 152-ФЗ | Все данные в Yandex Cloud (российские ЦОД), DLP маскирование в логах |
| Идемпотентность платежей | Таблица idempotency_keys в Payment Service (UNIQUE constraint) |
| Эластичность < 2 мин | Kubernetes HPA по CPU/RPS метрике; Kafka Consumer Group автоматически ребалансирует |