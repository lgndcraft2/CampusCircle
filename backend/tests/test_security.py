from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import BrowserSession, User, Verification, now
from app.security import code_digest, digest
from datetime import timedelta


def test_csrf_origin_and_logout(store, clients):
    cid = store[2]
    client = clients[0]
    r = client.post(f'/api/v1/circles/{cid}/messages', json={'body': 'Hello'}, headers={'x-csrf-token': 'wrong'})
    assert r.status_code == 403
    assert client.post(f'/api/v1/circles/{cid}/messages', json={'body': 'Hello'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/v1/auth/logout').status_code == 200
    assert client.get('/api/v1/me').status_code == 401


def test_refresh_rotates_both_tokens_and_revocation(store, clients):
    client = clients[0]
    old_access, old_refresh = client.cookies['cc_access'], client.cookies['cc_refresh']
    assert client.post('/api/v1/auth/refresh').status_code == 200
    assert client.cookies['cc_access'] != old_access
    assert client.cookies['cc_refresh'] != old_refresh
    with store[0]() as db:
        assert db.scalar(select(BrowserSession).where(BrowserSession.access_hash == digest(old_access))) is None
    replay = TestClient(app)
    replay.cookies.set('cc_refresh', old_refresh)
    replay.headers['x-csrf-token'] = client.headers['x-csrf-token']
    assert replay.post('/api/v1/auth/refresh').status_code == 401


def test_otp_attempt_limit_and_single_use(store):
    factory, ids, _ = store
    with factory.begin() as db:
        db.get(User, ids[0]).email_verified = False
        db.add(Verification(user_id=ids[0], purpose='verify', code_hash=code_digest(ids[0], 'verify', '123456'), expires_at=now() + timedelta(minutes=10)))
    client = TestClient(app)
    payload = {'email': 'student0@students.example.edu', 'code': '000000'}
    for _ in range(5):
        assert client.post('/api/v1/auth/verify', json=payload).status_code == 400
    assert client.post('/api/v1/auth/verify', json={**payload, 'code': '123456'}).status_code == 400
    with factory.begin() as db:
        old = db.scalar(select(Verification))
        old.used = True
        db.add(Verification(user_id=ids[0], purpose='verify', code_hash=code_digest(ids[0], 'verify', '654321'), expires_at=now() + timedelta(minutes=10)))
    payload['code'] = '654321'
    assert client.post('/api/v1/auth/verify', json=payload).status_code == 200
    assert client.post('/api/v1/auth/verify', json=payload).status_code == 400


def test_profile_protected_fields_and_ai_unavailable(store, clients):
    assert clients[0].patch('/api/v1/me', json={'full_name': 'New Name', 'department': 'CS', 'email_verified': True}).status_code == 422
    assert clients[0].post('/api/v1/copilot', json={'question': 'Explain interest', 'use_ai': True, 'consent': True}).status_code == 503
    response = clients[0].post('/api/v1/copilot', json={'question': 'Explain interest'})
    assert response.json()['source'] == 'deterministic_guide'


def test_private_chat_and_moderation(store, clients):
    cid = store[2]
    msg = clients[1].post(f'/api/v1/circles/{cid}/messages', json={'body': '<script>alert(1)</script>'})
    assert msg.status_code == 201
    mid = msg.json()['id']
    assert clients[2].delete(f'/api/v1/circles/{cid}/messages/{mid}').status_code == 403
    assert clients[0].delete(f'/api/v1/circles/{cid}/messages/{mid}').status_code == 200
    item = clients[1].get(f'/api/v1/circles/{cid}/messages').json()['items'][0]
    assert item['deleted'] and item['body'] == ''


def test_password_reset_revokes_sessions(store, clients):
    factory, ids, _ = store
    with factory.begin() as db:
        db.add(Verification(user_id=ids[0], purpose='reset', code_hash=code_digest(ids[0], 'reset', '123456'), expires_at=now() + timedelta(minutes=10)))
    client = TestClient(app)
    r = client.post('/api/v1/auth/reset-password', json={'email': 'student0@students.example.edu', 'code': '123456', 'password': 'new-test-password-123'})
    assert r.status_code == 200
    assert clients[0].get('/api/v1/me').status_code == 401
    assert client.post('/api/v1/auth/login', json={'email': 'student0@students.example.edu', 'password': 'new-test-password-123'}).status_code == 200
