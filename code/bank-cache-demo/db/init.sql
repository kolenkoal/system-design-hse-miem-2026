CREATE TABLE accounts (
    id           SERIAL PRIMARY KEY,
    account_number VARCHAR(20) UNIQUE NOT NULL,
    balance      DECIMAL(15, 2) NOT NULL DEFAULT 0,
    account_type VARCHAR(20)    NOT NULL DEFAULT 'checking',
    owner_name   VARCHAR(100)   NOT NULL,
    created_at   TIMESTAMP      DEFAULT NOW()
);

CREATE TABLE transactions (
    id              SERIAL PRIMARY KEY,
    from_account_id INTEGER REFERENCES accounts(id),
    to_account_id   INTEGER REFERENCES accounts(id),
    amount          DECIMAL(15, 2) NOT NULL,
    description     TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Намеренно нет индексов на from_account_id / to_account_id —
-- seq scan по 500k строк нужен для демонстрации разницы в скорости с кэшем и без.

CREATE TABLE exchange_rates (
    id            SERIAL PRIMARY KEY,
    from_currency VARCHAR(3)     NOT NULL,
    to_currency   VARCHAR(3)     NOT NULL,
    rate          DECIMAL(12, 6) NOT NULL,
    updated_at    TIMESTAMP      DEFAULT NOW()
);

-- Accounts
INSERT INTO accounts (account_number, balance, account_type, owner_name) VALUES
('ACC-0001', 125000.00, 'checking', 'Ivan Petrov'),
('ACC-0002',  43500.00, 'savings',  'Maria Sidorova'),
('ACC-0003', 980000.00, 'checking', 'Alexey Kozlov'),
('ACC-0004',   7200.00, 'savings',  'Elena Novikova'),
('ACC-0005',  55000.00, 'checking', 'Dmitry Sokolov'),
('ACC-0006', 210000.00, 'savings',  'Olga Mikhailova'),
('ACC-0007',  18700.00, 'checking', 'Sergey Fedorov'),
('ACC-0008', 340000.00, 'savings',  'Natalya Volkova'),
('ACC-0009',  62000.00, 'checking', 'Andrey Popov'),
('ACC-0010',   5500.00, 'savings',  'Yulia Morozova');

-- Exchange rates
INSERT INTO exchange_rates (from_currency, to_currency, rate) VALUES
('USD', 'RUB', 90.150000),
('USD', 'EUR',  0.921000),
('USD', 'GBP',  0.786000),
('USD', 'CNY',  7.248000),
('USD', 'JPY', 149.820000),
('EUR', 'RUB', 97.880000),
('EUR', 'USD',  1.086000),
('EUR', 'GBP',  0.853000),
('RUB', 'USD',  0.011093),
('RUB', 'EUR',  0.010216),
('RUB', 'CNY',  0.080442);

-- 500 000 транзакций — нужно, чтобы агрегация занимала секунды без кэша
INSERT INTO transactions (from_account_id, to_account_id, amount, description, created_at)
SELECT
    (floor(random() * 10) + 1)::int,
    (floor(random() * 10) + 1)::int,
    round((random() * 50000 + 100)::numeric, 2),
    (ARRAY['salary','rent','groceries','utilities','transfer','loan_payment','insurance'])[ceil(random() * 7)::int],
    NOW() - (random() * interval '730 days')
FROM generate_series(1, 500000);
