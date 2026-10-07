import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);

-- 邻串阴影剔除开关与对照参数（单行，id 恒为 1）
CREATE TABLE IF NOT EXISTS shadow_settings (
    id smallint PRIMARY KEY DEFAULT 1,
    enabled boolean NOT NULL DEFAULT true,
    threshold double precision NOT NULL DEFAULT 0.15,
    window_minutes integer NOT NULL DEFAULT 30,
    updated_by text,
    updated_at timestamptz NOT NULL,
    CONSTRAINT shadow_settings_singleton CHECK (id = 1)
);
INSERT INTO shadow_settings (id, enabled, threshold, window_minutes, updated_at)
VALUES (1, true, 0.15, 30, now())
ON CONFLICT (id) DO NOTHING;

-- 交单口被拦住的单子留在这张履历里：剔除关掉后旧履历仍可翻
CREATE TABLE IF NOT EXISTS scan_rejections (
    id serial PRIMARY KEY,
    kind text NOT NULL,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    reason text NOT NULL,
    ref_scan_id bigint,
    neighbor_count integer NOT NULL DEFAULT 0,
    neighbor_median double precision,
    settings_enabled boolean NOT NULL,
    threshold double precision NOT NULL,
    window_minutes integer NOT NULL,
    rejected_by text NOT NULL,
    rejected_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rejections_rejected_at ON scan_rejections (rejected_at DESC);

-- 旧卷幂等升级：种子行没有这两列也不影响
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'iv_scans' AND column_name = 'array_code') THEN
        ALTER TABLE iv_scans ADD COLUMN array_code text;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'iv_scans' AND column_name = 'seq_no') THEN
        ALTER TABLE iv_scans ADD COLUMN seq_no integer;
    END IF;
END $$;
UPDATE iv_scans
   SET array_code = substring(string_code from '^(.*)-串[0-9]+$'),
       seq_no = substring(string_code from '^.*-串([0-9]+)$')::integer
 WHERE array_code IS NULL
   AND substring(string_code from '^.*-串[0-9]+$') IS NOT NULL;

CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
