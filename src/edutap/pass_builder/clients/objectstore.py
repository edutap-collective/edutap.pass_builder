"""S3-compatible object store client for RustFS."""

import aioboto3
from botocore.exceptions import ClientError

_BUCKET_ALREADY_PROVISIONED = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}

_BUCKET_MISSING = {"404", "NoSuchBucket", "NotFound"}
"""What a `head_bucket` says when the bucket is not there.

Three spellings because the answer depends on who is answering: botocore raises
the bare HTTP status for `head_bucket` against AWS, while S3-compatible servers
send a named code. Reading only one of them would make a present bucket look
absent against the other implementation.
"""


class ObjectStore:
    """Store and retrieve content-addressed template assets."""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        """Configure the object store client for a single bucket."""
        self._endpoint_url = endpoint_url
        self._bucket = bucket
        self._session = aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    @staticmethod
    def content_key(tenant: str, version_id: str, sha256: str) -> str:
        """Return the content-addressed object key."""
        return f"{tenant}/{version_id}/{sha256}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store a blob under the given key."""
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            await s3.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

    async def get(self, key: str) -> bytes:
        """Retrieve the blob stored under the given key."""
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            body: bytes = await response["Body"].read()
            return body

    async def ensure_bucket(self) -> None:
        """Make sure the configured bucket is there, creating it only if it is not.

        Called once at application startup (`app.py`'s `lifespan`) so a first
        `put`/`get` never fails only because the bucket was never provisioned.

        **Look before creating.** The obvious shape -- call `create_bucket` and
        swallow "already exists" -- reads as idempotent and is not: it needs the
        right to create a bucket on every single start, including the overwhelming
        majority of starts where the bucket has been there for months. A deployment
        that provisions its buckets from infrastructure code hands this service a
        credential scoped to the one bucket it owns, and that credential may not
        create anything. RustFS answers such a call with
        `UnauthorizedAccess: Your account is not signed up`, which is neither
        "already exists" nor recognisably about permissions.

        Measured on 2026-09-04: that is exactly what happened on the first sharp
        deploy. The bucket stood, provisioned by the Ansible role one step earlier
        in the same run, and the service crash-looped past it.

        So: `head_bucket` first. Present is the normal case and costs one call.
        Only a bucket that is genuinely absent leads to `create_bucket`, which is
        the developer's case -- an empty MinIO and an admin credential -- and there
        "already exists" is still swallowed, because two workers may start at once.
        """
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                # Anything other than "not there" is a real problem -- a wrong
                # endpoint, a rejected credential, a bucket owned by someone else.
                # Creating on top of that would replace a precise error with a
                # confusing one.
                if code not in _BUCKET_MISSING:
                    raise
            else:
                return

            try:
                await s3.create_bucket(Bucket=self._bucket)
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code not in _BUCKET_ALREADY_PROVISIONED:
                    raise

    async def ping(self) -> None:
        """Raise if the configured bucket is not reachable."""
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            await s3.head_bucket(Bucket=self._bucket)
