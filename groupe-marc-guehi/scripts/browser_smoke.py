"""Test de bout en bout dans un vrai Chrome (micro simulé par un fichier WAV) : clique sur Démarrer, attend une réponse.

Prérequis : serveur lancé (uvicorn src.transport.app:app --port 8765) et Google Chrome installé.
Usage : python scripts/browser_smoke.py [data/audio/question1.wav] [--url http://localhost:8765] [--screenshot data/out/ui.png]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", nargs="?", default="data/audio/question1.wav")
    ap.add_argument("--url", default="http://localhost:8765")
    ap.add_argument("--screenshot", default="data/out/ui.png")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--min-turns", type=int, default=1, help="nombre de réponses complètes ou interrompues à attendre")
    ap.add_argument("--text", help="teste le mode texte (utiliser avec un WAV de silence)")
    a = ap.parse_args()
    wav = str(Path(a.wav).resolve())
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=[
            "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}%noloop", "--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport={"width": 900, "height": 760}, permissions=["microphone"])
        page = ctx.new_page()
        logs: list[str] = []
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.goto(a.url)
        page.click("#toggle")
        if a.text:
            page.wait_for_function("document.body.dataset.state === 'listening'")
            page.click("#viewText")  # le champ texte n'existe que dans la vue Écrit
            page.fill("#text", a.text)
            page.click("#send")
        try:
            page.wait_for_function(f"window.__stats.metrics.length + window.__stats.interrupts >= {a.min_turns} && window.__stats.transcripts.length >= {a.min_turns}", timeout=a.timeout * 1000)
            page.wait_for_timeout(500)
        except Exception as e:  # noqa: BLE001
            print("TIMEOUT :", type(e).__name__)
        stats = page.evaluate("window.__stats")
        stats["vue"] = page.evaluate("document.body.dataset.view")
        stats["sous_titres"] = page.evaluate("document.getElementById('subs').textContent")
        Path(a.screenshot).parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=a.screenshot)
        browser.close()
    print(json.dumps({k: stats[k] for k in ("framesSent", "audioBytes", "chunksPlayed", "transcripts", "errors", "interrupts", "truncates")}, ensure_ascii=False, indent=1))
    print("vue :", stats["vue"], "| sous-titres :", stats["sous_titres"])
    print("états :", " > ".join(dict.fromkeys(stats["states"])))
    for m in stats["metrics"]:
        print("métriques :", {k: (round(v) if isinstance(v, float) else v) for k, v in m.items() if k != "type"})
    print("console :", logs[:8])


if __name__ == "__main__":
    main()
