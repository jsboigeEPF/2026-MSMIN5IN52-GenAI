"""CLI entry points keep parsing separate from runtime orchestration."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from voiceagent.config import load_config
from voiceagent.events import EventBus


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Agent vocal français local et instrumenté")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("run", "replay", "demo", "bench"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("config.yaml"))
        command.add_argument(
            "--mock", action="store_true", help="Simulation explicite sans modèles"
        )
        command.add_argument("--session", help="Nom d'un NOUVEAU dossier de session")
        command.add_argument("--voice", help="Voix Kokoro ou chemin Piper selon le moteur")
        command.add_argument("--half-duplex", action="store_true")
        command.add_argument("--no-audio", action="store_true")
        command.add_argument("--route", choices=["local", "cloud"], default="local")
        if name == "run":
            command.add_argument("--text", action="store_true")
        elif name == "replay":
            command.add_argument("corpus", type=Path)
            command.add_argument("--noise", choices=["white", "pink", "real"], default="pink")
            command.add_argument("--noise-file", type=Path)
            command.add_argument("--snr", default="clean", help="clean ou SNR en dB")
            command.add_argument("--limit", type=int)
        elif name == "bench":
            command.add_argument("cases", type=Path, help="CSV file,expected,experiment")
            command.add_argument("--delay", type=float, default=2.0)
    report = commands.add_parser("report")
    report.add_argument("session", type=Path)
    report.add_argument("--no-charts", action="store_true")
    commands.add_parser("devices")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--config", type=Path, default=Path("config.yaml"))
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--config", type=Path, default=Path("config.yaml"))
    prepare.add_argument("--output", type=Path, default=Path("config.lock.yaml"))
    return root


def doctor(config) -> int:
    from voiceagent.runtime import memory_sample, package_versions

    print(json.dumps({"versions": package_versions(), "memory": memory_sample()}, indent=2))
    failures = 0
    try:
        import httpx

        response = httpx.get(config.llm.url + "/api/tags", timeout=5)
        response.raise_for_status()
        models = [item["name"] for item in response.json()["models"]]
        if config.llm.model not in models:
            raise RuntimeError(f"Modèle absent : ollama pull {config.llm.model}")
        print("Ollama : OK")
    except Exception as exc:
        failures += 1
        print(f"Ollama : {exc}")
    try:
        import sounddevice as sd

        print(sd.query_devices())
        sd.check_input_settings(device=config.audio.input_device, channels=1, samplerate=16000)
        sd.check_output_settings(
            device=config.audio.output_device, channels=1, samplerate=config.audio.output_rate
        )
        print("Formats audio : OK (le routage AEC reste à vérifier dans PipeWire)")
    except Exception as exc:
        failures += 1
        print(f"Audio : {exc}")
    return 1 if failures else 0


async def run_command(args, config):
    from voiceagent.live import text_conversation, voice_conversation
    from voiceagent.runtime import open_agent

    mode = "simulated" if args.mock else args.command
    if args.command == "run" and not args.mock:
        mode = "text" if args.text else "live"
    if args.session and Path(args.session).name != args.session:
        raise ValueError("--session doit être un nom simple, sans chemin")
    events = EventBus(Path(config.runs_dir), mode, args.session)
    from voiceagent.console import Console

    events.subscribe(Console())
    print(f"Session : {events.directory}")
    try:
        async with open_agent(
            config,
            events,
            mock=args.mock,
            need_stt=args.command != "run" or not args.text,
            audio=not args.no_audio,
            on_token=lambda token: print(token, end="", flush=True),
        ) as agent:
            if args.command == "run":
                if args.text:
                    await text_conversation(agent)
                elif args.mock:
                    raise ValueError("Pour simuler, utiliser run --mock --text ou demo --mock")
                else:
                    await voice_conversation(agent)
            elif args.command == "replay":
                from voiceagent.evaluation.replay import replay

                snr = None if args.snr == "clean" else float(args.snr)
                await replay(
                    agent, args.corpus, args.noise, snr, args.noise_file, args.mock, args.limit
                )
            elif args.command == "demo":
                from voiceagent.evaluation.demo import demo

                await demo(agent)
            elif args.command == "bench":
                from voiceagent.evaluation.interruptions import benchmark

                await benchmark(agent, args.cases, args.delay, args.mock)
    finally:
        events.close()
        print(f"\nRapport : python -m voiceagent report {events.directory}")


def main(argv=None) -> None:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "report":
            from voiceagent.report import build_report

            build_report(arguments.session, charts=not arguments.no_charts)
            return
        if arguments.command == "devices":
            import sounddevice as sd

            print(sd.query_devices())
            return
        config = load_config(arguments.config)
        if arguments.command == "doctor":
            sys.exit(doctor(config))
        if arguments.command == "prepare":
            from voiceagent.prepare import prepare

            prepare(config, arguments.output)
            return
        if arguments.route == "cloud":
            raise ValueError(
                "Extension P2 non activée : aucune connexion ni dépense cloud. "
                "Voir docs/DECISIONS.md pour les prérequis de la comparaison Realtime."
            )
        if arguments.command == "demo" and not arguments.mock:
            raise ValueError(
                "demo est un test technique : ajouter --mock. Utiliser run en réel."
            )
        if arguments.voice:
            if config.tts.engine == "piper":
                config.tts.piper_model = arguments.voice
            else:
                config.tts.voice = arguments.voice
        config.audio.half_duplex = arguments.half_duplex or config.audio.half_duplex
        asyncio.run(run_command(arguments, config))
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        sys.exit(1)
