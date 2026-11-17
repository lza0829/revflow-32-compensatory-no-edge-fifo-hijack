from __future__ import annotations

import json
from pathlib import Path

from app.db import FIXTURES_DIR, connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'CNY',
  balance_cents INTEGER NOT NULL DEFAULT 0,
  last_settle_run_id INTEGER
);
CREATE TABLE IF NOT EXISTS ledger_entries (
  id INTEGER PRIMARY KEY,
  entry_key TEXT NOT NULL UNIQUE,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  kind TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  biz_ref TEXT NOT NULL DEFAULT '',
  target_entry_key TEXT,
  booked_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '待归并',
  note TEXT NOT NULL DEFAULT '',
  settle_run_id INTEGER
);
CREATE TABLE IF NOT EXISTS offset_edges (
  id INTEGER PRIMARY KEY,
  forward_entry_key TEXT NOT NULL UNIQUE REFERENCES ledger_entries(entry_key),
  reverse_entry_key TEXT NOT NULL REFERENCES ledger_entries(entry_key),
  offset_cents INTEGER NOT NULL CHECK (offset_cents > 0),
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  reverse_settle_run_id INTEGER REFERENCES settle_runs(id),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offset_edges_reverse ON offset_edges(reverse_entry_key);

CREATE TRIGGER IF NOT EXISTS trg_compensatory_immutable
BEFORE UPDATE OF entry_key, account_id, kind, amount_cents, target_entry_key ON ledger_entries
WHEN EXISTS (SELECT 1 FROM offset_edges o WHERE o.forward_entry_key = OLD.entry_key)
 AND (
   NEW.entry_key IS NOT OLD.entry_key
   OR NEW.account_id IS NOT OLD.account_id
   OR NEW.kind IS NOT OLD.kind
   OR NEW.amount_cents IS NOT OLD.amount_cents
   OR NEW.target_entry_key IS NOT OLD.target_entry_key
 )
BEGIN
  SELECT RAISE(ABORT, '补正向对消绑定后不可改键/账户/金额/类型/引用冲正');
END;
CREATE TRIGGER IF NOT EXISTS trg_forward_kind_not_promoted
BEFORE UPDATE OF kind ON ledger_entries
WHEN NEW.kind = '补正向' AND OLD.kind <> '补正向'
BEGIN
  SELECT RAISE(ABORT, '补正向只能通过补正向登记创建');
END;
CREATE TRIGGER IF NOT EXISTS trg_compensatory_required_offset
BEFORE INSERT ON ledger_entries
WHEN NEW.kind = '补正向'
 AND NOT EXISTS (
   SELECT 1 FROM ledger_entries r
   WHERE r.entry_key = NEW.target_entry_key AND r.kind = '冲正'
 )
BEGIN
  SELECT RAISE(ABORT, '补正向必须引用一笔真实冲正，不得由普通正向冒充');
END;
CREATE TABLE IF NOT EXISTS quarantine_items (
  id INTEGER PRIMARY KEY,
  entry_key TEXT NOT NULL,
  account_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  settle_run_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settle_runs (
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL,
  forward_count INTEGER NOT NULL DEFAULT 0,
  reversal_count INTEGER NOT NULL DEFAULT 0,
  settled_count INTEGER NOT NULL DEFAULT 0,
  quarantine_count INTEGER NOT NULL DEFAULT 0,
  net_cents INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS verify_reports (
  id INTEGER PRIMARY KEY,
  settle_run_id INTEGER REFERENCES settle_runs(id),
  ledger_net_cents INTEGER NOT NULL,
  balance_sum_cents INTEGER NOT NULL,
  quarantine_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
        if n:
            return
        seed_path = FIXTURES_DIR / "seed.json"
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        for a in data["accounts"]:
            conn.execute(
                "INSERT INTO accounts(id, code, name, currency, balance_cents) VALUES (?, ?, ?, ?, 0)",
                (a["id"], a["code"], a["name"], a["currency"]),
            )
        for e in data["forward_entries"]:
            conn.execute(
                """
                INSERT INTO ledger_entries(
                  id, entry_key, account_id, kind, amount_cents, biz_ref,
                  target_entry_key, booked_at, status, note
                ) VALUES (?, ?, ?, '正向', ?, ?, NULL, ?, '待归并', ?)
                """,
                (
                    e["id"],
                    e["entry_key"],
                    e["account_id"],
                    e["amount_cents"],
                    e.get("biz_ref", ""),
                    e["booked_at"],
                    e.get("note", ""),
                ),
            )
        # 冲正 id 与正向可能重叠，用独立自增：先取 max
        max_id = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM ledger_entries").fetchone()["m"]
        for i, e in enumerate(data["reversal_entries"], start=1):
            conn.execute(
                """
                INSERT INTO ledger_entries(
                  id, entry_key, account_id, kind, amount_cents, biz_ref,
                  target_entry_key, booked_at, status, note
                ) VALUES (?, ?, ?, '冲正', ?, ?, ?, ?, '待归并', ?)
                """,
                (
                    max_id + i,
                    e["entry_key"],
                    e["account_id"],
                    e["amount_cents"],
                    e.get("biz_ref", ""),
                    e.get("target_entry_key"),
                    e["booked_at"],
                    e.get("note", ""),
                ),
            )
        conn.commit()
