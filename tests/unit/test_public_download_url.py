"""Download links are signed for the address the browser uses, without network calls."""
from types import SimpleNamespace as NS

from pydantic import SecretStr

from storage.minio_client import build_signing_client, get_presigned_url


def storage(**kw):
    base = dict(public_endpoint="localhost:9010", public_secure=None, secure=False,
                access_key="minioadmin", secret_key=SecretStr("x"), region="us-east-1")
    base.update(kw)
    return NS(**base)


def test_link_uses_public_host():
    url = get_presigned_url(build_signing_client(storage()), "crosscasting-files", "job/f.pdf", 1)
    assert url.startswith("http://localhost:9010/crosscasting-files/job/f.pdf?") and "X-Amz-Signature=" in url


def test_public_https():
    url = get_presigned_url(build_signing_client(storage(public_endpoint="files.example.com",
                                                         public_secure=True)), "crosscasting-files", "k", 1)
    assert url.startswith("https://files.example.com/crosscasting-files/k?")


def test_not_configured_falls_back():
    assert build_signing_client(storage(public_endpoint="")) is None
