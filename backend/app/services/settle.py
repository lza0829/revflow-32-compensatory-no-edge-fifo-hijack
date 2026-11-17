from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect, rows_to_dicts

COMPENSATORY = "补正向"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def recompute_account_balance(conn, account_id: int) -> int:
    """按「全部已归并流水」重算账户余额：正向/补正向记正，冲正记负。

    归并、补正向登记共用此口径，保证余额与流水净额同源。
    """
    net = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN kind = '冲正' THEN -amount_cents ELSE amount_cents END), 0) AS s
        FROM ledger_entries
        WHERE account_id = ? AND status = '已归并'
        """,
        (account_id,),
    ).fetchone()["s"]
    conn.execute("UPDATE accounts SET balance_cents = ? WHERE id = ?", (net, account_id))
    return net


def dashboard() -> dict:
    with connect() as conn:
        accounts = rows_to_dicts(conn.execute("SELECT * FROM accounts ORDER BY id").fetchall())
        fwd = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_entries WHERE kind = '正向'"
        ).fetchone()["c"]
        rev = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_entries WHERE kind = '冲正'"
        ).fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_entries WHERE status = '待归并'"
        ).fetchone()["c"]
        q = conn.execute("SELECT COUNT(*) AS c FROM quarantine_items").fetchone()["c"]
        comp = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_entries WHERE kind = '补正向'"
        ).fetchone()["c"]
        edges = conn.execute("SELECT COUNT(*) AS c FROM offset_edges").fetchone()["c"]
        proofs = conn.execute("SELECT COUNT(*) AS c FROM offset_proofs").fetchone()["c"]
        bal = conn.execute("SELECT COALESCE(SUM(balance_cents), 0) AS s FROM accounts").fetchone()["s"]
        last = conn.execute("SELECT * FROM settle_runs ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "账户数": len(accounts),
            "正向笔数": fwd,
            "冲正笔数": rev,
            "补正向笔数": comp,
            "对消边数": edges,
            "对消证明数": proofs,
            "待归并": pending,
            "未平隔离": q,
            "余额合计分": bal,
            "最近归并": dict(last) if last else None,
            "账户": accounts,
        }


def list_accounts() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM accounts ORDER BY id").fetchall())


def list_entries(kind: str | None = None) -> list[dict]:
    """正向口径含「补正向」；每行附对消边信息（补正向指向冲正，冲正汇总指向它的补正向）。"""
    with connect() as conn:
        # 补正向 → 冲正 是对消边唯一键（1:1）；一笔冲正可被多笔补正向引用（1:N），故冲正侧聚合。
        base = """
            SELECT e.*, a.code AS account_code, a.name AS account_name,
                   (SELECT o.reverse_entry_key FROM offset_edges o
                     WHERE o.forward_entry_key = e.entry_key) AS offset_reverse_key,
                   (SELECT GROUP_CONCAT(o.forward_entry_key, ', ') FROM offset_edges o
                     WHERE o.reverse_entry_key = e.entry_key) AS offset_forward_keys,
                   (SELECT COUNT(*) FROM offset_edges o
                     WHERE o.reverse_entry_key = e.entry_key) AS offset_edge_count,
                   COALESCE(
                     (SELECT o.offset_cents FROM offset_edges o
                       WHERE o.forward_entry_key = e.entry_key),
                     (SELECT SUM(o.offset_cents) FROM offset_edges o
                       WHERE o.reverse_entry_key = e.entry_key)
                   ) AS offset_cents,
                   (SELECT o.created_at FROM offset_edges o
                     WHERE o.forward_entry_key = e.entry_key) AS offset_created_at
            FROM ledger_entries e
            JOIN accounts a ON a.id = e.account_id
        """
        if kind == "正向":
            rows = conn.execute(
                base + " WHERE e.kind IN ('正向', '补正向') ORDER BY e.id"
            ).fetchall()
        elif kind:
            rows = conn.execute(base + " WHERE e.kind = ? ORDER BY e.id", (kind,)).fetchall()
        else:
            rows = conn.execute(base + " ORDER BY e.id").fetchall()
        return rows_to_dicts(rows)


def list_quarantine() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT q.*, a.code AS account_code
            FROM quarantine_items q
            JOIN accounts a ON a.id = q.account_id
            ORDER BY q.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def list_settle_runs() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(
            conn.execute("SELECT * FROM settle_runs ORDER BY id DESC").fetchall()
        )


def list_verify_reports() -> list[dict]:
    with connect() as conn:
        rows = rows_to_dicts(
            conn.execute("SELECT * FROM verify_reports ORDER BY id DESC").fetchall()
        )
        for r in rows:
            r["detail"] = json.loads(r["detail_json"])
        return rows


def run_settle() -> dict:
    """底座冒烟：按账户汇总正向−已匹配冲正；无原单/超额冲正进隔离；余额写账户。

    「补正向」登记时已直接进入已归并，不参与本轮 FIFO；本轮的余额写与净额统计
    仍按账户全量已归并流水重算，保证与流水/对消/余额同源。
    """
    with connect() as conn:
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO settle_runs(status, started_at)
            VALUES ('进行中', ?)
            """,
            (now,),
        )
        run_id = cur.lastrowid

        forwards = rows_to_dicts(
            conn.execute(
                "SELECT * FROM ledger_entries WHERE kind = '正向' AND status = '待归并' ORDER BY id"
            ).fetchall()
        )
        reversals = rows_to_dicts(
            conn.execute(
                "SELECT * FROM ledger_entries WHERE kind = '冲正' AND status = '待归并' ORDER BY id"
            ).fetchall()
        )
        fwd_by_key = {f["entry_key"]: f for f in forwards}

        # 每个正向已占用冲正额度
        used: dict[str, int] = {f["entry_key"]: 0 for f in forwards}
        settled = 0
        quarantined = 0
        account_net: dict[int, int] = {}

        for f in forwards:
            account_net[f["account_id"]] = account_net.get(f["account_id"], 0) + f["amount_cents"]

        for r in reversals:
            target = r.get("target_entry_key")
            if not target or target not in fwd_by_key:
                # 硬约束：补正向已绑定对消边，不得被无关冲正按 FIFO 抢占。
                # 匹配层直接排除已绑定补正向，此类冲正一律隔离。
                comp = conn.execute(
                    "SELECT * FROM ledger_entries WHERE entry_key = ? AND kind = '补正向'",
                    (target,),
                ).fetchone() if target else None
                if comp:
                    reason = "冲正不得抢占补正向"
                    conn.execute(
                        """
                        INSERT INTO quarantine_items(
                          entry_key, account_id, reason, amount_cents, settle_run_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (r["entry_key"], r["account_id"], reason, r["amount_cents"], run_id, now),
                    )
                    conn.execute(
                        "UPDATE ledger_entries SET status = '已隔离', settle_run_id = ? WHERE id = ?",
                        (run_id, r["id"]),
                    )
                    quarantined += 1
                    continue
                reason = "冲正无原单"
                conn.execute(
                    """
                    INSERT INTO quarantine_items(
                      entry_key, account_id, reason, amount_cents, settle_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (r["entry_key"], r["account_id"], reason, r["amount_cents"], run_id, now),
                )
                conn.execute(
                    "UPDATE ledger_entries SET status = '已隔离', settle_run_id = ? WHERE id = ?",
                    (run_id, r["id"]),
                )
                quarantined += 1
                continue

            # 防御性兜底：即便补正向键混入匹配字典，也绝不参与 FIFO 占用。
            if fwd_by_key[target].get("kind") == COMPENSATORY:
                conn.execute(
                    """
                    INSERT INTO quarantine_items(
                      entry_key, account_id, reason, amount_cents, settle_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r["entry_key"],
                        r["account_id"],
                        "冲正不得抢占补正向",
                        r["amount_cents"],
                        run_id,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE ledger_entries SET status = '已隔离', settle_run_id = ? WHERE id = ?",
                    (run_id, r["id"]),
                )
                quarantined += 1
                continue

            fwd = fwd_by_key[target]
            if fwd["account_id"] != r["account_id"]:
                conn.execute(
                    """
                    INSERT INTO quarantine_items(
                      entry_key, account_id, reason, amount_cents, settle_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (r["entry_key"], r["account_id"], "冲正账户与原单不一致", r["amount_cents"], run_id, now),
                )
                conn.execute(
                    "UPDATE ledger_entries SET status = '已隔离', settle_run_id = ? WHERE id = ?",
                    (run_id, r["id"]),
                )
                quarantined += 1
                continue

            remain = fwd["amount_cents"] - used[target]
            if r["amount_cents"] > remain:
                reason = "冲正超额"
                if conn.execute(
                    "SELECT 1 FROM offset_edges WHERE reverse_entry_key = ?", (r["entry_key"],)
                ).fetchone():
                    reason = "冲正已被补正向对消绑定"
                conn.execute(
                    """
                    INSERT INTO quarantine_items(
                      entry_key, account_id, reason, amount_cents, settle_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (r["entry_key"], r["account_id"], reason, r["amount_cents"], run_id, now),
                )
                conn.execute(
                    "UPDATE ledger_entries SET status = '已隔离', settle_run_id = ? WHERE id = ?",
                    (run_id, r["id"]),
                )
                quarantined += 1
                continue

            used[target] += r["amount_cents"]
            account_net[r["account_id"]] = account_net.get(r["account_id"], 0) - r["amount_cents"]
            conn.execute(
                "UPDATE ledger_entries SET status = '已归并', settle_run_id = ? WHERE id = ?",
                (run_id, r["id"]),
            )
            settled += 1

        for f in forwards:
            conn.execute(
                "UPDATE ledger_entries SET status = '已归并', settle_run_id = ? WHERE id = ?",
                (run_id, f["id"]),
            )
            settled += 1

        net_total = 0
        touched = set(account_net)
        touched |= {
            r["account_id"]
            for r in conn.execute(
                "SELECT DISTINCT account_id FROM ledger_entries WHERE kind = '补正向'"
            ).fetchall()
        }
        for acc_id in sorted(touched):
            net_total += recompute_account_balance(conn, acc_id)
            conn.execute(
                "UPDATE accounts SET last_settle_run_id = ? WHERE id = ?", (run_id, acc_id)
            )

        # 无流水账户余额清零（本轮未触及则保持；底座简化：仅写触及账户）
        finished = _now()
        conn.execute(
            """
            UPDATE settle_runs
            SET status = '完成',
                forward_count = ?,
                reversal_count = ?,
                settled_count = ?,
                quarantine_count = ?,
                net_cents = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (
                len(forwards),
                len(reversals),
                settled,
                quarantined,
                net_total,
                finished,
                run_id,
            ),
        )
        conn.commit()
        return get_settle_run(run_id)


def get_settle_run(run_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM settle_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "归并运行不存在")
        return dict(row)


def run_verify(settle_run_id: int | None = None) -> dict:
    with connect() as conn:
        if settle_run_id is None:
            last = conn.execute("SELECT id FROM settle_runs ORDER BY id DESC LIMIT 1").fetchone()
            if not last:
                raise HTTPException(400, "尚无归并运行，无法互证")
            settle_run_id = last["id"]
        run = conn.execute("SELECT * FROM settle_runs WHERE id = ?", (settle_run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "归并运行不存在")

        # 已归并正向（含补正向）− 已归并冲正
        fwd_sum = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries
            WHERE kind IN ('正向', '补正向') AND status = '已归并' AND settle_run_id = ?
            """,
            (settle_run_id,),
        ).fetchone()["s"]
        rev_sum = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries
            WHERE kind = '冲正' AND status = '已归并' AND settle_run_id = ?
            """,
            (settle_run_id,),
        ).fetchone()["s"]
        ledger_net = fwd_sum - rev_sum
        balance_sum = conn.execute(
            "SELECT COALESCE(SUM(balance_cents), 0) AS s FROM accounts WHERE last_settle_run_id = ?",
            (settle_run_id,),
        ).fetchone()["s"]
        # 账户余额合计可能含历史；底座约定：互证看「本轮触及账户余额合计」vs 本轮流水净额
        # 简化：用全部账户余额 vs 全部已归并净额（多轮后仍应一致若每轮覆盖写）
        all_bal = conn.execute("SELECT COALESCE(SUM(balance_cents), 0) AS s FROM accounts").fetchone()["s"]
        q_count = conn.execute(
            "SELECT COUNT(*) AS c FROM quarantine_items WHERE settle_run_id = ?",
            (settle_run_id,),
        ).fetchone()["c"]

        # 全量已归并净额（正向 + 补正向 − 冲正）
        all_fwd = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries WHERE kind IN ('正向', '补正向') AND status = '已归并'"
        ).fetchone()["s"]
        all_rev = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries WHERE kind = '冲正' AND status = '已归并'"
        ).fetchone()["s"]
        all_ledger_net = all_fwd - all_rev

        comp_count = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_entries WHERE kind = '补正向'"
        ).fetchone()["c"]
        comp_sum = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries WHERE kind = '补正向'"
        ).fetchone()["s"]
        edge_count = conn.execute("SELECT COUNT(*) AS c FROM offset_edges").fetchone()["c"]
        edge_sum = conn.execute(
            "SELECT COALESCE(SUM(offset_cents), 0) AS s FROM offset_edges"
        ).fetchone()["s"]
        proof_count = conn.execute("SELECT COUNT(*) AS c FROM offset_proofs").fetchone()["c"]

        consistent = 1 if all_ledger_net == all_bal else 0
        detail = {
            "本轮正向分": fwd_sum,
            "本轮冲正分": rev_sum,
            "本轮流水净额分": ledger_net,
            "本轮触及余额合计分": balance_sum,
            "全量流水净额分": all_ledger_net,
            "全量账户余额分": all_bal,
            "本轮隔离数": q_count,
            "补正向笔数": comp_count,
            "补正向金额分": comp_sum,
            "对消边数": edge_count,
            "对消分合计": edge_sum,
            "对消证明数": proof_count,
            "边证明一致": proof_count == edge_count,
        }
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO verify_reports(
              settle_run_id, ledger_net_cents, balance_sum_cents,
              quarantine_count, consistent, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                settle_run_id,
                all_ledger_net,
                all_bal,
                q_count,
                consistent,
                json.dumps(detail, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
        rid = cur.lastrowid
        row = conn.execute("SELECT * FROM verify_reports WHERE id = ?", (rid,)).fetchone()
        out = dict(row)
        out["detail"] = detail
        return out
