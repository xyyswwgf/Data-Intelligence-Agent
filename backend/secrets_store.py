"""
敏感配置加密 — 模型 API Key、工具凭证等以 Fernet（AES-128-CBC + HMAC）加密后存库

密钥来源：APP_SECRET_KEY 环境变量（推荐）；未设置时在数据目录生成 .secret_key 并复用。
接口只返回掩码（sk-****abcd），明文只在调用模型/工具时于服务端解密。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from backend.config import APP_DATA_DIR, APP_SECRET_KEY

logger = logging.getLogger("nl2sql.secrets")

_fernet: Fernet | None = None


def _load_key() -> bytes:
    if APP_SECRET_KEY:
        # 任意长度的口令 → 32 字节 Fernet 密钥
        return base64.urlsafe_b64encode(hashlib.sha256(APP_SECRET_KEY.encode()).digest())
    path = os.path.join(APP_DATA_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read().strip()
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    logger.warning("未设置 APP_SECRET_KEY，已生成加密密钥 %s；生产环境请改用环境变量并备份", path)
    return key


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def encrypt(plain: str | None) -> str | None:
    if not plain:
        return None
    return _cipher().encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.error("无法解密已保存的凭证：APP_SECRET_KEY 可能已更换，请重新填写该凭证")
        return ""


def mask(plain: str | None) -> str:
    if not plain:
        return ""
    return f"{plain[:3]}****{plain[-4:]}" if len(plain) > 10 else "****"
