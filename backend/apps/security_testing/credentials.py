"""Authenticated encryption for transient test identities; never serialized."""
import base64
import hashlib
import hmac
import json

from cryptography.fernet import Fernet
from django.conf import settings


def _cipher():
    key = hmac.new(settings.SECRET_KEY.encode('utf-8'), b'security-testing-identities-v1', hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_identities(value):
    if not value:
        return ''
    return _cipher().encrypt(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')).decode('ascii')


def decrypt_identities(value):
    if not value:
        return {}
    return json.loads(_cipher().decrypt(value.encode('ascii')).decode('utf-8'))


def identity_summary(value):
    return {key: {'name': item.get('name', key.upper()), 'configured': True}
            for key, item in decrypt_identities(value).items()}
