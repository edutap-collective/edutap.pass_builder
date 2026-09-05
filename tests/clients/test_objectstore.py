"""Tests for the RustFS object store client."""

import pytest
from botocore.exceptions import ClientError

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


class _FakeS3:
    """A minimal S3 client recording what `ensure_bucket` asks of it."""

    def __init__(self, head: Exception | None = None, create: Exception | None = None):
        self._head = head
        self._create = create
        self.calls: list[str] = []

    async def head_bucket(self, Bucket: str) -> None:  # noqa: N803 - botocore's spelling
        self.calls.append("head")
        if self._head is not None:
            raise self._head

    async def create_bucket(self, Bucket: str) -> None:  # noqa: N803 - botocore's spelling
        self.calls.append("create")
        if self._create is not None:
            raise self._create

    async def __aenter__(self) -> "_FakeS3":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    """Stands in for `aioboto3.Session`, handing out one prepared client."""

    def __init__(self, s3: _FakeS3) -> None:
        self._s3 = s3

    def client(self, *args: object, **kwargs: object) -> _FakeS3:
        """Return the prepared client, ignoring service name and endpoint."""
        return self._s3


def _store_with(s3: _FakeS3, monkeypatch: pytest.MonkeyPatch) -> ObjectStore:
    """Return a store whose session hands out `s3`.

    Through `monkeypatch` rather than a plain assignment: the attribute is typed
    as `aioboto3.Session`, and a double is not one. The alternative would be a
    `cast` that tells the typechecker something untrue for the rest of the file.
    """
    store = ObjectStore(
        endpoint_url="https://rustfs:9000",
        bucket="edutap-production-pass-builder",
        access_key="edutap_production",
        secret_key="irrelevant",  # noqa: S106 - not a credential, a stub
    )
    monkeypatch.setattr(store, "_session", _FakeSession(s3))
    return store


def _client_error(code: str) -> ClientError:
    """Return a botocore ClientError carrying the given error code."""
    return ClientError({"Error": {"Code": code, "Message": code}}, "HeadBucket")


async def test_an_existing_bucket_is_not_created_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal production start: the bucket stands, so nothing is created.

    This is the case that broke the first sharp deploy on 2026-09-04. The
    deployment provisions its buckets from Ansible and hands this service a
    credential scoped to the one bucket it owns; that credential may not create
    anything. RustFS answered the unconditional `create_bucket` with
    `UnauthorizedAccess: Your account is not signed up`, which is neither
    "already exists" nor recognisably about permissions, and the service
    crash-looped past a bucket that was right there.
    """
    s3 = _FakeS3()

    await _store_with(s3, monkeypatch).ensure_bucket()

    assert s3.calls == ["head"]


async def test_a_missing_bucket_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's case: an empty MinIO and an admin credential."""
    s3 = _FakeS3(head=_client_error("404"))

    await _store_with(s3, monkeypatch).ensure_bucket()

    assert s3.calls == ["head", "create"]


async def test_a_race_between_two_workers_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two workers may start at once; the loser's create is not a failure."""
    s3 = _FakeS3(
        head=_client_error("NoSuchBucket"),
        create=_client_error("BucketAlreadyOwnedByYou"),
    )

    await _store_with(s3, monkeypatch).ensure_bucket()

    assert s3.calls == ["head", "create"]


async def test_a_refused_credential_is_not_papered_over_with_a_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Access denied is a real problem, and creating on top of it hides it.

    Without this bound the fix would trade one confusing failure for another: a
    wrong endpoint or a rejected key would end in a `create_bucket` error rather
    than the access error that says what is actually wrong.
    """
    s3 = _FakeS3(head=_client_error("AccessDenied"))

    with pytest.raises(ClientError, match="AccessDenied"):
        await _store_with(s3, monkeypatch).ensure_bucket()

    assert s3.calls == ["head"]
