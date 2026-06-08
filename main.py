"""Top-level entrypoint so the bot can be started with `python main.py`."""

import asyncio

from app.main import main


if __name__ == "__main__":
    asyncio.run(main())
