"""Native inference cannot be force-cancelled; serialize it and discard stale results."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial


class SerialWorker:
    def __init__(self, name: str):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)

    async def run(self, function, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, partial(function, *args))

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
