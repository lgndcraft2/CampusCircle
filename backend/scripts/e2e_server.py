"""Start an isolated backend for Playwright: fresh SQLite database, migrations applied, development outbox on."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DB = BACKEND / 'e2e.db'
for suffix in ('', '-wal', '-shm'):
    Path(f'{DB}{suffix}').unlink(missing_ok=True)
env = {**os.environ, 'ENVIRONMENT': 'development', 'DATABASE_URL': f'sqlite:///{DB.as_posix()}', 'ALLOW_SQLITE': 'true', 'DEVELOPMENT_OUTBOX': 'true',
       'FRONTEND_URL': 'http://127.0.0.1:5183', 'LOG_LEVEL': 'WARNING', 'GROQ_API_KEY': '', 'BREVO_API_KEY': '', 'BMONI_API_KEY': ''}
subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=BACKEND, env=env, check=True)
os.chdir(BACKEND)
os.execve(sys.executable, [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8010'], env) if os.name != 'nt' else \
    sys.exit(subprocess.call([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8010'], cwd=BACKEND, env=env))
