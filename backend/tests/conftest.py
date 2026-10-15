import os

os.environ['ALLOW_SQLITE'] = 'true'
os.environ['LOG_LEVEL'] = 'WARNING'
os.environ['ENVIRONMENT'] = 'test'
# Isolate tests from any developer .env: providers are mocked per test.
for _name in ('BREVO_API_KEY', 'BREVO_SENDER_EMAIL', 'GROQ_API_KEY', 'BMONI_API_KEY', 'BMONI_WEBHOOK_SECRET', 'BMONI_SIGNER_SEED'):
    os.environ[_name] = ''
os.environ['DEVELOPMENT_OUTBOX'] = 'true'
os.environ['DATABASE_URL'] = 'sqlite:///./test-unused.db'

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db, make_engine
from app.main import app
from app.models import Circle, Membership, User
from app.security import hasher


@pytest.fixture
def store(tmp_path):
    engine = make_engine(f'sqlite:///{tmp_path / "test.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        users = []
        encoded = hasher.hash('test-password-12345')
        for i in range(4):
            u = User(email=f'student{i}@students.example.edu', full_name=f'Student {i}', phone='+2348010000000', university='Demo University', department='Computer Science', password_hash=encoded, email_verified=True)
            db.add(u)
            users.append(u)
        db.flush()
        circle = Circle(name='Test Circle', description='A synthetic test circle.', purpose='Study', university='Demo University', rules='All money is simulated and pooled.', joining_method='open')
        db.add(circle)
        db.flush()
        for i, u in enumerate(users[:3]):
            db.add(Membership(circle_id=circle.id, user_id=u.id, role='owner' if i == 0 else 'member'))
        ids = [u.id for u in users]
        circle_id = circle.id
    def dependency():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
    app.dependency_overrides[get_db] = dependency
    yield factory, ids, circle_id
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def clients(store):
    result = []
    for i in range(4):
        client = TestClient(app)
        response = client.post('/api/v1/auth/login', json={'email': f'student{i}@students.example.edu', 'password': 'test-password-12345'})
        assert response.status_code == 200, response.text
        client.headers['x-csrf-token'] = client.cookies['cc_csrf']
        result.append(client)
    yield result
    for client in result:
        client.close()


@pytest.fixture
def proposal(store, clients):
    _, _, circle_id = store
    req = clients[1].post(f'/api/v1/circles/{circle_id}/loan-requests', json={'principal_minor': 10001, 'purpose': 'Synthetic study materials', 'explanation': 'A request used by the automated tests.', 'frequency': 'monthly', 'instalment_count': 3})
    assert req.status_code == 201, req.text
    response = clients[0].post(f'/api/v1/circles/{circle_id}/proposals', json={'request_id': req.json()['id'], 'interest_bps': 500, 'fees_minor': 31, 'instalment_count': 3})
    assert response.status_code == 201, response.text
    return response.json()
