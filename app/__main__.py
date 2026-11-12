import asyncio
import sys

from app.runtime import serve
from app.settings import load_settings, load_studio


def main() -> int:
    try:
        load_settings()
        studio = load_studio()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    asyncio.run(serve(studio.runtime.heartbeat_interval_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
