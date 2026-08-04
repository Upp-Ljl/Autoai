from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import getpass
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResolvedSecret:
    value: str | None
    source: str
    fingerprint: str | None


def fingerprint(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return data
    in_blob, in_buffer = _blob(data)
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "SAT2 Relay credentials",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    _ = in_buffer
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return data
    in_blob, in_buffer = _blob(data)
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    _ = in_buffer
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


class CredentialStore:
    """Small local secret store.

    Windows uses user-bound DPAPI. Other platforms use a mode-0600 file. The
    browser extension never receives these secrets.
    """

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        raw = self.path.read_bytes()
        if not raw:
            return {}
        if raw.startswith(b"SAT2DPAPI1\n"):
            protected = base64.b64decode(raw.split(b"\n", 1)[1])
            plain = _dpapi_unprotect(protected)
        elif raw.startswith(b"SAT2PLAIN1\n"):
            plain = base64.b64decode(raw.split(b"\n", 1)[1])
        else:
            raise ValueError("unrecognized credential store format")
        payload = json.loads(plain.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("credential store payload must be an object")
        return {str(k): str(v) for k, v in payload.items() if v is not None}

    def save(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plain = json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if sys.platform == "win32":
            raw = b"SAT2DPAPI1\n" + base64.b64encode(_dpapi_protect(plain))
        else:
            raw = b"SAT2PLAIN1\n" + base64.b64encode(plain)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(raw)
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def set(self, key: str, value: str) -> None:
        values = self.load()
        values[key] = value
        self.save(values)

    def clear(self, key: str | None = None) -> None:
        if key is None:
            self.path.unlink(missing_ok=True)
            return
        values = self.load()
        values.pop(key, None)
        self.save(values)

    def summary(self) -> dict[str, Any]:
        values = self.load()
        return {
            "path": str(self.path),
            "protection": "windows-dpapi" if sys.platform == "win32" else "file-mode-0600",
            "keys": {key: fingerprint(value) for key, value in values.items()},
        }


def resolve_secret(store: CredentialStore, key: str, env_name: str) -> ResolvedSecret:
    values = store.load()
    if values.get(key):
        value = values[key]
        return ResolvedSecret(value, f"credential_store:{store.path}", fingerprint(value))
    value = os.environ.get(env_name)
    if value:
        return ResolvedSecret(value, f"environment:{env_name}", fingerprint(value))
    return ResolvedSecret(None, "missing", None)


def prompt_secret(label: str) -> str:
    first = getpass.getpass(f"{label}: ").strip()
    if not first:
        raise ValueError("secret cannot be empty")
    second = getpass.getpass(f"Confirm {label}: ").strip()
    if first != second:
        raise ValueError("secret confirmation does not match")
    return first
