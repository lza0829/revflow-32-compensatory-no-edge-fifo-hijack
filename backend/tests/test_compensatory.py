import sqlite3

import pytest


@pytest.fixture(scope="module", autouse=True)
def settled(client):
    client.post("/api/settle-runs")


def _merged_reversals(client):
    return [r for r in client.get("/api/entries?kind=冲正").json() if r["status"] == "已归并"]


def test_requires_reference(client):
    r = client.post("/api/compensatory-forwards", json={"amount_cents": 100})
    assert r.status_code == 422 or r.status_code == 400

    r = client.post(
        "/api/compensatory-forwards",
        json={"reverse_entry_key": "FWD-20261116-001", "amount_cents": 100},
    )
    assert r.status_code == 400
    assert "冲正" in r.json()["detail"]


def test_rejects_missing_or_unmerged_reverse(client):
    r = client.post(
        "/api/compensatory-forwards",
        json={"reverse_entry_key": "REV-NOPE", "amount_cents": 100},
    )
    assert r.status_code == 400

    # 无原单冲正已进隔离，不是已归并，不得被引用
    r = client.post(
        "/api/compensatory-forwards",
        json={"reverse_entry_key": "REV-20261116-004", "amount_cents": 100},
    )
    assert r.status_code == 400
    assert "已归并" in r.json()["detail"]


def test_register_and_offset_edge(client):
    revs = _merged_reversals(client)
    rev = next(r for r in revs if r["target_entry_key"] == "FWD-20261116-002")  # 5000 部分冲正
    before_bal = client.get("/api/accounts").json()
    acc_before = next(a for a in before_bal if a["id"] == rev["account_id"])["balance_cents"]

    r = client.post(
        "/api/compensatory-forwards",
        json={
            "reverse_entry_key": rev["entry_key"],
            "amount_cents": 1500,
            "biz_ref": "ORD-9002",
            "note": "原正向少计补记",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    key = body["补正向"]["entry_key"]
    assert body["补正向"]["kind"] == "补正向"
    assert body["补正向"]["status"] == "已归并"
    assert body["对消边"]["forward_entry_key"] == key
    assert body["对消边"]["reverse_entry_key"] == rev["entry_key"]
    assert body["对消边"]["offset_cents"] == 1500
    assert body["被引用冲正剩余额度分"] == rev["amount_cents"] - 1500

    acc_after = next(a for a in client.get("/api/accounts").json() if a["id"] == rev["account_id"])
    assert acc_after["balance_cents"] == acc_before + 1500

    # 正向流水页能拿到补正向与指向的冲正键
    fwd = client.get("/api/entries?kind=正向").json()
    row = next(e for e in fwd if e["entry_key"] == key)
    assert row["offset_reverse_key"] == rev["entry_key"]
    assert row["offset_cents"] == 1500

    # 冲正流水页反向可见
    rv = client.get("/api/entries?kind=冲正").json()
    revrow = next(e for e in rv if e["entry_key"] == rev["entry_key"])
    assert key in revrow["offset_forward_keys"]
    assert revrow["offset_edge_count"] == 1


def test_amount_must_be_positive(client):
    rev = _merged_reversals(client)[0]
    for bad in (0, -5):
        r = client.post(
            "/api/compensatory-forwards",
            json={"reverse_entry_key": rev["entry_key"], "amount_cents": bad},
        )
        assert r.status_code in (400, 422)


def test_over_quota_fails_closed(client):
    # REV-…-002 余 3500；超额 4000 必须整笔拒绝，且不落任何流水/对消边
    revs = _merged_reversals(client)
    rev = next(r for r in revs if r["target_entry_key"] == "FWD-20261116-002")
    fwd_before = len(client.get("/api/entries?kind=正向").json())
    edges_before = len(client.get("/api/offset-edges").json())

    r = client.post(
        "/api/compensatory-forwards",
        json={"reverse_entry_key": rev["entry_key"], "amount_cents": 4000},
    )
    assert r.status_code == 400
    assert "额度" in r.json()["detail"]

    assert len(client.get("/api/entries?kind=正向").json()) == fwd_before
    assert len(client.get("/api/offset-edges").json()) == edges_before


def test_offset_cannot_exceed_amount(client):
    rev = next(r for r in _merged_reversals(client) if r["target_entry_key"] == "FWD-20261116-002")
    r = client.post(
        "/api/compensatory-forwards",
        json={
            "reverse_entry_key": rev["entry_key"],
            "amount_cents": 100,
            "offset_cents": 200,
        },
    )
    assert r.status_code == 400


def test_offset_edges_immutable(client, db_file):
    with sqlite3.connect(db_file) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE offset_edges SET offset_cents = offset_cents + 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM offset_edges")


def test_compensatory_not_hijacked_by_fifo(client, db_file):
    # 登记一笔带引用冲正 target 的新冲正：它不得抢占补正向
    rev = next(r for r in _merged_reversals(client) if r["target_entry_key"] == "FWD-20261116-002")
    comp = next(
        e
        for e in client.get("/api/entries?kind=正向").json()
        if e["kind"] == "补正向" and e["offset_reverse_key"] == rev["entry_key"]
    )
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """
            INSERT INTO ledger_entries(entry_key, account_id, kind, amount_cents, target_entry_key, booked_at)
            VALUES ('REV-HIJACK-1', ?, '冲正', 50, ?, '2026-11-16T18:00:00Z')
            """,
            (rev["account_id"], comp["entry_key"]),
        )
        conn.commit()
    run = client.post("/api/settle-runs").json()
    assert run["status"] == "完成"
    q = client.get("/api/quarantine").json()
    assert any(x["entry_key"] == "REV-HIJACK-1" and x["reason"] == "冲正不得抢占补正向" for x in q)


def test_report_same_source(client):
    rep = client.get("/api/compensatory-report").json()
    assert rep["补正向笔数"] == len([e for e in client.get("/api/entries?kind=正向").json() if e["kind"] == "补正向"])
    assert rep["对消边数"] == len(client.get("/api/offset-edges").json())
    assert rep["补正向金额分"] == sum(
        e["amount_cents"] for e in client.get("/api/entries?kind=正向").json() if e["kind"] == "补正向"
    )
    assert rep["对消分合计"] == sum(e["offset_cents"] for e in rep["对消边"])
    bal = sum(a["balance_cents"] for a in client.get("/api/accounts").json())
    assert rep["账户余额合计分"] == bal
    assert rep["流水余额一致"] is True

    report = client.post("/api/verify-reports").json()
    assert report["consistent"] == 1
    assert report["detail"]["补正向笔数"] == rep["补正向笔数"]
    assert report["detail"]["对消边数"] == rep["对消边数"]
