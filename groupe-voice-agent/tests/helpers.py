import time


class MemoryEvents:
    def __init__(self):
        self.items = []

    def emit(self, event, turn=0, *, t_ns=None, **data):
        self.items.append(
            {
                "event": event,
                "turn": turn,
                "t_ns": t_ns or time.perf_counter_ns(),
                "session": "test",
                "mode": "simulated",
                "route": "local",
                "data": data,
            }
        )

    def named(self, name):
        return [item for item in self.items if item["event"] == name]
