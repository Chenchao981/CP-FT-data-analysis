"""Account-scoped Windows Credential Manager storage; credentials never enter SQL."""
from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes

from app.core.errors import DomainError


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def _target(reference: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,63}", reference):
        raise DomainError("FTP_CREDENTIAL_REF_INVALID", "FTP 凭据引用无效", 422)
    return f"NCE_PYMS:FTP:{reference}"


def _api():
    if os.name != "nt":
        raise DomainError("FTP_CREDENTIAL_PLATFORM", "该部署使用 Windows 凭据管理器，请在 Worker 运行账号下配置", 503)
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    return api


def read_ftp_credential(reference: str) -> tuple[str, str]:
    target = _target(reference)
    api = _api()
    pointer = ctypes.POINTER(_Credential)()
    if not api.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        raise DomainError("FTP_CREDENTIAL_UNAVAILABLE", "当前运行账号无法读取 FTP 凭据，请在该账号下配置指定引用", 503)
    try:
        record = pointer.contents
        username = record.UserName or ""
        password = ctypes.string_at(record.CredentialBlob, record.CredentialBlobSize).decode("utf-16-le")
        if not username or not password or any(char in username + password for char in "\r\n\x00"):
            raise DomainError("FTP_CREDENTIAL_INVALID", "FTP 凭据内容无效", 503)
        return username, password
    finally:
        api.CredFree(pointer)


def store_ftp_credential(reference: str, username: str, password: str) -> None:
    target = _target(reference)
    if not username or not password or any(char in username + password for char in "\r\n\x00"):
        raise ValueError("FTP 凭据不能为空或包含控制字符")
    raw = password.encode("utf-16-le")
    if len(raw) > 2560:
        raise ValueError("FTP 凭据长度超过 Windows 限制")
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    record = _Credential(Type=1, TargetName=target, UserName=username,
                         CredentialBlobSize=len(raw), CredentialBlob=buffer, Persist=2)
    try:
        if not _api().CredWriteW(ctypes.byref(record), 0):
            raise DomainError("FTP_CREDENTIAL_STORE_FAILED", "Windows 凭据保存失败", 503)
    finally:
        ctypes.memset(buffer, 0, len(raw))
