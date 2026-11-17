"""补正向登记与冲正对消。

补正向 = 归并配平后，发现原正向装载少计而补记的正向；它必须显式引用一笔
**已归并**冲正，并把「补正向键 ↔ 冲正键 ↔ 对消分」物化为不可变 `offset_edges`
（不使用 `offset_target` 快照列）。登记成功后补正向立即以「已归并」参与净额，
并与被引用冲正形成唯一对消边；登记、对消边、余额重算在同一事务内，任一校验
失败整笔回滚。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect, rows_to_dicts
from app.services.settle import recompute_account_balance

COMPENSATORY = "补正向"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fail(status_code: int, msg: str) -> HTTPException:
    return HTTPException(status_code, msg)


def _next_entry_key(conn: sqlite3.Connection, now: str) -> str:
    day = now[:10].replace("-", "")
    prefix = f"CMP-{day}-"
    row = conn.execute(
        "SELECT entry_key FROM ledger_entries WHERE entry_key LIKE ? ORDER BY entry_key DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row["entry_key"].rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            seq = 1
    return f"{prefix}{seq:03d}"


def _remaining_quota(conn: sqlite3.Connection, reverse_entry_key: str, reverse_amount: int) -> int:
    used = conn.execute(
        "SELECT COALESCE(SUM(offset_cents), 0) AS s FROM offset_edges WHERE reverse_entry_key = ?",
        (reverse_entry_key,),
    ).fetchone()["s"]
    return reverse_amount - used


def register(
    *,
    reverse_entry_key: str,
    amount_cents: int,
    offset_cents: int | None = None,
    entry_key: str | None = None,
    biz_ref: str = "",
    booked_at: str | None = None,
    note: str = "",
    account_id: int | None = None,
) -> dict:
    """登记一笔补正向；失败闭（任何一条校验不过都整笔回滚）。"""
    reverse_entry_key = (reverse_entry_key or "").strip()
    if not reverse_entry_key:
        raise _fail(400, "补正向必须引用一笔已归并冲正")
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise _fail(400, "补正向金额必须大于 0 分")
    if offset_cents is None:
        offset_cents = amount_cents
    if not isinstance(offset_cents, int) or offset_cents <= 0:
        raise _fail(400, "对消分必须大于 0 分")
    if offset_cents > amount_cents:
        raise _fail(400, "对消分不得大于补正向金额")

    now = _now()
    booked = booked_at or now

    conn = connect()
    conn.isolation_level = None  # 显式事务：登记 + 对消边 + 余额重算同一失败闭
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")

            rev = conn.execute(
                "SELECT * FROM ledger_entries WHERE entry_key = ?", (reverse_entry_key,)
            ).fetchone()
            if not rev:
                raise _fail(400, "引用的冲正不存在")
            if rev["kind"] != "冲正":
                raise _fail(400, "只能引用冲正，不能用普通正向冒充补正向")
            if rev["status"] != "已归并":
                raise _fail(400, "只能引用已归并冲正（先跑净额归并配平）")
            if account_id is not None and account_id != rev["account_id"]:
                raise _fail(400, "补正向账户必须与被引用冲正一致")

            quota = _remaining_quota(conn, reverse_entry_key, rev["amount_cents"])
            if quota <= 0:
                raise _fail(400, "被引用冲正已无可对消额度")
            if amount_cents > quota:
                raise _fail(
                    400,
                    f"补额超出被引用冲正未对消额度（补额 {amount_cents} > 余额度 {quota} 分）",
                )
            if offset_cents > quota:
                raise _fail(
                    400,
                    f"对消分超出被引用冲正未对消额度（对消 {offset_cents} > 余额度 {quota} 分）",
                )

            key = (entry_key or "").strip() or _next_entry_key(conn, now)
            exists = conn.execute(
                "SELECT 1 FROM ledger_entries WHERE entry_key = ?", (key,)
            ).fetchone()
            if exists:
                raise _fail(400, f"流水键已存在：{key}")

            cur = conn.execute(
                """
                INSERT INTO ledger_entries(
                  entry_key, account_id, kind, amount_cents, biz_ref,
                  target_entry_key, booked_at, status, note, settle_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '已归并', ?, ?)
                """,
                (
                    key,
                    rev["account_id"],
                    COMPENSATORY,
                    amount_cents,
                    biz_ref or "",
                    reverse_entry_key,
                    booked,
                    note or "",
                    rev["settle_run_id"],
                ),
            )
            entry_id = cur.lastrowid

            balance = recompute_account_balance(conn, rev["account_id"])
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise _fail(409, f"补正向登记冲突：{exc}") from exc
        except Exception:
            conn.rollback()
            raise

        entry = conn.execute(
            """
            SELECT e.*, a.code AS account_code, a.name AS account_name
            FROM ledger_entries e JOIN accounts a ON a.id = e.account_id
            WHERE e.id = ?
            """,
            (entry_id,),
        ).fetchone()
        edge = conn.execute(
            "SELECT * FROM offset_edges WHERE forward_entry_key = ?", (key,)
        ).fetchone()
        return {
            "补正向": dict(entry),
            "对消边": dict(edge),
            "账户余额分": balance,
            "被引用冲正剩余额度分": _remaining_quota(conn, reverse_entry_key, rev["amount_cents"]),
        }
    finally:
        conn.close()


def list_compensatory_entries() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT e.*, a.code AS account_code, a.name AS account_name,
                   o.reverse_entry_key AS offset_reverse_key,
                   o.offset_cents AS offset_cents,
                   o.created_at AS offset_created_at
            FROM ledger_entries e
            JOIN accounts a ON a.id = e.account_id
            LEFT JOIN offset_edges o ON o.forward_entry_key = e.entry_key
            WHERE e.kind = ?
            ORDER BY e.id
            """,
            (COMPENSATORY,),
        ).fetchall()
        return rows_to_dicts(rows)


def list_offset_edges() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT o.*, a.code AS account_code,
                   f.amount_cents AS forward_amount_cents,
                   r.amount_cents AS reverse_amount_cents,
                   r.booked_at AS reverse_booked_at
            FROM offset_edges o
            JOIN accounts a ON a.id = o.account_id
            JOIN ledger_entries f ON f.entry_key = o.forward_entry_key
            JOIN ledger_entries r ON r.entry_key = o.reverse_entry_key
            ORDER BY o.id
            """
        ).fetchall()
        return rows_to_dicts(rows)


def report() -> dict:
    """补正向对消报告：与流水 / 对消边 / 敞口 / 余额同一快照读。

    显式开启只读事务，保证笔数、对消边、敞口、余额来自同一数据库快照。
    """
    conn = connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN")
        edges = rows_to_dicts(
            conn.execute(
                """
                SELECT o.*, a.code AS account_code,
                       f.amount_cents AS forward_amount_cents
                FROM offset_edges o
                JOIN accounts a ON a.id = o.account_id
                JOIN ledger_entries f ON f.entry_key = o.forward_entry_key
                ORDER BY o.id
                """
            ).fetchall()
        )
        entry_count = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_entries WHERE kind = ?", (COMPENSATORY,)
        ).fetchone()["c"]
        entry_sum = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries WHERE kind = ?",
            (COMPENSATORY,),
        ).fetchone()["s"]
        edge_sum = conn.execute(
            "SELECT COALESCE(SUM(offset_cents), 0) AS s FROM offset_edges"
        ).fetchone()["s"]
        edge_count = len(edges)

        fwd_sum = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries
            WHERE kind IN ('正向', '补正向') AND status = '已归并'
            """
        ).fetchone()["s"]
        rev_sum = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries WHERE kind = '冲正' AND status = '已归并'"
        ).fetchone()["s"]
        ledger_net = fwd_sum - rev_sum
        balance_sum = conn.execute(
            "SELECT COALESCE(SUM(balance_cents), 0) AS s FROM accounts"
        ).fetchone()["s"]

        # 余额影响：各账户被补正向抬升的数额（补额合计），与余额同源
        account_rows = conn.execute(
            """
            SELECT a.id, a.code, a.name, a.balance_cents,
                   COALESCE(c.cnt, 0) AS compensatory_count,
                   COALESCE(c.sum_cents, 0) AS compensatory_cents
            FROM accounts a
            LEFT JOIN (
              SELECT account_id, COUNT(*) AS cnt, SUM(amount_cents) AS sum_cents
              FROM ledger_entries WHERE kind = ? GROUP BY account_id
            ) c ON c.account_id = a.id
            ORDER BY a.id
            """,
            (COMPENSATORY,),
        ).fetchall()

        # 敞口：被引用冲正已对消 / 未对消额度
        exposure = conn.execute(
            """
            SELECT r.entry_key AS reverse_entry_key, r.account_id, r.amount_cents,
                   COALESCE(SUM(o.offset_cents), 0) AS offset_cents
            FROM ledger_entries r
            LEFT JOIN offset_edges o ON o.reverse_entry_key = r.entry_key
            WHERE o.id IS NOT NULL
            GROUP BY r.entry_key
            ORDER BY r.entry_key
            """
        ).fetchall()

        return {
            "补正向笔数": entry_count,
            "补正向金额分": entry_sum,
            "对消边数": edge_count,
            "对消分合计": edge_sum,
            "补正向未对消分": entry_sum - edge_sum,
            "对消边": edges,
            "账户影响": rows_to_dicts(account_rows),
            "引用冲正敞口": [
                {
                    **dict(r),
                    "未对消额度分": r["amount_cents"] - r["offset_cents"],
                }
                for r in exposure
            ],
            "已归并正向合计分": fwd_sum,
            "已归并冲正合计分": rev_sum,
            "已归并流水净额分": ledger_net,
            "账户余额合计分": balance_sum,
            "流水余额一致": ledger_net == balance_sum,
        }
    finally:
        conn.rollback()
        conn.close()
