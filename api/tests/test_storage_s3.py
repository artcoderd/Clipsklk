import io

import boto3
import pytest
from moto import mock_aws

from app.storage import S3Storage, StorageError


@pytest.fixture
def s3():
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="clipeo")
        yield S3Storage("clipeo", None, "us-east-1", "test", "test", 600)


def test_roundtrip(s3, tmp_path):
    assert s3.save_stream("users/u/a.mp4", io.BytesIO(b"x" * 10_000), max_bytes=20_000) == 10_000
    assert s3.exists("users/u/a.mp4") and s3.size("users/u/a.mp4") == 10_000
    src = tmp_path / "t.json"
    src.write_text("{}")
    s3.put_file("users/u/t.json", src, "application/json")
    s3.get_file("users/u/a.mp4", tmp_path / "out.mp4")
    assert (tmp_path / "out.mp4").read_bytes() == b"x" * 10_000
    assert s3.local_path("users/u/a.mp4") is None
    url = s3.url("users/u/a.mp4", "clip.mp4")
    assert "X-Amz-Signature" in url and "attachment" in url
    s3.delete_prefix("users/u/")
    assert not s3.exists("users/u/a.mp4")


def test_size_limit(s3):
    with pytest.raises(StorageError):
        s3.save_stream("big.mp4", io.BytesIO(b"x" * 10_000), max_bytes=5_000)
    assert not s3.exists("big.mp4")


def test_missing(s3, tmp_path):
    with pytest.raises(StorageError):
        s3.get_file("nope", tmp_path / "x")
