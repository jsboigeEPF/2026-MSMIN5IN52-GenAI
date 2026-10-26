"""Texte réellement entendu par l'utilisateur quand il interrompt l'agent (troncature du contexte du LLM)."""
from __future__ import annotations

DEFAULT_SEC_PER_CHAR = 0.06


def heard_text(texts: list[str], durs: list[float], played_s: float, complete: bool) -> tuple[str, bool]:
    """texts : morceaux dans l'ordre de lecture (accroche comprise) ; durs : secondes d'audio envoyées par morceau ;
    played_s : durée jouée depuis le début de la réponse ; complete : toute la réponse a été générée et envoyée.
    Retourne (texte entendu, tronqué). Le morceau en cours de lecture est coupé proportionnellement, à une frontière de mot."""
    chars = sum(len(t) for t, d in zip(texts, durs, strict=False) if d > 0)
    spc = sum(durs) / chars if chars else DEFAULT_SEC_PER_CHAR
    last_audio = max((i for i, d in enumerate(durs) if d > 0), default=-1)
    out: list[str] = []
    start, truncated = 0.0, not complete
    for i, (text, dur) in enumerate(zip(texts, durs, strict=False)):
        if dur <= 0 or start >= played_s:
            truncated = True
            break
        # le dernier morceau qui a reçu de l'audio est peut-être incomplet si la réponse n'est pas finie
        est = max(dur, len(text) * spc) if (not complete and i == last_audio) else dur
        frac = (played_s - start) / est
        if frac >= 0.98:
            out.append(text)
            start += dur
            continue
        cut = text[:int(len(text) * frac)]
        cut = cut.rsplit(" ", 1)[0] if " " in cut else ""
        if cut.strip():
            out.append(cut.strip() + "…")
        truncated = True
        break
    return " ".join(out).strip(), truncated


def history_content(heard: str, truncated: bool) -> str:
    return heard + (" [interrompu par l'utilisateur]" if truncated and heard else "")
