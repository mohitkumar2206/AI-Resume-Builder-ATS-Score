import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_static_css_served(client):
    res = client.get("/static/css/style.css")
    assert res.status_code == 200
    assert "text/css" in res.content_type
    assert len(res.data) > 1000
    assert b"--navy" in res.data


def test_static_js_served(client):
    res = client.get("/static/js/main.js")
    assert res.status_code == 200
    assert "text/javascript" in res.content_type
    assert len(res.data) > 500
    assert b"DOMContentLoaded" in res.data


def test_index_page_includes_assets(client):
    res = client.get("/")
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert '<link rel="stylesheet" href="/static/css/style.css">' in html
    assert '<script defer src="/static/js/main.js"></script>' in html


def test_all_get_routes(client):
    routes = [
        "/",
        "/builder",
        "/ats",
        "/templates",
        "/contact",
        "/register",
        "/login",
    ]
    for route in routes:
        res = client.get(route)
        assert res.status_code == 200, f"Failed for route {route}"

