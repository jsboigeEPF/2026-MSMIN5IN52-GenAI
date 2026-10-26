"""Un tour complet en ligne de commande : texte (ou WAV) -> [STT] -> LLM -> TTS -> fichier WAV.

Usage :
  python scripts/run_turn.py --text "Bonjour, tu m'entends ?" [--play]
  python scripts/run_turn.py --audio-in question.wav
  python scripts/run_turn.py --dry-run          # faux fournisseurs, aucun appel réseau
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audio.pcm import SAMPLE_RATE_OUT, read_wav, wav_bytes
from src.config import build_system_prompt, load_settings
from src.factory import build_providers, make_client
from src.metrics.cost import CostLedger
from src.metrics.recorder import Recorder
from src.metrics.tracer import Tracer
from src.orchestrator.turn import run_turn
from src.tts.voices import pick_voice


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text")
    ap.add_argument("--audio-in")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--out", default="data/out/turn.wav")
    ap.add_argument("--play", action="store_true", help="joue le résultat (winsound sous Windows, afplay sous macOS, aplay sous Linux)")
    a = ap.parse_args()
    if not (a.text or a.audio_in or a.dry_run):
        ap.error("fournir --text, --audio-in ou --dry-run")

    s = load_settings()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + ("_dry" if a.dry_run else "")
    tracer = Tracer(run_id, s.cfg["metrics"]["trace_dir"])
    ledger = None if a.dry_run else CostLedger(budget_usd=s.budget_usd)
    rec = Recorder(tracer, ledger)
    voice = pick_voice(s.tts_voices or ["fake"])
    client = None if a.dry_run else make_client()
    try:
        stt, llm, tts = build_providers(s, voice, client, rec, a.dry_run)
        if not a.no_warmup and not a.dry_run:
            await asyncio.gather(stt.warmup(), llm.warmup(), tts.warmup())
            rec.events.clear()
        audio = read_wav(a.audio_in) if a.audio_in else None
        r = await run_turn(stt=stt, llm=llm, tts=tts, history=[], system_prompt=build_system_prompt(s), rec=rec,
                           user_text=None if audio else (a.text or "Bonjour, ceci est un test."), audio=audio,
                           first_min=8, rest_min=s.cfg["tts"]["min_chunk_chars"])
    finally:
        if client:
            await client.aclose()
        tracer.close()

    pcm = b"".join(c for part in r.audio for c in part)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(wav_bytes(pcm, SAMPLE_RATE_OUT))
    print(f"voix        : {voice}")
    print(f"utilisateur : {r.user_text}")
    print(f"agent       : {r.answer}")
    print(f"STT         : {'—' if r.stt_ms is None else f'{r.stt_ms:.0f} ms'}")
    for ev in ("llm_first_token", "tts_first_byte", "tts_first_pcm"):
        vals = [d.get("ms") for e, _, d in rec.events if e == ev and d.get("ms") is not None]
        if vals:
            print(f"{ev:<12}: {vals[0]:.0f} ms (1er) / {max(vals):.0f} ms (max), n={len(vals)}")
    print(f"1er audio   : {r.first_audio_ms:.0f} ms après le début du tour ; total {r.total_ms:.0f} ms ; {len(r.audio)} morceau(x)")
    print(f"audio       : {out} ({len(pcm) / (2 * SAMPLE_RATE_OUT):.1f} s) ; trace : {s.cfg['metrics']['trace_dir']}/{run_id}.jsonl")
    if ledger:
        print(f"coût session cumulé : {ledger.total_usd:.4f} $ (journal data/cost_ledger.jsonl)")
    if a.play:
        if sys.platform == "win32":
            import winsound
            await asyncio.to_thread(winsound.PlaySound, str(out), winsound.SND_FILENAME)
        else:
            player = "afplay" if sys.platform == "darwin" else "aplay"
            await (await asyncio.create_subprocess_exec(player, str(out))).wait()


if __name__ == "__main__":
    asyncio.run(main())
