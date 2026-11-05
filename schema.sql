CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('operator', 'admin', 'techadmin')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by BIGINT REFERENCES users(id) ON DELETE SET NULL
);
INSERT INTO settings(key,value) VALUES
    ('weighing_price','30000'), ('entry_service_price','30000'),
    ('reload_service_price','30000'), ('zone_daily_rate','30000'),
    ('weighing_enabled','true'), ('entry_service_enabled','true'),
    ('reload_service_enabled','true'), ('zone_daily_enabled','true')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS tariff_history (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL,
    amount BIGINT NOT NULL CHECK(amount >= 0),
    enabled BOOLEAN NOT NULL,
    applied_to_existing BOOLEAN NOT NULL DEFAULT FALSE,
    affected_rows INTEGER NOT NULL DEFAULT 0,
    changed_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tariff_history_time ON tariff_history(changed_at DESC);

CREATE TABLE IF NOT EXISTS daily_sequences (
    business_date DATE PRIMARY KEY,
    next_value INTEGER NOT NULL CHECK (next_value > 0)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id UUID PRIMARY KEY,
    filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    imported_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_rows INTEGER NOT NULL DEFAULT 0,
    inserted_rows INTEGER NOT NULL DEFAULT 0,
    skipped_rows INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS weighings (
    id BIGSERIAL PRIMARY KEY,
    receipt_no TEXT NOT NULL UNIQUE,
    plate_number TEXT NOT NULL,
    plate_search TEXT NOT NULL,
    price BIGINT NOT NULL CHECK (price >= 0),
    weight_kg BIGINT NOT NULL DEFAULT 0 CHECK (weight_kg >= 0),
    weighing_fee BIGINT NOT NULL DEFAULT 0 CHECK (weighing_fee >= 0),
    entry_fee BIGINT NOT NULL DEFAULT 0 CHECK (entry_fee >= 0),
    reload_fee BIGINT NOT NULL DEFAULT 0 CHECK (reload_fee >= 0),
    entry_service BOOLEAN NOT NULL DEFAULT FALSE,
    reload_service BOOLEAN NOT NULL DEFAULT FALSE,
    public_token TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    business_date DATE NOT NULL,
    paid_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    payment_method TEXT NOT NULL DEFAULT 'cash' CHECK (payment_method IN ('cash', 'card', 'bank')),
    created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'web',
    legacy_id BIGINT,
    legacy_receipt_no TEXT,
    legacy_fingerprint TEXT UNIQUE,
    import_batch_id UUID REFERENCES import_batches(id) ON DELETE SET NULL
);

ALTER TABLE weighings ADD COLUMN IF NOT EXISTS payment_method TEXT NOT NULL DEFAULT 'cash';
ALTER TABLE weighings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE weighings ADD COLUMN IF NOT EXISTS weight_kg BIGINT NOT NULL DEFAULT 0;
ALTER TABLE weighings ADD COLUMN IF NOT EXISTS weighing_fee BIGINT NOT NULL DEFAULT 0;
ALTER TABLE weighings ADD COLUMN IF NOT EXISTS entry_fee BIGINT NOT NULL DEFAULT 0;
ALTER TABLE weighings ADD COLUMN IF NOT EXISTS reload_fee BIGINT NOT NULL DEFAULT 0;
ALTER TABLE weighings ADD COLUMN IF NOT EXISTS entry_service BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE weighings ADD COLUMN IF NOT EXISTS reload_service BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE weighings ADD COLUMN IF NOT EXISTS public_token TEXT;

UPDATE weighings
SET weighing_fee = price
WHERE weighing_fee = 0 AND entry_fee = 0 AND reload_fee = 0 AND price > 0;

CREATE INDEX IF NOT EXISTS idx_weighings_created_at ON weighings (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_weighings_updated_at ON weighings (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_weighings_business_date ON weighings (business_date DESC);
CREATE INDEX IF NOT EXISTS idx_weighings_status_date ON weighings (status, business_date DESC);
CREATE INDEX IF NOT EXISTS idx_weighings_plate_search ON weighings (plate_search);
CREATE INDEX IF NOT EXISTS idx_weighings_created_by ON weighings (created_by, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_weighings_public_token ON weighings (public_token) WHERE public_token IS NOT NULL;

CREATE TABLE IF NOT EXISTS backup_tokens (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS login_attempts (
    identifier TEXT PRIMARY KEY,
    failure_count INTEGER NOT NULL DEFAULT 0,
    first_failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    blocked_until TIMESTAMPTZ
);

ALTER TABLE weighings ADD COLUMN IF NOT EXISTS client_request_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_weighing_request ON weighings(client_request_id) WHERE client_request_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS print_attempts (
    id UUID PRIMARY KEY,
    weighing_id BIGINT NOT NULL REFERENCES weighings(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    state TEXT NOT NULL CHECK (state IN ('requested','dialog_closed','spooled','confirmed','failed')),
    method TEXT NOT NULL CHECK (method IN ('browser','agent'))
);
CREATE INDEX IF NOT EXISTS idx_print_weighing ON print_attempts(weighing_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_print_created ON print_attempts(created_at DESC);

-- Extend roles on existing installations as well as new databases.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='users'::regclass AND conname='users_zone_role_check') THEN
        ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
        ALTER TABLE users ADD CONSTRAINT users_zone_role_check CHECK
            (role IN ('operator','admin','techadmin','parkovka_tashqi','kpp2','ayritom'));
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS zone_visits (
    id BIGSERIAL PRIMARY KEY,
    plate TEXT NOT NULL, plate_key TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('border','zone')),
    location TEXT NOT NULL CHECK (location IN ('parking','inside','closed')),
    entry_gate TEXT CHECK (entry_gate IS NULL OR entry_gate IN ('kpp2','ayritom')),
    parking_departed_at TIMESTAMPTZ,
    entered_at TIMESTAMPTZ,
    started TIMESTAMPTZ NOT NULL, daily_rate BIGINT NOT NULL CHECK (daily_rate>=0)
);
DO $$ BEGIN
    ALTER TABLE zone_visits DROP CONSTRAINT IF EXISTS zone_visits_daily_rate_check;
    ALTER TABLE zone_visits ADD CONSTRAINT zone_visits_daily_rate_check CHECK (daily_rate >= 0);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS zone_one_active ON zone_visits(plate_key) WHERE location <> 'closed';
CREATE INDEX IF NOT EXISTS zone_queue ON zone_visits(location,purpose,id);
CREATE INDEX IF NOT EXISTS zone_plate_history ON zone_visits(plate_key,id DESC);
CREATE TABLE IF NOT EXISTS zone_events (
    id BIGSERIAL PRIMARY KEY, request_key UUID UNIQUE NOT NULL,
    visit_id BIGINT NOT NULL REFERENCES zone_visits(id),
    occurred TIMESTAMPTZ NOT NULL, point TEXT NOT NULL,
    action TEXT NOT NULL, location TEXT NOT NULL, operator_name TEXT NOT NULL,
    days INTEGER NOT NULL, previous_paid BIGINT NOT NULL,
    amount BIGINT NOT NULL CHECK (amount>=0),
    method TEXT NOT NULL CHECK(method IN ('cash','card','bank','none'))
);

-- Non-destructive v2 migration. A Free Zone vehicle remains in the outside
-- group until a gate confirms entry. Existing transfer rows are preserved as
-- parking visits and their departure timestamp is retained as metadata.
ALTER TABLE zone_visits ADD COLUMN IF NOT EXISTS entry_gate TEXT;
ALTER TABLE zone_visits ADD COLUMN IF NOT EXISTS parking_departed_at TIMESTAMPTZ;
ALTER TABLE zone_visits ADD COLUMN IF NOT EXISTS entered_at TIMESTAMPTZ;
UPDATE zone_visits v
SET parking_departed_at = COALESCE(
        (SELECT MAX(e.occurred) FROM zone_events e
         WHERE e.visit_id=v.id AND e.point='parkovka_tashqi' AND e.action='out'),
        v.started
    ),
    location = 'parking'
WHERE v.location = 'transfer';
UPDATE zone_visits v
SET entry_gate = (
        SELECT e.point FROM zone_events e
        WHERE e.visit_id=v.id AND e.action='in' AND e.point IN ('kpp2','ayritom')
        ORDER BY e.id DESC LIMIT 1
    ),
    entered_at = COALESCE(v.entered_at, (
        SELECT e.occurred FROM zone_events e
        WHERE e.visit_id=v.id AND e.action='in' AND e.point IN ('kpp2','ayritom')
        ORDER BY e.id DESC LIMIT 1
    ))
WHERE v.location='inside' AND v.entry_gate IS NULL;
DO $$ BEGIN
    ALTER TABLE zone_visits DROP CONSTRAINT IF EXISTS zone_visits_location_check;
    ALTER TABLE zone_visits DROP CONSTRAINT IF EXISTS zone_visits_location_v2_check;
    ALTER TABLE zone_visits ADD CONSTRAINT zone_visits_location_v2_check
        CHECK (location IN ('parking','inside','closed'));
    ALTER TABLE zone_visits DROP CONSTRAINT IF EXISTS zone_visits_entry_gate_check;
    ALTER TABLE zone_visits ADD CONSTRAINT zone_visits_entry_gate_check
        CHECK (entry_gate IS NULL OR entry_gate IN ('kpp2','ayritom'));
END $$;
CREATE INDEX IF NOT EXISTS zone_event_visit ON zone_events(visit_id,id);
CREATE INDEX IF NOT EXISTS zone_event_time ON zone_events(occurred DESC);
CREATE INDEX IF NOT EXISTS zone_event_point_recent ON zone_events(point,id DESC);
CREATE INDEX IF NOT EXISTS zone_active_dashboard
    ON zone_visits(location,purpose,entry_gate) WHERE location <> 'closed';
CREATE INDEX IF NOT EXISTS zone_inside_gate
    ON zone_visits(entry_gate,id) WHERE location='inside';
CREATE INDEX IF NOT EXISTS zone_active_plate_prefix
    ON zone_visits(plate_key text_pattern_ops) WHERE location <> 'closed';
CREATE INDEX IF NOT EXISTS zone_weigh_paid_time ON weighings(paid_at) WHERE status='paid';
