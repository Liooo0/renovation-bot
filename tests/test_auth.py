#!/usr/bin/env python3
"""老板面板鉴权测试:验证 URL 密码、空密码放行、明文 cookie 三个漏洞已修复。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import renov_bot_web


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(renov_bot_web, "OWNER_PASS", "secret123")
    renov_bot_web.app.config["TESTING"] = True
    renov_bot_web._owner_sessions.clear()
    return renov_bot_web.app.test_client()


def test_owner_requires_login(client):
    r = client.get("/owner")
    assert r.status_code == 200
    assert "老板面板" in r.get_data(as_text=True)
    assert "type=\"password\"" in r.get_data(as_text=True)


def test_url_pass_param_rejected(client):
    """修复前 ?pass=secret123 可直接进入面板,修复后必须走会话。"""
    r = client.get("/owner?pass=secret123")
    assert "type=\"password\"" in r.get_data(as_text=True)


def test_wrong_password_rejected(client):
    r = client.post("/owner/login", data={"pass": "wrong"})
    assert r.status_code == 403


def test_correct_password_gets_session(client):
    r = client.post("/owner/login", data={"pass": "secret123"})
    assert r.status_code == 302
    cookie = r.headers.get("Set-Cookie", "")
    assert "owner_session=" in cookie
    assert "secret123" not in cookie, "cookie 不应包含明文密码"
    assert "HttpOnly" in cookie


def test_session_cookie_grants_access(client):
    r = client.post("/owner/login", data={"pass": "secret123"})
    c = r.headers.get("Set-Cookie").split(";")[0]
    r2 = client.get("/owner", headers={"Cookie": c})
    assert "type=\"password\"" not in r2.get_data(as_text=True)


def test_api_requires_auth(client):
    r = client.get("/api/leads")
    assert r.status_code == 403


def test_empty_password_denied_by_default(monkeypatch):
    """修复前空密码直接放行,修复后默认拒绝。"""
    monkeypatch.setattr(renov_bot_web, "OWNER_PASS", "")
    renov_bot_web.app.config["TESTING"] = True
    c = renov_bot_web.app.test_client()
    r = c.get("/owner")
    assert "type=\"password\"" in r.get_data(as_text=True)
    r2 = c.post("/owner/login", data={"pass": ""})
    assert r2.status_code == 403


def test_logout_invalidates_session(client):
    r = client.post("/owner/login", data={"pass": "secret123"})
    c = r.headers.get("Set-Cookie").split(";")[0]
    client.post("/owner/logout", headers={"Cookie": c})
    r2 = client.get("/owner", headers={"Cookie": c})
    assert "type=\"password\"" in r2.get_data(as_text=True)