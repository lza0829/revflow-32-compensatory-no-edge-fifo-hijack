def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["项目"] == "冲流台"


def test_settle_and_verify(client):
    d = client.get("/api/dashboard").json()
    assert d["账户数"] >= 3
    assert d["正向笔数"] >= 5
    assert d["冲正笔数"] >= 4

    run = client.post("/api/settle-runs")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "完成"
    assert body["quarantine_count"] >= 1

    q = client.get("/api/quarantine").json()
    assert any(x["reason"] == "冲正无原单" for x in q)

    report = client.post("/api/verify-reports")
    assert report.status_code == 200, report.text
    rj = report.json()
    assert rj["consistent"] == 1
    assert rj["ledger_net_cents"] == rj["balance_sum_cents"]
