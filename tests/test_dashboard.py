from fastapi.testclient import TestClient
from src.app import app
from src.database import init_database, close_database

def test_dashboard_routes():
    init_database("./data/test_messages.db")
    client = TestClient(app)

    # 1. Test Dashboard HTML page
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "JiraAutomation" in res_root.text

    # 2. Test Dashboard Status API
    res_status = client.get("/api/status")
    assert res_status.status_code == 200
    data = res_status.json()
    assert data["status"] == "online"
    assert "roles" in data
    assert "metrics" in data
    assert "subscription" in data

    # 3. Test Message Simulation API
    sim_res = client.post("/api/test/simulate", json={
        "role": "CLIENT",
        "text": "Automated test message for dashboard"
    })
    assert sim_res.status_code == 200
    assert sim_res.json()["success"] is True
    assert sim_res.json()["role"] == "CLIENT"

    close_database()
    from pathlib import Path
    t_db = Path("./data/test_messages.db")
    if t_db.exists():
        try:
            t_db.unlink()
        except Exception:
            pass
