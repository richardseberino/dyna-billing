-- Schema da base de billing Dynatrace
-- Executado automaticamente pelo container MySQL na primeira inicialização.

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS subscriptions (
    uuid            VARCHAR(64)    NOT NULL PRIMARY KEY,
    name            VARCHAR(255)   NULL,
    status          VARCHAR(64)    NULL,
    start_date      DATE           NULL,
    end_date        DATE           NULL,
    budget_total    DECIMAL(20,6)  NULL COMMENT 'Total de créditos do contrato (moeda do contrato)',
    budget_used     DECIMAL(20,6)  NULL COMMENT 'Créditos consumidos informados pela Dynatrace',
    currency        VARCHAR(8)     NULL,
    raw_json        JSON           NULL,
    updated_at      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS environments (
    environment_id  VARCHAR(64)    NOT NULL PRIMARY KEY,
    name            VARCHAR(255)   NULL,
    updated_at      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Custo/uso diário por ambiente e recurso (capability da DPS)
CREATE TABLE IF NOT EXISTS daily_cost (
    id               BIGINT         NOT NULL AUTO_INCREMENT PRIMARY KEY,
    subscription_uuid VARCHAR(64)   NOT NULL,
    usage_date       DATE           NOT NULL,
    environment_id   VARCHAR(64)    NOT NULL,
    capability_key   VARCHAR(128)   NOT NULL,
    capability_name  VARCHAR(255)   NULL,
    cost             DECIMAL(20,6)  NOT NULL DEFAULT 0 COMMENT 'Custo na moeda do contrato',
    currency         VARCHAR(8)     NULL,
    usage_value      DECIMAL(24,6)  NULL,
    usage_unit       VARCHAR(64)    NULL,
    collected_at     TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_day_env_cap (subscription_uuid, usage_date, environment_id, capability_key),
    KEY ix_date (usage_date),
    KEY ix_env (environment_id),
    KEY ix_cap (capability_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Cotações (moeda do contrato -> BRL)
CREATE TABLE IF NOT EXISTS fx_rates (
    rate_date   DATE          NOT NULL,
    currency    VARCHAR(8)    NOT NULL,
    rate_brl    DECIMAL(18,6) NOT NULL,
    source      VARCHAR(32)   NOT NULL,
    PRIMARY KEY (rate_date, currency)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS collector_runs (
    id           BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    started_at   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at  TIMESTAMP     NULL,
    status       VARCHAR(16)   NOT NULL DEFAULT 'running',
    rows_upserted INT          NOT NULL DEFAULT 0,
    message      TEXT          NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Usuários da interface web (também criada automaticamente pelo app ao iniciar)
CREATE TABLE IF NOT EXISTS users (
    id            INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(64)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    active        TINYINT(1)   NOT NULL DEFAULT 1,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login    TIMESTAMP    NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
