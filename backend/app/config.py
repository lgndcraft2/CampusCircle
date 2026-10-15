from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
# B Moni documents this as the development / sandbox origin. Production is never enabled here.
BMONI_SANDBOX_URL = 'https://embedded-dev.bmoni.com'


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    environment: str = 'development'
    database_url: str = 'sqlite:///./campuscircle.db'
    allow_sqlite: bool = False
    app_secret: str = 'development-only-change-before-deployment'
    frontend_url: str = 'http://127.0.0.1:5173'
    cookie_secure: bool = False
    development_outbox: bool = False
    log_level: str = 'INFO'
    # Brevo transactional email (https://developers.brevo.com/docs/send-a-transactional-email)
    brevo_api_key: str = ''
    brevo_sender_email: str = ''
    brevo_sender_name: str = 'CampusCircle'
    # Groq (OpenAI-compatible chat completions). Model ids: https://console.groq.com/docs/models
    groq_api_key: str = ''
    groq_model: str = 'openai/gpt-oss-20b'
    groq_base_url: str = 'https://api.groq.com/openai/v1'
    # B Moni Embedded sandbox (https://embedded-docs.bmoni.com)
    bmoni_base_url: str = BMONI_SANDBOX_URL
    bmoni_api_key: str = ''
    bmoni_webhook_secret: str = ''
    bmoni_signer_seed: str = ''
    session_minutes: int = 15
    refresh_days: int = 7
    otp_minutes: int = 10
    financial_mode: str = 'simulation'
    university_directory: str = str(ROOT / 'universities.json')

    @property
    def email_provider(self) -> str:
        if self.brevo_api_key and self.brevo_sender_email:
            return 'brevo'
        return 'development_outbox' if self.development_outbox and self.environment != 'production' else 'unavailable'

    @property
    def ai_provider(self) -> str:
        return 'groq' if self.groq_api_key and self.groq_model else 'unavailable'

    @property
    def bmoni_status(self) -> str:
        return 'sandbox_configured' if self.bmoni_api_key and self.bmoni_signer_seed else 'unavailable'

    def validate_runtime(self):
        if self.database_url.startswith('sqlite') and not self.allow_sqlite:
            raise RuntimeError('SQLite requires ALLOW_SQLITE=true. Use PostgreSQL for deployment.')
        if self.financial_mode != 'simulation':
            raise RuntimeError('The internal ledger runs in simulation mode only. External sandbox activity is recorded separately.')
        if self.bmoni_base_url.rstrip('/') != BMONI_SANDBOX_URL:
            raise RuntimeError('Only the B Moni sandbox base URL is permitted. No real money operations are enabled.')
        if self.bmoni_api_key and len(self.bmoni_signer_seed) < 32:
            raise RuntimeError('BMONI_SIGNER_SEED must be at least 32 characters when B Moni is configured.')
        if self.environment == 'production':
            if self.development_outbox or not self.cookie_secure or len(self.app_secret) < 40 or self.app_secret.startswith('development'):
                raise RuntimeError('Production requires secure cookies, a strong APP_SECRET, and no development outbox.')
            if not self.database_url.startswith('postgresql') or not self.frontend_url.startswith('https://'):
                raise RuntimeError('Production requires PostgreSQL and an HTTPS frontend URL.')


@lru_cache
def settings() -> Settings:
    return Settings()
