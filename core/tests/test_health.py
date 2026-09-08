from fastapi.testclient import TestClient


def test_health_antwortet_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["X-Frame-Options"] == "DENY"
