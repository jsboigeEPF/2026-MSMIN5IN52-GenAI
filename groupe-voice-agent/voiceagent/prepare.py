"""Resolve mutable model references once; write an immutable local configuration."""

import hashlib
import json
from pathlib import Path


def prepare(config, output: Path) -> None:
    import httpx
    import yaml
    from huggingface_hub import HfApi, snapshot_download

    lock = {"models": {}, "ollama": {}}
    api = HfApi()
    for name, repo, revision in (
        ("stt", config.stt.model, config.stt.revision),
        ("tts", config.tts.repo, config.tts.revision),
    ):
        if name == "tts" and config.tts.engine != "kokoro":
            path = Path(config.tts.piper_model)
            lock["models"]["piper"] = {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            continue
        if Path(repo).is_dir():
            raise ValueError(
                "prepare attend config.yaml avec identifiants Hugging Face distants"
            )
        sha = api.model_info(repo, revision=revision).sha
        patterns = None
        if name == "tts":
            patterns = ["config.json", "kokoro-v1_0.pth", f"voices/{config.tts.voice}.pt"]
        directory = snapshot_download(repo, revision=sha, allow_patterns=patterns)
        lock["models"][name] = {"repo": repo, "revision": sha, "path": directory}
        if name == "stt":
            config.stt.model = directory
            config.stt.revision = sha
        else:
            config.tts.repo = directory
            config.tts.revision = sha
    response = httpx.get(config.llm.url + "/api/tags", timeout=10)
    response.raise_for_status()
    model = next(
        (item for item in response.json()["models"] if item["name"] == config.llm.model), None
    )
    if model is None:
        raise RuntimeError(f"Lancer d'abord ollama pull {config.llm.model}")
    lock["ollama"] = {"name": model["name"], "digest": model["digest"]}
    config.llm.expected_digest = model["digest"]
    output.write_text(yaml.safe_dump(config.as_dict(), allow_unicode=True), encoding="utf-8")
    output.with_suffix(".models.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    print(f"Configuration figée : {output}")
