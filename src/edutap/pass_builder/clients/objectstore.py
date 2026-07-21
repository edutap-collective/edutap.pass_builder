"""S3-compatible object store client for RustFS."""

import aioboto3


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
