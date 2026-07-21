"""Tests for the RustFS object store client."""

import pytest

from edutap.pass_builder.clients.objectstore import ObjectStore


def test_content_key_is_tenant_version_sha() -> None:
    """The content key joins tenant, version and sha256 with slashes."""
    assert ObjectStore.content_key("lmu", "v1", "abc") == "lmu/v1/abc"


@pytest.mark.integration
async def test_put_and_get_round_trip() -> None:
    """A blob written via put() is retrievable unchanged via get()."""
    store = ObjectStore(
        endpoint_url="http://localhost:9000",
        bucket="pass-builder",
        access_key="pass_builder",
        secret_key="pass_builder",  # noqa: S106 - compose test credential
    )
    key = ObjectStore.content_key("lmu", "v1", "roundtrip-sha")
    await store.put(key, b"hello world", "text/plain")

    result = await store.get(key)

    assert result == b"hello world"
