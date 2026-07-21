import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
async def engine(postgres_url):
    engine = create_async_engine(postgres_url, future=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    connection = await engine.connect()
    transaction = await connection.begin()
    maker = async_sessionmaker(bind=connection, expire_on_commit=False)
    async with maker() as session:
        yield session
    await transaction.rollback()
    await connection.close()
