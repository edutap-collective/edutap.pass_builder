"""S3-compatible object store client for RustFS."""

import aioboto3
from botocore.exceptions import ClientError

_BUCKET_ALREADY_PROVISIONED = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}


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
        """Create the configured bucket if it does not already exist.

        Called once at application startup (`app.py`'s `lifespan`) so a
        first `put`/`get` never fails only because the bucket was never
        provisioned. Idempotent: "already owned/exists" is treated as
        success rather than an error.
        """
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            try:
                await s3.create_bucket(Bucket=self._bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code not in _BUCKET_ALREADY_PROVISIONED:
                    raise

    async def ping(self) -> None:
        """Raise if the configured bucket is not reachable."""
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            await s3.head_bucket(Bucket=self._bucket)
