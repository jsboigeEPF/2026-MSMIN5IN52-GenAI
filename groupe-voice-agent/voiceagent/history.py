"""Retain only complete speech units acknowledged by the output device."""

from collections import deque


class History:
    def __init__(self, system_prompt: str, max_turns: int):
        self.system_prompt = system_prompt
        self._turns: deque[tuple[str, str]] = deque(maxlen=max_turns)

    def messages(self, user: str) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self.system_prompt}]
        for question, answer in self._turns:
            messages.append({"role": "user", "content": question})
            if answer:
                messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content": user})
        return messages

    def commit(self, user: str, heard: list[str], interrupted: bool = False) -> str:
        answer = " ".join(heard).strip()
        if interrupted:
            answer = f"{answer} [interrompu]".strip()
        self._turns.append((user, answer))
        return answer

    def clear(self) -> None:
        self._turns.clear()
