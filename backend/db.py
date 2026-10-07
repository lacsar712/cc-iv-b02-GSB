import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")

# 设置项缺省值，键名 -> (缺省值, 转换函数)
SETTING_DEFAULTS = {
    "shadow_gate_enabled": ("off", lambda v: v == "on"),
    "shadow_window_sec": ("90", lambda v: max(5, min(600, int(v)))),
    "shadow_jump": ("0.20", lambda v: max(0.01, min(1.0, float(v)))),
}


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
    block_reason text,
    blocked_at timestamptz,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS block_reason text;
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS blocked_at timestamptz;

CREATE TABLE IF NOT EXISTS app_settings (
    key text PRIMARY KEY,
    value text NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  -- 只有真正入队（pending）的扫描才叫醒工人；被阴影闸门拦住的不通知。
  IF NEW.status = 'pending' THEN
    PERFORM pg_notify('iv_scan_new', NEW.id::text);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
