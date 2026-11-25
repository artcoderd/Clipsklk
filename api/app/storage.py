"""Storage abstraction. Phase 1 writes to local disk; phase 2 adds an S3/R2
backend with the same interface so the rest of the code does not change."""
import os
import shutil
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings


class StorageError(Exception):
    pass


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve()) + os.sep):
            raise StorageError("invalid key")
        return p

    def save_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> int:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            with open(tmp, "wb") as out:
                while chunk := stream.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise StorageError("file_too_large")
                    out.write(chunk)
            tmp.replace(path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return written

    def local_path(self, key: str) -> Path:
        return self._path(key)

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


def get_storage() -> LocalStorage:
    return LocalStorage(get_settings().storage_dir)
