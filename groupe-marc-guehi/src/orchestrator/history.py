"""Historique de la conversation : seul endroit qui écrit les messages de l'agent, troncature au texte entendu comprise."""
from __future__ import annotations

from src.orchestrator.truncation import history_content


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
        """Les accroches dites avant la réponse font partie du message de l'agent."""
        self.messages[-1]["content"] = " ".join(spoken + [self.messages[-1]["content"]])

    def trim(self) -> None:
        del self.messages[:-self.max_messages]
