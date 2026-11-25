"""Storage abstraction with two backends sharing one interface:

- LocalStorage: a directory (a Docker volume shared by API and workers).
- S3Storage: any S3-compatible bucket (Cloudflare R2, AWS S3, MinIO).

Keys look like ``users/<uid>/assets/<aid>/source.mp4``.
"""
import os
import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

from app.config import get_settings


class StorageError(Exception):
    pass


class _LimitedReader:
    """Wraps a stream and raises once more than max_bytes have been read."""

    def __init__(self, stream: BinaryIO, max_bytes: int):
        self.stream, self.max_bytes, self.read_bytes = stream, max_bytes, 0

    def read(self, n: int = -1) -> bytes:
        chunk = self.stream.read(n)
        self.read_bytes += len(chunk)
        if self.read_bytes > self.max_bytes:
            raise StorageError("file_too_large")
        return chunk


class Storage(Protocol):
    def save_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> int: ...
    def put_file(self, key: str, path: str | Path, content_type: str | None = None) -> None: ...
    def get_file(self, key: str, dest: str | Path) -> None: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def url(self, key: str, download_name: str | None = None) -> str | None: ...
    def local_path(self, key: str) -> Path | None: ...
    def delete_prefix(self, prefix: str) -> None: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        root = self.root.resolve()
        p = (root / key).resolve()
        if not str(p).startswith(str(root) + os.sep):
            raise StorageError("invalid key")
        return p

    def save_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> int:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        reader = _LimitedReader(stream, max_bytes)
        try:
            with open(tmp, "wb") as out:
                while chunk := reader.read(1024 * 1024):
                    out.write(chunk)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return reader.read_bytes

    def put_file(self, key: str, path: str | Path, content_type: str | None = None) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if Path(path).resolve() != dest:
            shutil.copyfile(path, dest)

    def get_file(self, key: str, dest: str | Path) -> None:
        src = self._path(key)
        if not src.exists():
            raise StorageError("not_found")
        if Path(dest).resolve() != src:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def url(self, key: str, download_name: str | None = None) -> str | None:
        return None  # served by the API itself (with auth + HTTP range support)

    def local_path(self, key: str) -> Path | None:
        return self._path(key)

    def delete_prefix(self, prefix: str) -> None:
        p = self._path(prefix.rstrip("/"))
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


class S3Storage:
    def __init__(self, bucket: str, endpoint_url: str | None, region: str,
                 access_key: str | None, secret_key: str | None, presign_ttl: int):
        import boto3
        from botocore.config import Config

        self.bucket, self.presign_ttl = bucket, presign_ttl
        self.client = boto3.client(
            "s3", endpoint_url=endpoint_url, region_name=region,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
        )

    def save_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> int:
        reader = _LimitedReader(stream, max_bytes)
        try:
            self.client.upload_fileobj(reader, self.bucket, key)  # multipart for big files
        except StorageError:
            self._delete(key)
            raise
        return reader.read_bytes

    def put_file(self, key: str, path: str | Path, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else None
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs=extra)

    def get_file(self, key: str, dest: str | Path) -> None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download_file(self.bucket, key, str(dest))
        except self.client.exceptions.ClientError as e:
            raise StorageError("not_found") from e

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False

    def size(self, key: str) -> int:
        return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]

    def url(self, key: str, download_name: str | None = None) -> str | None:
        params = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=self.presign_ttl)

    def local_path(self, key: str) -> Path | None:
        return None

    def _delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "s3":
            if not s.s3_bucket:
                raise RuntimeError("S3_BUCKET is required when STORAGE_BACKEND=s3")
            _storage = S3Storage(s.s3_bucket, s.s3_endpoint_url, s.s3_region, s.s3_access_key_id,
                                 s.s3_secret_access_key, s.s3_presign_ttl_seconds)
        else:
            _storage = LocalStorage(s.storage_dir)
    return _storage
