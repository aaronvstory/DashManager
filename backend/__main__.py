"""DashManager entrypoint: `python -m backend`.

The Proactor event-loop policy MUST be installed before uvicorn creates its
loop — Playwright on Windows needs subprocess support, which the default
selector loop lacks. Always start the app through this module, never the
`uvicorn` CLI.
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

from backend.config import PORT


def main() -> None:
    uvicorn.run("backend.main:app", host="127.0.0.1", port=PORT, loop="asyncio")


if __name__ == "__main__":
    main()
