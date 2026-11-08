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
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='users'::regclass AND conname='users_monitor_role_check') THEN
        ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
        ALTER TABLE users DROP CONSTRAINT IF EXISTS users_zone_role_check;
        ALTER TABLE users DROP CONSTRAINT IF EXISTS users_display_role_check;
        ALTER TABLE users ADD CONSTRAINT users_monitor_role_check CHECK
            (role IN ('operator','admin','techadmin','parkovka_tashqi','kpp2','ayritom','display','observer'));
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

CREATE TABLE IF NOT EXISTS devices (
 id UUID PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL,
 approved BOOLEAN NOT NULL DEFAULT FALSE,blocked BOOLEAN NOT NULL DEFAULT FALSE,
 last_seen TIMESTAMPTZ,last_user TEXT,ip TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS device_sessions (
 token_hash TEXT PRIMARY KEY,device_id UUID NOT NULL REFERENCES devices(id),
 user_id BIGINT NOT NULL REFERENCES users(id),password_stamp TEXT NOT NULL,expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS device_events (
 id BIGSERIAL PRIMARY KEY,device_id UUID NOT NULL REFERENCES devices(id),
 event TEXT NOT NULL,occurred TIMESTAMPTZ NOT NULL DEFAULT NOW(),previous_seen TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS device_events_time ON device_events(occurred DESC);
ALTER TABLE devices ADD COLUMN IF NOT EXISTS connected BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS station_hash TEXT UNIQUE;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS tablet_hash TEXT UNIQUE;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS hw_id TEXT;
CREATE INDEX IF NOT EXISTS idx_devices_hw_id ON devices(hw_id) WHERE hw_id IS NOT NULL;
ALTER TABLE zone_visits ADD COLUMN IF NOT EXISTS station_id UUID REFERENCES devices(id);
ALTER TABLE zone_visits ADD COLUMN IF NOT EXISTS station_local_id BIGINT;
CREATE UNIQUE INDEX IF NOT EXISTS zone_local_ticket ON zone_visits(station_id,station_local_id);
CREATE TABLE IF NOT EXISTS station_events(device_id UUID REFERENCES devices(id),event_id UUID NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(device_id,event_id));

CREATE INDEX IF NOT EXISTS weighings_paid_ledger_time ON weighings ((COALESCE(paid_at,created_at))) WHERE status='paid';
ALTER TABLE zone_visits ADD COLUMN IF NOT EXISTS queue_no TEXT;
ALTER TABLE weighings ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ;
ALTER TABLE weighings ALTER COLUMN received_at SET DEFAULT NOW();
ALTER TABLE zone_events ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ;
ALTER TABLE zone_events ALTER COLUMN received_at SET DEFAULT NOW();
CREATE TABLE IF NOT EXISTS receipt_jobs (
 id UUID PRIMARY KEY, event_id BIGINT NOT NULL REFERENCES zone_events(id),
 user_id BIGINT NOT NULL REFERENCES users(id), device_id UUID REFERENCES devices(id),
 state TEXT NOT NULL CHECK(state IN ('requested','sent','confirmed','failed','cancelled','unknown')),
 method TEXT NOT NULL DEFAULT 'android', detail TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS receipt_jobs_event ON receipt_jobs(event_id,created_at DESC);
CREATE TABLE IF NOT EXISTS transfer_imports (digest TEXT PRIMARY KEY, imported_at TIMESTAMPTZ DEFAULT NOW(), imported_by BIGINT REFERENCES users(id));
CREATE INDEX IF NOT EXISTS weighings_received_time ON weighings(received_at DESC);
CREATE INDEX IF NOT EXISTS zone_received_time ON zone_events(received_at DESC);
CREATE INDEX IF NOT EXISTS weighing_operation_time ON weighings ((COALESCE(paid_at,cancelled_at,created_at)));
CREATE OR REPLACE VIEW operation_ledger AS
 SELECT 'S-'||w.id AS operation_id,w.id,'scale'::text AS kind,w.receipt_no,''::text AS queue_no,
 w.plate_number AS plate,COALESCE(w.paid_at,w.cancelled_at,w.created_at) AS occurred,w.received_at,
 'scale'::text AS point,w.created_by AS operator_id,COALESCE(u.username,'Import') AS operator_name,
 w.payment_method AS method,w.price AS amount,w.status,
 CONCAT_WS(', ','Vazn o‘lchash',CASE WHEN w.entry_service THEN 'Hududga kirish' END,CASE WHEN w.reload_service THEN 'Qayta yuklash' END) AS reason,
 w.weight_kg,w.weighing_fee,w.entry_fee,w.reload_fee,0::bigint AS parking_fee,
 ''::text AS purpose,w.created_at AS entry_time,w.paid_at AS exit_time,1::integer AS days,0::bigint AS daily_rate,
 'weigh'::text AS action,''::text AS location,w.entry_service,w.reload_service,w.source
 FROM weighings w LEFT JOIN users u ON u.id=w.created_by
 UNION ALL
 SELECT 'Z-'||e.id,e.id,'zone','AFZ-'||e.id,COALESCE(v.queue_no,'AFZ-V'||v.id),v.plate,e.occurred,e.received_at,
 e.point,u.id,e.operator_name,e.method,e.amount,'paid',
 CASE WHEN e.point='parkovka_tashqi' THEN 'Parkovka' ELSE 'Hudud' END||' — '||CASE WHEN e.action='in' THEN 'Kirish' ELSE 'Chiqish' END||' — '||CASE WHEN v.purpose='zone' THEN 'Free Zone' ELSE 'Ayritom navbati' END,
 0::bigint,0::bigint,0::bigint,0::bigint,e.amount,v.purpose,v.started,
 CASE WHEN e.action='out' THEN e.occurred END,e.days,v.daily_rate,e.action,e.location,FALSE,FALSE,e.point
 FROM zone_events e JOIN zone_visits v ON v.id=e.visit_id LEFT JOIN users u ON u.username=e.operator_name;

CREATE TABLE IF NOT EXISTS dispatch_config (
 id boolean PRIMARY KEY DEFAULT true CHECK(id),
 parking_capacity integer NOT NULL DEFAULT 1000 CHECK(parking_capacity BETWEEN 1 AND 2500),
 inside_capacity integer NOT NULL DEFAULT 300 CHECK(inside_capacity BETWEEN 1 AND 2500),
 reset_hour integer NOT NULL DEFAULT 0 CHECK(reset_hour BETWEEN 0 AND 23)
);
INSERT INTO dispatch_config(id) VALUES(true) ON CONFLICT DO NOTHING;
ALTER TABLE dispatch_config ADD COLUMN IF NOT EXISTS no_show_minutes integer NOT NULL DEFAULT 5 CHECK(no_show_minutes BETWEEN 1 AND 120);
ALTER TABLE dispatch_config ADD COLUMN IF NOT EXISTS warning_percent integer NOT NULL DEFAULT 80 CHECK(warning_percent BETWEEN 1 AND 98);
ALTER TABLE dispatch_config ADD COLUMN IF NOT EXISTS critical_percent integer NOT NULL DEFAULT 90 CHECK(critical_percent BETWEEN 2 AND 99 AND critical_percent>warning_percent);
CREATE TABLE IF NOT EXISTS dispatch_spots (
 area text NOT NULL CHECK(area IN ('parking','inside')), slot integer NOT NULL CHECK(slot BETWEEN 1 AND 2500),
 state text NOT NULL CHECK(state IN ('reserved','disabled')), reason text NOT NULL,
 actor text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(area,slot)
);
CREATE TABLE IF NOT EXISTS dispatch_gates (
 id integer PRIMARY KEY CHECK(id IN (1,2)),
 state text NOT NULL DEFAULT 'closed' CHECK(state IN ('open','closed','locked','fault','maintenance')),
 updated_at timestamptz NOT NULL DEFAULT now(), actor text NOT NULL DEFAULT 'system', reason text NOT NULL DEFAULT ''
);
INSERT INTO dispatch_gates(id) VALUES(1),(2) ON CONFLICT DO NOTHING;
ALTER TABLE dispatch_config DROP CONSTRAINT IF EXISTS dispatch_config_parking_capacity_check;
ALTER TABLE dispatch_config DROP CONSTRAINT IF EXISTS dispatch_config_inside_capacity_check;
ALTER TABLE dispatch_config ADD CONSTRAINT dispatch_config_parking_capacity_check CHECK(parking_capacity BETWEEN 1 AND 2500);
ALTER TABLE dispatch_config ADD CONSTRAINT dispatch_config_inside_capacity_check CHECK(inside_capacity BETWEEN 1 AND 2500);
ALTER TABLE dispatch_config ALTER COLUMN parking_capacity SET DEFAULT 1000;
ALTER TABLE dispatch_config ALTER COLUMN inside_capacity SET DEFAULT 300;
UPDATE dispatch_config SET parking_capacity=1000 WHERE parking_capacity=120;
UPDATE dispatch_config SET inside_capacity=300 WHERE inside_capacity=60;
ALTER TABLE dispatch_spots DROP CONSTRAINT IF EXISTS dispatch_spots_slot_check;
ALTER TABLE dispatch_spots ADD CONSTRAINT dispatch_spots_slot_check CHECK(slot BETWEEN 1 AND 2500);

CREATE TABLE IF NOT EXISTS dispatch_numbers (
 day date NOT NULL, flow text NOT NULL, number integer NOT NULL, PRIMARY KEY(day,flow)
);
CREATE TABLE IF NOT EXISTS dispatch_tickets (
 visit_id bigint PRIMARY KEY REFERENCES zone_visits(id) ON DELETE CASCADE,
 day date NOT NULL, flow text NOT NULL, number integer NOT NULL, area text NOT NULL,
 slot integer, called_at timestamptz, called_by bigint REFERENCES users(id),
 UNIQUE(day,flow,number), UNIQUE(area,slot)
);
CREATE TABLE IF NOT EXISTS dispatch_log (
 id bigserial PRIMARY KEY, visit_id bigint, at timestamptz NOT NULL DEFAULT now(),
 action text NOT NULL, actor text NOT NULL
);
ALTER TABLE dispatch_tickets ADD COLUMN IF NOT EXISTS queue_state text NOT NULL DEFAULT 'waiting'
 CHECK(queue_state IN ('waiting','called','hold','no_show','cancelled','inside','completed'));
ALTER TABLE dispatch_tickets ADD COLUMN IF NOT EXISTS priority integer NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 2);
ALTER TABLE dispatch_tickets ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
UPDATE dispatch_tickets SET queue_state=CASE WHEN area='closed' THEN 'completed' ELSE 'inside' END
 WHERE area IN ('inside','closed') AND queue_state IN ('waiting','called');
UPDATE dispatch_tickets SET queue_state='called' WHERE area='parking' AND called_at IS NOT NULL AND queue_state='waiting';
CREATE INDEX IF NOT EXISTS dispatch_waiting ON dispatch_tickets(flow,priority DESC,day,number) WHERE area='parking' AND queue_state='waiting';
ALTER TABLE dispatch_log ADD COLUMN IF NOT EXISTS detail jsonb NOT NULL DEFAULT '{}';
ALTER TABLE dispatch_log ADD COLUMN IF NOT EXISTS command_id uuid UNIQUE;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS backlog jsonb;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS backlog_at timestamptz;
CREATE OR REPLACE FUNCTION dispatch_place(vid bigint, purpose text, place text, started_at timestamptz)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE cfg dispatch_config%ROWTYPE; business_day date; prefix text; n integer; space integer; old_area text;
BEGIN
 IF current_setting('xsm.restoring',true)='on' THEN RETURN; END IF;
 SELECT * INTO cfg FROM dispatch_config WHERE id FOR UPDATE;
 SELECT area INTO old_area FROM dispatch_tickets WHERE visit_id=vid;
 IF old_area=place OR (old_area IS NULL AND place='closed') THEN RETURN; END IF;
 IF place IN ('parking','inside') THEN
   SELECT i INTO space FROM generate_series(1,CASE WHEN place='parking' THEN cfg.parking_capacity ELSE cfg.inside_capacity END) i
     WHERE NOT EXISTS(SELECT 1 FROM dispatch_tickets t WHERE t.area=place AND t.slot=i)
       AND NOT EXISTS(SELECT 1 FROM dispatch_spots s WHERE s.area=place AND s.slot=i) ORDER BY i LIMIT 1;
 END IF;
 IF old_area IS NULL THEN
   business_day:=((started_at AT TIME ZONE 'Asia/Tashkent') - make_interval(hours=>cfg.reset_hour))::date;
   prefix:=CASE WHEN purpose='border' THEN 'CH' ELSE 'FZ' END;
   INSERT INTO dispatch_numbers VALUES(business_day,prefix,1) ON CONFLICT(day,flow)
     DO UPDATE SET number=dispatch_numbers.number+1 RETURNING number INTO n;
   INSERT INTO dispatch_tickets(visit_id,day,flow,number,area,slot,queue_state)
     VALUES(vid,business_day,prefix,n,place,space,CASE WHEN place='inside' THEN 'inside' ELSE 'waiting' END);
 ELSE
   UPDATE dispatch_tickets SET area=place,slot=space,version=version+1,
     queue_state=CASE WHEN place='inside' THEN 'inside' WHEN place='closed' THEN 'completed' ELSE 'waiting' END WHERE visit_id=vid;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION dispatch_visit_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM dispatch_place(NEW.id,NEW.purpose,NEW.location,NEW.started); RETURN NEW; END $$;
CREATE OR REPLACE TRIGGER dispatch_on_visit AFTER INSERT OR UPDATE OF location ON zone_visits
 FOR EACH ROW EXECUTE FUNCTION dispatch_visit_trigger();
SELECT dispatch_place(v.id,v.purpose,v.location,v.started) FROM zone_visits v
 WHERE v.location<>'closed' AND NOT EXISTS(SELECT 1 FROM dispatch_tickets t WHERE t.visit_id=v.id)
 ORDER BY v.started,v.id;

CREATE TABLE IF NOT EXISTS export_jobs (
 id uuid PRIMARY KEY, user_id bigint NOT NULL REFERENCES users(id), filters jsonb NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','ready','failed')),
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz,
 attempts integer NOT NULL DEFAULT 0, error text, content bytea,
 CHECK(content IS NULL OR octet_length(content)<=12582912)
);
CREATE INDEX IF NOT EXISTS export_jobs_pending ON export_jobs(created_at) WHERE state IN ('queued','running');

CREATE TABLE IF NOT EXISTS corrections (
    id SERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    record_id BIGINT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    reason TEXT NOT NULL,
    corrected_by BIGINT REFERENCES users(id),
    corrected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corrections_record ON corrections(table_name, record_id);

CREATE TABLE IF NOT EXISTS shifts (
    id SERIAL PRIMARY KEY,
    point TEXT NOT NULL,
    opened_by BIGINT REFERENCES users(id),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_by BIGINT REFERENCES users(id),
    closed_at TIMESTAMPTZ,
    system_cash BIGINT,
    counted_cash BIGINT,
    difference BIGINT,
    note TEXT,
    open_note TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed'))
);
CREATE INDEX IF NOT EXISTS idx_shifts_point_status ON shifts(point, status);
CREATE INDEX IF NOT EXISTS idx_zone_events_point_occurred ON zone_events(point, occurred DESC);
