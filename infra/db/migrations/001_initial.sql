-- ============================================================
-- 001_initial.sql — Mini HFT System initial schema
-- ============================================================
-- Run order: this script is idempotent (IF NOT EXISTS everywhere).
-- In production: use Alembic for version-controlled migrations.
-- ============================================================

-- Orders: one row per order submission. Immutable after insertion;
-- status updates use separate UPDATE statements.
CREATE TABLE IF NOT EXISTS orders (
    order_id          UUID        PRIMARY KEY,
    symbol            VARCHAR(20) NOT NULL,
    side              VARCHAR(10) NOT NULL,    -- BUY | SELL
    order_type        VARCHAR(20) NOT NULL,    -- LIMIT | MARKET
    status            VARCHAR(30) NOT NULL,
    quantity          INTEGER     NOT NULL CHECK (quantity > 0),
    filled_quantity   INTEGER     NOT NULL DEFAULT 0,
    price             NUMERIC(18, 8),          -- NULL for MARKET orders
    avg_fill_price    NUMERIC(18, 8),
    time_in_force     VARCHAR(10) NOT NULL DEFAULT 'GTC',
    strategy_id       VARCHAR(100),
    client_order_id   VARCHAR(100),

    -- Nanosecond timestamps for latency analysis
    created_at_ns     BIGINT      NOT NULL,
    received_at_ns    BIGINT,
    processed_at_ns   BIGINT,
    acked_at_ns       BIGINT,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Trades: one row per individual fill. An order may generate many trades
-- (if it sweeps multiple price levels). Append-only — never updated.
CREATE TABLE IF NOT EXISTS trades (
    trade_id          UUID        PRIMARY KEY,
    symbol            VARCHAR(20) NOT NULL,
    price             NUMERIC(18, 8) NOT NULL,
    quantity          INTEGER     NOT NULL CHECK (quantity > 0),
    notional          NUMERIC(22, 8) GENERATED ALWAYS AS (price * quantity) STORED,
    aggressor_side    VARCHAR(10) NOT NULL,
    maker_order_id    UUID        NOT NULL REFERENCES orders(order_id),
    taker_order_id    UUID        NOT NULL REFERENCES orders(order_id),
    timestamp_ns      BIGINT      NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ──────────────────────────────────────────────────────────────────

-- Orders: frequent queries by symbol + status (open orders per instrument)
CREATE INDEX IF NOT EXISTS idx_orders_symbol_status
    ON orders(symbol, status);

-- Orders: time-series queries (recent activity)
CREATE INDEX IF NOT EXISTS idx_orders_created_at
    ON orders(created_at DESC);

-- Orders: strategy performance attribution
CREATE INDEX IF NOT EXISTS idx_orders_strategy_id
    ON orders(strategy_id) WHERE strategy_id IS NOT NULL;

-- Trades: market data queries (VWAP, volume by symbol)
CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts
    ON trades(symbol, timestamp_ns DESC);

-- Trades: order fill history
CREATE INDEX IF NOT EXISTS idx_trades_maker_order
    ON trades(maker_order_id);

CREATE INDEX IF NOT EXISTS idx_trades_taker_order
    ON trades(taker_order_id);

-- ── Latency view ──────────────────────────────────────────────────────────────
-- Precomputed latency metrics for the dashboard.
-- Recompute on-demand or materialise with pg_cron.
CREATE OR REPLACE VIEW order_latency_stats AS
SELECT
    symbol,
    COUNT(*)                                                       AS order_count,
    ROUND(AVG((processed_at_ns - received_at_ns) / 1000.0), 2)   AS avg_processing_us,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP
        (ORDER BY (processed_at_ns - received_at_ns) / 1000.0), 2) AS p50_us,
    ROUND(PERCENTILE_CONT(0.99) WITHIN GROUP
        (ORDER BY (processed_at_ns - received_at_ns) / 1000.0), 2) AS p99_us,
    ROUND(PERCENTILE_CONT(0.999) WITHIN GROUP
        (ORDER BY (processed_at_ns - received_at_ns) / 1000.0), 2) AS p999_us
FROM orders
WHERE processed_at_ns IS NOT NULL
  AND received_at_ns  IS NOT NULL
GROUP BY symbol;
