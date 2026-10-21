"""Campagne de mesures : stimuli bruités reproductibles -> STT (WER), VAD, latence temps réel, interruptions.

Usage :
  python scripts/run_campaign.py all --dry-run            # plan, nombre d'appels, durée et coût estimés ; aucun appel
  python scripts/run_campaign.py all --quick              # version courte (≈ 10 min) pour valider la chaîne
  python scripts/run_campaign.py all --out data/campaign/run1
  python scripts/run_campaign.py {prepare,stt,vad,latency,barge} --out data/campaign/run1 [--force]
Sorties dans --out : audio/, stt.jsonl, vad.jsonl, vad_noise.jsonl, latency.jsonl, barge.jsonl, barge_fp.jsonl, meta.json.
L'analyse (p50/p95, WER, figures) est dans notebooks/analysis.ipynb.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from datetime import datetime
from itertools import groupby
from pathlib import Path

import httpx
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.audio.noise import mix, noise_signal, scale_to_snr, speech_rms, to_f
from src.audio.pcm import SAMPLE_RATE_OUT, read_wav, resample, wav_bytes
from src.config import load_settings
from src.factory import make_client
from src.metrics.analysis import append_jsonl, wer
from src.replay.client import ReplayClient
from src.replay.mic_sim import MicSim
from src.replay.trials import barge_trial, false_positive_trial, latency_turn
from src.stt.school_whisper import SchoolWhisper
from src.tts.fillers import trim_silence
from src.tts.minimax import MiniMaxTTS
from src.vad.endpointer import VadConfig
from src.vad.scan import clip_probs, scan
from src.vad.silero import SileroVAD

PORT = 8801
LEAD_S, TAIL_S = 0.8, 2.0
PER_TURN_OVERHEAD_S = 14.0  # réponse + attente avant le tour suivant (estimation pour le plan)
COST_PER_TURN_USD = 0.012   # mesuré : ≈ 0,01 $ par tour réel complet


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_cfg(quick: bool) -> dict:
    cfg = yaml.safe_load((ROOT / "configs" / "campaign.yaml").read_text(encoding="utf-8"))
    if quick:
        cfg["utterances"] = [u for u in cfg["utterances"] if u["id"] in ("u02", "u04", "u07")]
        cfg["noise"]["snrs_db"] = [10, 0]
        cfg["stt"]["reps"] = 1
        cfg["vad"]["noise_only_s"] = 15
        cfg["barge"]["offsets_s"] = [1.0, 3.0]
        cfg["barge"]["utterances"] = cfg["barge"]["utterances"][:1]
        cfg["barge"]["conditions"] = [{"noise": "none", "snr": None, "reps": 1}, {"noise": "babble", "snr": 20, "reps": 1}]
        cfg["barge"]["false_positive_trials"] = [{"noise": "babble", "snr": 10}, {"noise": "babble", "snr": 20}]
    return cfg


def conditions(cfg: dict) -> list[tuple[str, float | None]]:
    return [("none", None)] + [(n, s) for n in cfg["noise"]["types"] for s in cfg["noise"]["snrs_db"]]


def rng_for(cfg: dict, *ints: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([cfg["seed"], *ints]))


# ---------- stimuli ----------
def load_stimuli(out: Path) -> dict:
    manifest = json.loads((out / "audio" / "manifest.json").read_text(encoding="utf-8"))
    audio = {}
    for item in manifest:
        pcm, rate = read_wav(str(out / "audio" / f"{item['id']}.wav"))
        assert rate == 16000
        audio[item["id"]] = to_f(pcm)
    return {"manifest": {m["id"]: m for m in manifest}, "audio": audio}


def ref_rms_global(cfg: dict, st: dict) -> float:
    return float(np.mean([speech_rms(st["audio"][u["id"]]) for u in cfg["utterances"]]))


def babble_clips(cfg: dict, st: dict) -> list[np.ndarray]:
    return [st["audio"][f"babble{i}"] for i in range(len(cfg["babble_texts"]))]


def stimulus(cfg: dict, st: dict, utt_id: str, noise: str, snr: float | None, rng, lead_s: float, tail_s: float) -> np.ndarray:
    x = np.concatenate([np.zeros(int(lead_s * 16000)), st["audio"][utt_id], np.zeros(int(tail_s * 16000))])
    if noise == "none":
        return x
    n = noise_signal(noise, len(x), rng, babble_clips(cfg, st))
    return mix(x, n, speech_rms(st["audio"][utt_id]), snr)


def make_mic(cfg: dict, st: dict, noise: str, snr: float | None, rng) -> MicSim:
    if noise == "none":
        return MicSim()
    sig = noise_signal(noise, 16000 * 90, rng, babble_clips(cfg, st))
    return MicSim(sig, ref_rms_global(cfg, st), snr, rng)


# ---------- plan ----------
def plan(cfg: dict, quick: bool) -> None:
    conds, uts = conditions(cfg), cfg["utterances"]
    n_prep = len(uts) + len(cfg["babble_texts"]) + 1 + len(cfg["barge"]["utterances"])
    n_stt = len(uts) * len(conds) * cfg["stt"]["reps"]
    lat_turns = len(cfg["latency"]["conditions"]) * len(uts)
    lat_s = lat_turns * (PER_TURN_OVERHEAD_S + 5)  # 5 s ≈ durée moyenne d'un énoncé
    b = cfg["barge"]
    n_barge = sum(c["reps"] for c in b["conditions"]) * len(b["offsets_s"]) * len(b["utterances"])
    n_fp = len(b["false_positive_trials"])
    barge_s = n_barge * 30 + n_fp * 25
    stt_s = n_stt * 0.8
    cost = (lat_turns + n_barge * 2 + n_fp) * COST_PER_TURN_USD + n_prep * 0.004
    print(f"Plan {'(rapide) ' if quick else ''}— conditions de bruit : {len(conds)} (propre + {len(conds) - 1})")
    print(f"  prepare : {n_prep} appels TTS (stimuli, mis en cache)")
    print(f"  stt     : {n_stt} appels Whisper de l'école (gratuit) ≈ {stt_s / 60:.1f} min")
    print(f"  vad     : {len(uts) * len(conds)} énoncés + {len(cfg['noise']['types']) * len(cfg['noise']['snrs_db'])} bruits seuls, "
          f"hors ligne (aucun appel), ≈ 1 min")
    print(f"  latency : {lat_turns} tours réels en temps réel ≈ {lat_s / 60:.0f} min")
    print(f"  barge   : {n_barge} essais d'interruption + {n_fp} essais de faux positifs ≈ {barge_s / 60:.0f} min")
    print(f"  total   : ≈ {(stt_s + lat_s + barge_s) / 60 + 3:.0f} min, coût estimé ≈ {cost:.2f} $ (mesuré ≈ 0,01 $/tour)")


# ---------- phases ----------
async def phase_prepare(cfg: dict, out: Path, force: bool) -> None:
    (out / "audio").mkdir(parents=True, exist_ok=True)
    if (out / "audio" / "manifest.json").exists() and not force:
        log("prepare : stimuli déjà présents")
        return
    s = load_settings()
    items = ([{"id": u["id"], "text": u["text"], "role": "utterance"} for u in cfg["utterances"]]
             + [{"id": f"babble{i}", "text": t, "role": "babble"} for i, t in enumerate(cfg["babble_texts"])]
             + [{"id": cfg["barge"]["prompt"]["id"], "text": cfg["barge"]["prompt"]["text"], "role": "barge_prompt"}]
             + [{"id": u["id"], "text": u["text"], "role": "barge_utterance"} for u in cfg["barge"]["utterances"]])
    sem = asyncio.Semaphore(3)
    async with make_client() as client:
        async def one(i: int, item: dict) -> dict:
            voice = cfg["user_voices"][i % len(cfg["user_voices"])]
            tts = MiniMaxTTS(client, s.tts_base_url, s.tts_api_key, s.tts_model, voice)  # débit normal (pas de speed)
            async with sem:
                pcm = b"".join([c async for c in tts.stream(item["text"])])
            pcm16 = resample(trim_silence(pcm, SAMPLE_RATE_OUT, keep_ms=30), SAMPLE_RATE_OUT, 16000)
            (out / "audio" / f"{item['id']}.wav").write_bytes(wav_bytes(pcm16, 16000))
            x = to_f(pcm16)
            return {**item, "voice": voice, "dur_s": round(len(x) / 16000, 2), "rms": round(speech_rms(x), 1)}
        manifest = await asyncio.gather(*[one(i, it) for i, it in enumerate(items)])
    (out / "audio" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"prepare : {len(manifest)} stimuli synthétisés")


async def phase_stt(cfg: dict, out: Path, force: bool) -> None:
    path = out / "stt.jsonl"
    if path.exists() and not force:
        return log("stt : déjà fait")
    path.unlink(missing_ok=True)
    s, st = load_settings(), load_stimuli(out)
    async with make_client() as client:
        stt = SchoolWhisper(client, s.stt_base_url, s.stt_api_key, s.stt_model)
        await stt.warmup()
        conds, k = conditions(cfg), 0
        for iu, u in enumerate(cfg["utterances"]):
            for ic, (noise, snr) in enumerate(conds):
                for rep in range(cfg["stt"]["reps"] if noise != "none" else 1):
                    x = stimulus(cfg, st, u["id"], noise, snr, rng_for(cfg, 1, iu, ic, rep), 0.5, 0.5)
                    pcm = np.clip(np.round(x), -32768, 32767).astype(np.int16).tobytes()
                    row = {"utt_id": u["id"], "noise": noise, "snr": snr, "rep": rep, "ref": u["text"],
                           "audio_s": round(len(x) / 16000, 2)}
                    t0 = time.perf_counter()
                    try:
                        hyp = await stt.transcribe(pcm, 16000)
                        row.update(hyp=hyp, wer=wer(u["text"], hyp), stt_ms=(time.perf_counter() - t0) * 1000)
                    except Exception as e:  # noqa: BLE001  une erreur ponctuelle ne doit pas arrêter la campagne
                        row.update(hyp=None, wer=None, error=f"{type(e).__name__}: {e}"[:200])
                    append_jsonl(path, row)
                    k += 1
            log(f"stt : {u['id']} terminé ({k} appels)")


def phase_vad(cfg: dict, out: Path, force: bool) -> None:
    path, path_n = out / "vad.jsonl", out / "vad_noise.jsonl"
    path_s, path_b = out / "vad_sweep.jsonl", out / "barge_sweep.jsonl"
    if path.exists() and not force:
        return log("vad : déjà fait")
    for p in (path, path_n, path_s, path_b):
        p.unlink(missing_ok=True)
    bcfg = yaml.safe_load((ROOT / "configs" / "base.yaml").read_text(encoding="utf-8"))
    vcfg = VadConfig(**bcfg["vad"])
    sc = bcfg["session"]
    st, vad = load_stimuli(out), SileroVAD()
    sweep = cfg["vad"]["sweep_end_silence_ms"]
    for iu, u in enumerate(cfg["utterances"]):
        dur_ms = len(st["audio"][u["id"]]) / 16
        for ic, (noise, snr) in enumerate(conditions(cfg)):
            x = stimulus(cfg, st, u["id"], noise, snr, rng_for(cfg, 2, iu, ic), LEAD_S, TAIL_S)
            probs = clip_probs(vad, x)
            true_start, true_end = LEAD_S * 1000, LEAD_S * 1000 + dur_ms
            events, _ = scan(probs, vcfg)
            starts = [e for e in events if e.kind == "speech_start"]
            ends = [e for e in events if e.kind == "speech_end"]
            append_jsonl(path, {
                "utt_id": u["id"], "noise": noise, "snr": snr, "segments": len(ends),
                "missed": len(ends) == 0, "cut": len(ends) > 1,
                "start_err_ms": (starts[0].t_ms - vcfg.min_speech_ms - true_start) if starts else None,
                "endpoint_delay_ms": (ends[0].t_ms - true_end) if ends else None,
                "first_segment_speech_ms": ends[0].speech_ms if ends else None, "true_speech_ms": round(dur_ms)})
            for es in sweep:   # même signal, autre durée de silence exigée pour la fin de tour
                ev, _ = scan(probs, VadConfig(**{**bcfg["vad"], "end_silence_ms": es}))
                en = [e for e in ev if e.kind == "speech_end"]
                append_jsonl(path_s, {"end_silence_ms": es, "utt_id": u["id"], "noise": noise, "snr": snr,
                                      "missed": len(en) == 0, "cut": len(en) > 1,
                                      "endpoint_delay_ms": (en[0].t_ms - true_end) if en else None})
    ref = ref_rms_global(cfg, st)
    n = int(cfg["vad"]["noise_only_s"] * 16000)
    noise_probs: dict[tuple[str, int, int], list[float]] = {}
    for ik, kind in enumerate(cfg["noise"]["types"]):
        for isr, snr in enumerate(cfg["noise"]["snrs_db"]):
            normal = strict = 0
            for seed in range(2):
                x = scale_to_snr(noise_signal(kind, n, rng_for(cfg, 3, ik, isr, seed), babble_clips(cfg, st)), ref, snr)
                probs = clip_probs(vad, x)
                noise_probs[(kind, snr, seed)] = probs
                ev, _ = scan(probs, vcfg)
                normal += sum(e.kind == "speech_start" for e in ev)
                ev2, _ = scan(probs, vcfg, strict_prob=sc["barge_in_min_prob"])
                strict += sum(e.kind in ("speech_end", "discarded") and e.speech_ms >= sc["barge_in_min_speech_ms"] for e in ev2)
            minutes = 2 * cfg["vad"]["noise_only_s"] / 60
            append_jsonl(path_n, {"noise": kind, "snr": snr, "minutes": minutes, "false_starts": normal,
                                  "false_starts_per_min": normal / minutes, "false_barge_candidates": strict,
                                  "false_barge_per_min": strict / minutes})
    # calibration hors ligne des seuils d'interruption : détection sur les phrases interruptives vs faux déclenchements sur bruit seul
    bs, lead_ms = cfg["vad"]["barge_sweep"], 500
    for ic, cond in enumerate(bs["conditions"]):
        for iu, u in enumerate(cfg["barge"]["utterances"]):
            x = stimulus(cfg, st, u["id"], cond["noise"], cond["snr"], rng_for(cfg, 7, ic, iu), lead_ms / 1000, 1.5)
            probs = clip_probs(vad, x)
            for sp in bs["strict_prob"]:
                for mm in bs["min_ms"]:
                    _, at = scan(probs, vcfg, strict_prob=sp, barge_ms=mm)
                    append_jsonl(path_b, {"kind": "detect", "strict_prob": sp, "min_ms": mm, "noise": cond["noise"], "snr": cond["snr"],
                                          "utt_id": u["id"], "detect_ms": None if at is None else at - lead_ms})
    for cond in bs["noise_only"]:
        ik, isr = cfg["noise"]["types"].index(cond["noise"]), cfg["noise"]["snrs_db"].index(cond["snr"])
        for sp in bs["strict_prob"]:
            for mm in bs["min_ms"]:
                total = 0
                for seed in range(2):
                    ev, _ = scan(noise_probs[(cond["noise"], cond["snr"], seed)], vcfg, strict_prob=sp)
                    total += sum(e.kind in ("speech_end", "discarded") and e.speech_ms >= mm for e in ev)
                append_jsonl(path_b, {"kind": "false", "strict_prob": sp, "min_ms": mm, "noise": cond["noise"], "snr": cond["snr"],
                                      "false_per_min": total / (2 * cfg["vad"]["noise_only_s"] / 60)})
    log("vad : terminé (dont balayages de calibration)")


@contextlib.asynccontextmanager
async def server(out: Path, name: str, filler: bool):
    env = {**os.environ, "VOICE_FILLER": "1" if filler else "0"}
    logf = (out / f"server_{name}.log").open("w")
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "uvicorn", "src.transport.app:app", "--port", str(PORT),
                                                "--log-level", "warning", cwd=ROOT, env=env, stdout=logf, stderr=logf)
    try:
        async with httpx.AsyncClient() as c:
            for _ in range(180):
                try:
                    if (await c.get(f"http://localhost:{PORT}/")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
            else:
                raise RuntimeError("le serveur ne démarre pas (voir server_*.log)")
        log(f"serveur prêt ({name}, accroches {'oui' if filler else 'non'})")
        yield f"ws://localhost:{PORT}/ws"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 10)
        logf.close()
        await asyncio.sleep(1)


async def phase_latency(cfg: dict, out: Path, force: bool) -> None:
    path = out / "latency.jsonl"
    if path.exists() and not force:
        return log("latency : déjà fait")
    path.unlink(missing_ok=True)
    st = load_stimuli(out)
    for filler, group in groupby(cfg["latency"]["conditions"], key=lambda c: c["filler"]):
        async with server(out, f"latency_{'filler' if filler else 'nofiller'}", filler) as url:
            for ci, cond in enumerate(group):
                todo, attempt, turn_no, n_fail = list(cfg["utterances"]), 0, 0, 0
                while todo and attempt <= 2 and n_fail < cfg["latency"]["stop_after_failures"]:  # une session désynchronisée (échec) est relancée avec les énoncés restants
                    mic = make_mic(cfg, st, cond["noise"], cond["snr"], rng_for(cfg, 4, ci, attempt))
                    async with ReplayClient(url, mic) as c:
                        while todo:
                            u = todo.pop(0)
                            turn_no += 1
                            row = await latency_turn(c, mic, u, st["audio"][u["id"]], turn_no)
                            append_jsonl(path, {"condition": cond["name"], "noise": cond["noise"], "snr": cond["snr"],
                                                "filler_enabled": cond["filler"], "attempt": attempt, **row})
                            res = f"ÉCHEC {row.get('error')}" if row["failed"] else f"{(row.get('wall_first_ms') or 0):.0f} ms"
                            log(f"latency {cond['name']} {u['id']} : {res}")
                            if row["failed"]:
                                n_fail += 1
                                break
                    attempt += 1
                for u in todo:  # condition abandonnée : les énoncés non testés sont tracés, pas comptés comme des échecs
                    append_jsonl(path, {"condition": cond["name"], "noise": cond["noise"], "snr": cond["snr"],
                                        "filler_enabled": cond["filler"], "utt_id": u["id"], "skipped": True, "failed": False,
                                        "turn": 0, "error": f"ignoré après {n_fail} échecs"})


async def phase_barge(cfg: dict, out: Path, force: bool) -> None:
    path, path_fp = out / "barge.jsonl", out / "barge_fp.jsonl"
    if path.exists() and not force:
        return log("barge : déjà fait")
    path.unlink(missing_ok=True)
    path_fp.unlink(missing_ok=True)
    st, b = load_stimuli(out), cfg["barge"]
    prompt = st["audio"][b["prompt"]["id"]]
    async with server(out, "barge", True) as url:
        k = 0
        for ic, cond in enumerate(b["conditions"]):
            for rep in range(cond["reps"]):
                for ou, u in enumerate(b["utterances"]):
                    for io, off in enumerate(b["offsets_s"]):
                        mic = make_mic(cfg, st, cond["noise"], cond["snr"], rng_for(cfg, 5, ic, rep, ou, io))
                        refs = [u["text"], b["prompt"]["text"] + " " + u["text"]]
                        row = await barge_trial(url, mic, prompt, u, st["audio"][u["id"]], b["prompt"]["text"], off, refs)
                        row.update(noise=cond["noise"], snr=cond["snr"], rep=rep)
                        append_jsonl(path, row)
                        k += 1
                        log(f"barge #{k} {cond['noise']}/{cond['snr']} off={off}s {u['id']} : "
                            f"{'invalide ' + row.get('invalid_reason', '') if not row['valid'] else 'detect_ms=' + str(row.get('detect_ms') and round(row['detect_ms']))}")
        for i, cond in enumerate(b["false_positive_trials"]):
            mic = make_mic(cfg, st, cond["noise"], cond["snr"], rng_for(cfg, 6, i))
            row = await false_positive_trial(url, mic, prompt)
            row.update(noise=cond["noise"], snr=cond["snr"])
            append_jsonl(path_fp, row)
            log(f"faux positifs {cond['noise']}/{cond['snr']} dB : {row.get('false_interrupts')} interruption(s)")


def cost_total() -> float:
    p = ROOT / "data" / "cost_ledger.jsonl"
    if not p.exists():
        return 0.0
    return sum(json.loads(line).get("cost_usd", 0.0) for line in p.read_text().splitlines() if line.strip())


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["all", "prepare", "stt", "vad", "latency", "barge"])
    ap.add_argument("--out", default="data/campaign/run1")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="refaire les phases dont les résultats existent")
    a = ap.parse_args()
    cfg, out = load_cfg(a.quick), ROOT / a.out
    if a.dry_run:
        return plan(cfg, a.quick)
    out.mkdir(parents=True, exist_ok=True)
    t0, c0 = time.time(), cost_total()
    phases = ["prepare", "stt", "vad", "latency", "barge"] if a.phase == "all" else [a.phase]
    for ph in phases:
        log(f"=== phase {ph}")
        if ph == "prepare":
            await phase_prepare(cfg, out, a.force)
        elif ph == "stt":
            await phase_stt(cfg, out, a.force)
        elif ph == "vad":
            phase_vad(cfg, out, a.force)
        elif ph == "latency":
            await phase_latency(cfg, out, a.force)
        elif ph == "barge":
            await phase_barge(cfg, out, a.force)
    meta_path = out / "meta.json"
    prev = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta = {"started": prev.get("started", datetime.fromtimestamp(t0).isoformat(timespec="seconds")),
            "duration_s": prev.get("duration_s", 0) + round(time.time() - t0),   # cumul des lancements (phases relancées comprises)
            "quick": a.quick, "cost_usd_delta": round(prev.get("cost_usd_delta", 0.0) + cost_total() - c0, 4),
            "phases": sorted(set(prev.get("phases", [])) | set(phases)), "config": cfg,
            "voice_cfg": yaml.safe_load((ROOT / "configs" / "base.yaml").read_text(encoding="utf-8"))}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log(f"terminé en {meta['duration_s']} s, coût ≈ {meta['cost_usd_delta']} $ ; résultats dans {out}")


if __name__ == "__main__":
    asyncio.run(main())
