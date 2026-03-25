import asyncio
import traceback

from model.app import main


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
