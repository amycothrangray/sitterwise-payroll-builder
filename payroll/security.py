"""Local app access and bounded input. No credentials belong in a backup."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
import zipfile

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_WORKBOOK_BYTES = 128 * 1024 * 1024


def access_token(database: Path, create: bool = True) -> str | None:
    """Keep the local access key in an owner-only file beside the database."""
    path = database.parent / ('.' + database.name + '-access-token')
    if not path.exists() and not create:
        return None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    if create:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('The local access key must be a file owned by this user.')
        os.fchmod(fd, 0o600)
        # Serialize simultaneous launches so they cannot create different keys.
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = os.read(fd, 128).decode('ascii')
        if not raw and create:
            raw = secrets.token_urlsafe(32)
            os.write(fd, raw.encode('ascii'))
            os.fsync(fd)
        if len(raw) != 43 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in raw):
            raise ValueError('The local connection file is damaged. It needs to be reset on this Mac.')
        return raw
    finally:
        os.close(fd)


def local_headers(database: Path) -> dict:
    token = access_token(database, create=False)
    return {'Authorization': 'Bearer ' + token} if token else {}


def checked_workbook(path: Path) -> None:
    """Refuse oversized or highly expanding spreadsheets before XML parsing."""
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError('This spreadsheet is too large (maximum 32 MB).')
    if path.suffix.lower() not in ('.xlsx', '.xlsm'):
        return
    # Fail closed if XML entity protections were omitted from an installation.
    import defusedxml.ElementTree  # noqa: F401
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_WORKBOOK_BYTES:
            raise ValueError('This spreadsheet expands beyond the allowed size.')
