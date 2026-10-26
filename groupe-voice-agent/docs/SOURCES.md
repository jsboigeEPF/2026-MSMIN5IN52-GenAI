# Sources techniques consultées le 29 septembre 2026

- faster-whisper : https://github.com/SYSTRAN/faster-whisper
- Kokoro, pipeline et modèle : https://github.com/hexgrad/kokoro
- API Ollama chat : https://docs.ollama.com/api/chat
- Modèle Ollama : https://ollama.com/library/qwen2.5:3b-instruct-q4_K_M
- Silero VAD : https://github.com/snakers4/silero-vad
- python-sounddevice, streams : https://python-sounddevice.readthedocs.io/en/latest/api/streams.html
- PipeWire echo-cancel : https://docs.pipewire.org/page_module_echo_cancel.html
- Piper API Python : https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md
- Voix française UPMC : https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR/upmc/medium

Les signatures des distributions Kokoro 0.9.4, Piper 1.3.0 et faster-whisper 1.2.1
ont également été inspectées dans leurs wheels. Cette inspection valide le choix
d'interface ; elle ne remplace pas une exécution des modèles sur le matériel cible.
