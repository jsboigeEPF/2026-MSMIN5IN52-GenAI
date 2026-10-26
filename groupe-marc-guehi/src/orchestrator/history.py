"""Historique de la conversation : seul endroit qui écrit les messages de l'agent, troncature au texte entendu comprise."""
from __future__ import annotations

import re

from src.orchestrator.truncation import history_content

SENTENCE_END = re.compile(r"^(.*[.!?…])(?:\s|$)", re.DOTALL)


def cut_at_sentence(text: str) -> str:
    """Réponse coupée par la limite de jetons : ne garde que les phrases entières (tout le texte s'il n'y en a aucune)."""
    text = text.strip()
    m = SENTENCE_END.match(text)
    return m.group(1).strip() if m else text


def append_user(messages: list[dict], text: str) -> None:
    """Ajoute la question de l'utilisateur. Si la précédente est restée sans réponse (interrompue avant d'être entendue),
    les deux sont fusionnées : jamais deux messages `user` d'affilée."""
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] = f"{messages[-1]['content']} {text}".strip()
    else:
        messages.append({"role": "user", "content": text})


def append_assistant(messages: list[dict], answer: str, cut_by_length: bool = False) -> None:
    """Ajoute la réponse de l'agent, sauf si elle est vide (jamais de message `assistant` vide)."""
    answer = cut_at_sentence(answer) if cut_by_length else answer.strip()
    if answer:
        messages.append({"role": "assistant", "content": answer})


class History:
    """Les messages sont des dicts `{"role", "content"}` (passés tels quels à `run_turn`). La référence d'un message
    de l'agent est le dict lui-même : elle reste valable même si l'historique est rogné ou si d'autres tours ont eu lieu."""

    def __init__(self, max_messages: int) -> None:
        self.messages: list[dict] = []
        self.max_messages = max_messages

    def __getitem__(self, i: int) -> dict:
        return self.messages[i]

    def _contains(self, ref: dict) -> bool:
        return any(m is ref for m in self.messages)

    def interrupt(self, complete: bool, heard: str, truncated: bool) -> dict | None:
        """Interruption (estimation du serveur). Tour terminé : le dernier message de l'agent est réécrit au texte entendu (ou
        retiré si rien n'a été entendu) ; tour inachevé : un message est ajouté s'il y a du texte entendu ET que la question a été
        enregistrée (dernier message = utilisateur), sinon l'historique aurait deux messages de l'agent d'affilée. Retourne la référence."""
        last = self.messages[-1] if self.messages else None
        if complete and last is not None and last["role"] == "assistant":
            if heard:
                last["content"] = history_content(heard, truncated)
                return last
            self.messages.pop()
            return None
        if heard and last is not None and last["role"] == "user":
            ref = {"role": "assistant", "content": history_content(heard, truncated)}
            self.messages.append(ref)
            return ref
        return None

    def refine(self, ref: dict | None, heard: str, truncated: bool) -> bool:
        """Retour du client (durée réellement jouée) : réécrit le message visé par `ref`. Idempotent ; sans effet si le message
        a disparu (historique rogné) ou si rien n'a été entendu. Ne vise jamais un autre message que `ref`."""
        if ref is None or not heard or not self._contains(ref):
            return False
        ref["content"] = history_content(heard, truncated)
        return True

    def drop_pending_user(self) -> None:
        """Tour en échec : le message de l'utilisateur resté sans réponse est retiré."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()

    def prefix_last(self, spoken: list[str]) -> None:
        """Les accroches dites avant la réponse font partie du message de l'agent ; sans réponse (vide), elles forment à elles seules
        ce message (elles ont été entendues)."""
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages[-1]["content"] = " ".join(spoken + [self.messages[-1]["content"]])
        elif spoken:
            self.messages.append({"role": "assistant", "content": " ".join(spoken)})

    def trim(self) -> None:
        """Garde les `max_messages` derniers messages (0 : aucune mémoire), en commençant par une question de l'utilisateur."""
        self.messages[:] = self.messages[-self.max_messages:] if self.max_messages > 0 else []
        while self.messages and self.messages[0]["role"] != "user":
            self.messages.pop(0)
