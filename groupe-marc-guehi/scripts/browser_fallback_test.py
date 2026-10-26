"""Test navigateur de la bascule automatique vers le mode texte (panne du STT).

Prérequis : serveur lancé AVEC l'injection de panne : VOICE_FAIL_STT=1 uvicorn src.transport.app:app --port 8765
(le STT principal et le secours échouent ; le LLM et le TTS sont réels, sauf avec VOICE_DRY_RUN=1) et Google Chrome.
Déroulé : 2 énoncés au micro simulé -> 2 échecs -> bandeau + champ texte actif -> saisie clavier -> réponse -> « Réessayer la voix ».
Usage : python scripts/browser_fallback_test.py [--url http://localhost:8765] [--screenshot data/out/fallback.png]
"""
from __future__ import annotations

import argparse
import json
import tempfile
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright


def two_utterances(a: str, b: str, out: Path, gap_s: float = 2.0) -> None:
    """Concatène deux WAV de même format avec un silence entre les deux (le micro simulé ne lit le fichier qu'une fois)."""
    frames, params = [], None
    for path in (a, b):
        with wave.open(path) as w:
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
    gap = b"\x00\x00" * int(params.framerate * gap_s)
    with wave.open(str(out), "wb") as w:
        w.setparams(params)
        w.writeframes(frames[0] + gap + frames[1] + gap)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8765")
    ap.add_argument("--screenshot", default="data/out/fallback.png")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--text", default="Bonjour, peux-tu me dire une phrase très courte ?")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as d, sync_playwright() as p:
        wav = Path(d) / "two.wav"
        two_utterances("data/audio/question1.wav", "data/audio/question2.wav", wav)
        browser = p.chromium.launch(channel="chrome", headless=True, args=[
            "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}%noloop", "--autoplay-policy=no-user-gesture-required"])
        page = browser.new_context(viewport={"width": 900, "height": 760}, permissions=["microphone"]).new_page()
        logs: list[str] = []
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.goto(a.url)
        page.click("#toggle")
        result: dict = {}
        page.wait_for_function('document.body.dataset.fallback === "1"', timeout=a.timeout * 1000)
        result["vue_ecrit_auto"] = page.evaluate("document.body.dataset.view") == "text"
        result["bandeau_visible"] = page.is_visible("#banner")
        result["champ_texte_actif"] = page.is_enabled("#text")
        frames_at_switch = page.evaluate("window.__stats.framesSent")
        page.wait_for_timeout(1500)
        result["micro_coupe"] = page.evaluate("window.__stats.framesSent") - frames_at_switch <= 5
        page.fill("#text", a.text)
        page.click("#send")
        page.wait_for_function("window.__stats.metrics.length >= 1", timeout=a.timeout * 1000)
        result["reponse_a_l_ecrit"] = page.evaluate("window.__stats.transcripts.length") >= 1
        result["audio_recu"] = page.evaluate("window.__stats.audioBytes") > 0
        page.screenshot(path=a.screenshot)
        page.click("#retryVoice")
        page.wait_for_function('document.body.dataset.fallback === "0"', timeout=5000)
        result["voix_reessayee"] = not page.is_visible("#banner")
        stats = page.evaluate("window.__stats")
        browser.close()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print("erreurs affichées :", stats["errors"])
    print("console :", logs[:8])
    raise SystemExit(0 if all(result.values()) else 1)


if __name__ == "__main__":
    main()
