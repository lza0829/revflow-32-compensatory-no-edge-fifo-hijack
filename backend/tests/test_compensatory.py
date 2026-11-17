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
    assert report["detail"]["对消证明数"] == rep["对消证明数"]
    assert report["detail"]["边证明一致"] is True

    # 总览同源暴露对消证明数
    dash = client.get("/api/dashboard").json()
    assert dash["对消证明数"] == rep["对消证明数"]
    assert dash["对消边数"] == rep["对消边数"]


def test_register_returns_offset_proof_with_remaining_quota(client):
    revs = _merged_reversals(client)
    rev = next(r for r in revs if r["target_entry_key"] == "FWD-20261116-002")  # 5000 冲正
    r = client.post(
        "/api/compensatory-forwards",
        json={"reverse_entry_key": rev["entry_key"], "amount_cents": 500, "offset_cents": 400},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    fwd_key = body["补正向"]["entry_key"]
    proof = body["对消证明"]

    # 证明实体四要素：补正向键、冲正键、对消分、冲正剩余额度
    assert proof["forward_entry_key"] == fwd_key
    assert proof["reverse_entry_key"] == rev["entry_key"]
    assert proof["offset_cents"] == 400
    assert proof["reverse_amount_cents"] == rev["amount_cents"]
    # 此前 REV-…-002 已对消 1500（test_register_and_offset_edge），本轮再对消 400
    assert proof["reverse_remaining_cents"] == rev["amount_cents"] - 1500 - 400
    assert proof["proof_key"].startswith("PRF-")


def test_offset_proofs_readonly_lookup_by_either_key(client):
    # 先登记一笔，拿到补正向键 / 冲正键
    rev = next(r for r in _merged_reversals(client) if r["target_entry_key"] == "FWD-20261116-001")
    reg = client.post(
        "/api/compensatory-forwards",
        json={"reverse_entry_key": rev["entry_key"], "amount_cents": 300},
    ).json()
    fwd_key = reg["补正向"]["entry_key"]

    # 按补正向键查
    by_forward = client.get(f"/api/offset-proofs?forward_entry_key={fwd_key}").json()
    assert len(by_forward) == 1
    assert by_forward[0]["forward_entry_key"] == fwd_key
    assert by_forward[0]["reverse_entry_key"] == rev["entry_key"]
    assert by_forward[0]["reverse_remaining_cents"] == rev["amount_cents"] - 300

    # 按冲正键查
    by_reverse = client.get(
        f"/api/offset-proofs?reverse_entry_key={rev['entry_key']}"
    ).json()
    assert all(p["reverse_entry_key"] == rev["entry_key"] for p in by_reverse)
    assert any(p["forward_entry_key"] == fwd_key for p in by_reverse)

    # 无条件返回全部
    all_proofs = client.get("/api/offset-proofs").json()
    assert len(all_proofs) >= len(by_reverse)
    assert len(all_proofs) == len(client.get("/api/offset-edges").json())


def test_offset_proofs_immutable(client, db_file):
    with sqlite3.connect(db_file) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE offset_proofs SET offset_cents = offset_cents + 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM offset_proofs")


def test_report_proofs_match_edges_same_snapshot(client):
    rep = client.get("/api/compensatory-report").json()
    assert rep["对消证明数"] == rep["对消边数"]
    assert rep["边证明一致"] is True
    # 证明与边、流水同源：对消分逐边可对上
    edge_sums = sorted((e["forward_entry_key"], e["offset_cents"]) for e in rep["对消边"])
    proof_sums = sorted((p["forward_entry_key"], p["offset_cents"]) for p in rep["对消证明"])
    assert edge_sums == proof_sums


def test_failed_registration_leaves_no_half_state(client):
    # 超额登记必须失败闭：补正向、对消边、证明、余额都不得留下半截
    rev = next(r for r in _merged_reversals(client) if r["target_entry_key"] == "FWD-20261116-002")
    accounts_before = {a["id"]: a["balance_cents"] for a in client.get("/api/accounts").json()}
    entries_before = len(client.get("/api/entries?kind=正向").json())
    edges_before = len(client.get("/api/offset-edges").json())
    proofs_before = len(client.get("/api/offset-proofs").json())

    r = client.post(
        "/api/compensatory-forwards",
        json={"reverse_entry_key": rev["entry_key"], "amount_cents": 999999},
    )
    assert r.status_code == 400

    assert len(client.get("/api/entries?kind=正向").json()) == entries_before
    assert len(client.get("/api/offset-edges").json()) == edges_before
    assert len(client.get("/api/offset-proofs").json()) == proofs_before
    accounts_after = {a["id"]: a["balance_cents"] for a in client.get("/api/accounts").json()}
    assert accounts_after == accounts_before  # 失败闭不得抬任何余额


def test_duplicate_edge_key_conflict_fails_closed(client, db_file):
    # 同键重放：唯一约束冲突时补正向/边/证明/余额不得出现半截
    rev = next(r for r in _merged_reversals(client) if r["target_entry_key"] == "FWD-20261116-003")
    r1 = client.post(
        "/api/compensatory-forwards",
        json={"reverse_entry_key": rev["entry_key"], "amount_cents": 200, "entry_key": "CMP-DUP-001"},
    )
    assert r1.status_code == 200, r1.text
    edges_before = len(client.get("/api/offset-edges").json())
    proofs_before = len(client.get("/api/offset-proofs").json())

    r2 = client.post(
        "/api/compensatory-forwards",
        json={"reverse_entry_key": rev["entry_key"], "amount_cents": 200, "entry_key": "CMP-DUP-001"},
    )
    assert r2.status_code == 400
    assert len(client.get("/api/offset-edges").json()) == edges_before
    assert len(client.get("/api/offset-proofs").json()) == proofs_before


def test_bound_compensatory_excluded_from_fifo_balance_intact(client, db_file):
    # 抢占冲正被隔离后：补正向对消边/证明仍在，余额不被抢占冲正改写
    comp = next(
        e
        for e in client.get("/api/entries?kind=正向").json()
        if e["kind"] == "补正向"
    )
    bal_before = next(
        a for a in client.get("/api/accounts").json() if a["id"] == comp["account_id"]
    )["balance_cents"]
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """
            INSERT INTO ledger_entries(entry_key, account_id, kind, amount_cents, target_entry_key, booked_at)
            VALUES ('REV-HIJACK-2', ?, '冲正', 77, ?, '2026-11-16T19:00:00Z')
            """,
            (comp["account_id"], comp["entry_key"]),
        )
        conn.commit()
    client.post("/api/settle-runs")
    q = client.get("/api/quarantine").json()
    assert any(
        x["entry_key"] == "REV-HIJACK-2" and x["reason"] == "冲正不得抢占补正向" for x in q
    )
    # 补正向仍是已归并，对消边 / 证明未被动过
    still = next(
        e for e in client.get("/api/entries?kind=正向").json() if e["entry_key"] == comp["entry_key"]
    )
    assert still["status"] == "已归并"
    proofs = client.get(
        f"/api/offset-proofs?forward_entry_key={comp['entry_key']}"
    ).json()
    assert len(proofs) == 1
    # 被隔离的抢占冲正不参与净额，余额不变
    bal_after = next(
        a for a in client.get("/api/accounts").json() if a["id"] == comp["account_id"]
    )["balance_cents"]
    assert bal_after == bal_before
