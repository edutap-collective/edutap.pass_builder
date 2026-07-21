from sqlalchemy import text


async def test_session_talks_to_postgres_18(session):
    result = await session.execute(text("SHOW server_version_num"))
    assert int(result.scalar_one()) >= 180000
