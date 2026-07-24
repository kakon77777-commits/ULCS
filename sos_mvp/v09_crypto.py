from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .review import ReviewError, canonical_bytes

ALGORITHM = "ed25519"


def generate_keypair(
    private_path: str | Path,
    public_path: str | Path,
) -> str:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_target = Path(private_path)
    public_target = Path(public_path)
    _atomic_write_bytes(private_target, private_bytes, private=True)
    _atomic_write_bytes(public_target, public_bytes, private=False)
    return public_key_id(public_key)


def load_private_key(path: str | Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    except (OSError, TypeError, ValueError) as exc:
        raise ReviewError(f"無法讀取 Ed25519 private key：{exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ReviewError("Private key 不是 Ed25519 key。")
    return key


def load_public_key(path: str | Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(Path(path).read_bytes())
    except (OSError, TypeError, ValueError) as exc:
        raise ReviewError(f"無法讀取 Ed25519 public key：{exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ReviewError("Public key 不是 Ed25519 key。")
    return key


def public_key_id(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def sign_mapping(key: Ed25519PrivateKey, payload: Mapping[str, Any]) -> str:
    signature = key.sign(canonical_bytes(payload))
    return base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def verify_mapping(
    key: Ed25519PublicKey,
    payload: Mapping[str, Any],
    signature: str,
) -> None:
    try:
        decoded = _decode_signature(signature)
        key.verify(decoded, canonical_bytes(payload))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ReviewError("Ed25519 signature 驗證失敗。") from exc


def _decode_signature(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ReviewError("Ed25519 signature 不可為空。")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise ReviewError("Ed25519 signature 不是合法 base64url。") from exc
    if len(decoded) != 64:
        raise ReviewError("Ed25519 signature 長度無效。")
    return decoded


def _atomic_write_bytes(path: Path, content: bytes, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if private:
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
        os.replace(temp_name, path)
        if private:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass
