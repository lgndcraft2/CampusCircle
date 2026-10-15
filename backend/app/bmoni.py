"""B Moni Embedded sandbox adapter.

Verified against https://embedded-docs.bmoni.com (llms-full.txt) and the sandbox OpenAPI document at
https://embedded-dev.bmoni.com/docs/openapi.json. Only the sandbox origin is permitted (see config.validate_runtime).

Implemented capabilities: create a partner user, provision a CNGN smart wallet with a server-held sandbox owner key,
read wallet balances, and verify/deduplicate signed webhooks. KYC, virtual accounts, and payouts are deliberately not
implemented: they need real-format identity data (BVN, ID documents) that CampusCircle must not collect.
"""
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

from .config import settings

log = logging.getLogger('campuscircle.bmoni')
WALLET_CURRENCY = 'CNGN'  # Smart-wallet calls take the stablecoin code; CNGN is the NGN rail.
SECRET_PATTERN = re.compile(r'(pk_[A-Za-z0-9_]+|0x[0-9a-fA-F]{64,})')


@dataclass
class Outcome:
    """`uncertain` means the provider may have acted; reconcile before retrying a mutation."""
    kind: Literal['ok', 'failed', 'uncertain']
    data: Any = None
    message: str = ''
    status_code: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.kind == 'ok'


def redact(text: str) -> str:
    return SECRET_PATTERN.sub('[redacted]', text)[:300]


def owner_account(circle_id: str):
    """Deterministic sandbox owner key per circle, derived from BMONI_SIGNER_SEED. Nothing secret is stored in the DB.

    This is a sandbox-only custody shortcut; B Moni recommends device secure elements or a KMS/HSM for real keys.
    """
    seed = settings().bmoni_signer_seed.encode()
    key = hmac.new(seed, f'campuscircle-circle-treasury:{circle_id}'.encode(), hashlib.sha256).digest()
    return Account.from_key(key)


def parse_amount_minor(value: Any) -> int:
    """Convert a provider decimal string like "1000.00" to integer kobo without floating point."""
    if not isinstance(value, str):
        raise ValueError('amount must be a decimal string')
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('amount is not a decimal') from exc
    minor = amount * 100
    if amount <= 0 or minor != minor.to_integral_value():
        raise ValueError('amount must be positive with at most two decimal places')
    return int(minor)


class BmoniClient:
    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 20):
        cfg = settings()
        if not cfg.bmoni_api_key:
            raise RuntimeError('B Moni is not configured. Set BMONI_API_KEY and BMONI_SIGNER_SEED to use the sandbox.')
        self._client = httpx.Client(base_url=cfg.bmoni_base_url.rstrip('/'), timeout=timeout, transport=transport,
                                    headers={'x-api-key': cfg.bmoni_api_key, 'accept': 'application/json'})

    def close(self):
        self._client.close()

    def _request(self, method: str, path: str, body: dict | None = None) -> Outcome:
        mutating = method != 'GET'
        try:
            response = self._client.request(method, path, json=body)
        except httpx.ConnectError as exc:
            return Outcome('failed', message=f'Could not connect to B Moni ({type(exc).__name__}).')
        except httpx.TimeoutException as exc:
            # A request that timed out after sending may still have been applied by the provider.
            return Outcome('uncertain' if mutating else 'failed', message=f'B Moni did not respond in time ({type(exc).__name__}).')
        except httpx.HTTPError as exc:
            return Outcome('uncertain' if mutating else 'failed', message=f'B Moni request error ({type(exc).__name__}).')
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code >= 500:
            return Outcome('uncertain' if mutating else 'failed', data, f'B Moni server error {response.status_code}.', response.status_code)
        if response.status_code >= 400:
            message = data.get('message') if isinstance(data, dict) else None
            text = '; '.join(map(str, message)) if isinstance(message, list) else str(message or response.reason_phrase)
            log.info('bmoni.rejected', extra={'path': path, 'status': response.status_code})
            return Outcome('failed', data, redact(text), response.status_code)
        if data is None:
            return Outcome('uncertain' if mutating else 'failed', message='B Moni returned a response that was not JSON.', status_code=response.status_code)
        return Outcome('ok', data, status_code=response.status_code)

    def create_user(self, first_name: str, last_name: str, email: str, phone: str) -> Outcome:
        result = self._request('POST', '/v1/users', {'firstName': first_name, 'lastName': last_name, 'email': email, 'phoneNumber': phone})
        if result.ok:
            user_id = (result.data.get('user') or {}).get('bmoniUserId') if isinstance(result.data, dict) else None
            if not isinstance(user_id, str) or not user_id:
                return Outcome('uncertain', result.data, 'B Moni created a user but the response had no bmoniUserId.', result.status_code)
            result.extra['bmoni_user_id'] = user_id
        return result

    def find_user_by_email(self, email: str, max_pages: int = 20) -> Outcome:
        """Reconcile an uncertain or conflicting create by paging the partner's users (GET /v1/users)."""
        for page in range(1, max_pages + 1):
            result = self._request('GET', f'/v1/users?page={page}&limit=100')
            if not result.ok:
                return result
            users = result.data.get('users') if isinstance(result.data, dict) else None
            if not isinstance(users, list):
                return Outcome('failed', result.data, 'User list response was malformed.')
            match = next((u for u in users if isinstance(u, dict) and str(u.get('email', '')).lower() == email.lower()), None)
            if match and isinstance(match.get('bmoniUserId'), str):
                return Outcome('ok', match, extra={'bmoni_user_id': match['bmoniUserId']})
            if len(users) < 100:
                break
        return Outcome('failed', message='No matching B Moni user was found.')

    def provision_wallet(self, user_id: str, account) -> Outcome:
        challenge = self._request('POST', f'/v1/users/{user_id}/smart-wallets/owner-proof-challenges',
                                  {'currency': WALLET_CURRENCY, 'userOwnerAddress': account.address})
        if not challenge.ok:
            # A challenge has no side effects beyond itself, so uncertainty here is safe to retry later.
            return Outcome('failed', challenge.data, f'Owner-proof challenge failed: {challenge.message}', challenge.status_code)
        data = challenge.data if isinstance(challenge.data, dict) else {}
        if not isinstance(data.get('challengeId'), str) or not isinstance(data.get('message'), str):
            return Outcome('failed', data, 'Owner-proof challenge response was malformed.')
        # EIP-191 personal_sign, exactly as the B Moni quickstart documents for this step.
        signature = Account.sign_message(encode_defunct(text=data['message']), account.key).signature.hex()
        signature = signature if signature.startswith('0x') else f'0x{signature}'
        wallet = self._request('POST', f'/v1/users/{user_id}/smart-wallets/create-managed', {
            'currency': WALLET_CURRENCY, 'userOwnerAddress': account.address,
            'ownerProofChallengeId': data['challengeId'], 'ownerProofSignature': signature})
        if wallet.ok:
            body = wallet.data if isinstance(wallet.data, dict) else {}
            if not isinstance(body.get('id'), str):
                return Outcome('uncertain', body, 'Wallet response had no id; check the wallet list before retrying.')
            wallet.extra.update(wallet_id=body['id'], wallet_address=body.get('walletAddress'), active=bool(body.get('isActive')))
        return wallet

    def list_wallets(self, user_id: str) -> Outcome:
        return self._request('GET', f'/v1/users/{user_id}/smart-wallets/account/wallets')

    def balances(self, user_id: str) -> Outcome:
        result = self._request('GET', f'/v1/users/{user_id}/smart-wallets/account/balances')
        if result.ok:
            # The OpenAPI document wraps this in `data`; the live sandbox (verified 2026-10-15) returns it unwrapped.
            body = result.data if isinstance(result.data, dict) else {}
            data = body.get('data') if isinstance(body.get('data'), dict) else body
            items = data.get('balances')
            if not isinstance(items, list):
                return Outcome('failed', result.data, 'Balance response was malformed.')
            result.extra['balances'] = [{'smart_wallet_id': i.get('smartWalletId'), 'currency': i.get('currency'), 'balance': i.get('balance'), 'error': i.get('error')}
                                        for i in items if isinstance(i, dict)]
        return result


def verify_webhook(raw_body: bytes, signature: str, secret: str) -> bool:
    """HMAC-SHA256 over the raw body, hex-encoded, compared in constant time (B Moni webhooks guide)."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())
