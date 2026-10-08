import argparse
import asyncio
import sys
from collections.abc import Sequence

from commerce_support.config import Settings
from commerce_support.database.engine import Database
from commerce_support.database.models import Base
from commerce_support.database.seed import seed_demo_data


async def initialize_database(database: Database) -> None:
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await seed_demo_data(database.sessions)


async def _initialize_from_settings() -> None:
    database = Database(Settings())
    try:
        await initialize_database(database)
    finally:
        await database.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m commerce_support.database.cli")
    parser.add_argument("command", choices=("init",))
    parser.parse_args(argv)

    try:
        asyncio.run(_initialize_from_settings())
    except Exception as error:  # noqa: BLE001 - Keep driver details and URLs out of CLI output.
        print(
            f"Database initialization failed ({type(error).__name__}).",
            file=sys.stderr,
        )
        return 1

    print("Database schema and demo data initialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
