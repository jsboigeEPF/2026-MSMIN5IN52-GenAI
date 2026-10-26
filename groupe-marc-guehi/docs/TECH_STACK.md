# Tech stack

Légende : **[V]** fait vérifié (source, vérifié le 2026-10-20) · **[H]** hypothèse à tester · **[R]** recommandation.
Sources : pricing OpenAI <https://developers.openai.com/api/docs/pricing> ; modèle realtime <https://developers.openai.com/api/docs/models/gpt-realtime> ; guide WebSocket <https://developers.openai.com/api/docs/guides/realtime-websocket> ; Silero <https://github.com/snakers4/silero-vad> ; comparatifs blogs cités par brique (qualité faible : à re-mesurer soi-même).

## Choix retenus (source de vérité ; remplacent les sections « historique » ci-dessous)
Les sections ci-dessous marquées **Historique** sont les recommandations du cadrage du 2026-10-20, avant l'accès à OpenRouter et au Whisper de l'école ; elles sont conservées pour leurs faits vérifiés [V] (tarifs, licences) mais ne décrivent plus le prototype. Les valeurs de réglage (seuils, durées) sont dans `configs/`, pas ici.

Contexte : l'étudiant a accès à OpenRouter (liste de modèles fournie) et à un serveur Whisper large-v3-turbo de l'école (API compatible OpenAI). Faits donnés par l'étudiant, non revérifiés par moi : disponibilité des modèles, identifiants exacts, présence d'endpoints audio sur OpenRouter.

| Brique | Choix | Alternative | Statut |
|---|---|---|---|
| Client | Navigateur : AudioWorklet (PCM16 16 kHz) + WebSocket, `echoCancellation` activé | — | décidé (interface attendue) |
| Backend | FastAPI + WebSocket, asyncio | — | décidé |
| VAD | Silero (ONNX) côté serveur | — | décidé |
| STT | Whisper large-v3-turbo de l'école (par segment, STT spéculatif) ; secours `qwen/qwen3-asr-1.7b` (OpenRouter) | Qwen3 ASR 0,6B, Voxtral Small STT, Muse Voice Transcribe | **décidé** (DECISIONS 3, 13c, 13d) ; latences mesurées ci-dessous |
| LLM | `deepseek/deepseek-v4.1-flash` (OpenRouter, streaming, réflexion coupée) ; repli `qwen/qwen3.8-27b` avant le 1er jeton ; outil `web_search` | Qwen3.8 Flash (429 permanent), GLM 5.3 Flash (raisonnement obligatoire) | **décidé** (DECISIONS 3, 8, 13b) |
| TTS | `minimax/speech-2.8-turbo`, voix `French_Male_Speech_New` ou `French_Female_Speech_New` (tirage au hasard par conversation) ; secours `qwen/qwen-audio-3.0-tts-flash` (accent jugé trop marqué pour un usage principal) | Kokoro local écarté (DECISIONS 13) | **décidé** (DECISIONS 7, 15b) ; mp3 uniquement en streaming → décodage PCM |
| Local | whisper.cpp, Qwen3 via mlx-lm, Kokoro | — | **écarté** (consigne du 2026-10-22 : APIs uniquement, confirmée le 2026-10-24 ; DECISIONS 13) |

**Ajouts du 2026-10-22** : STT de secours = `qwen/qwen3-asr-1.7b` (OpenRouter) ; recherche web = plugin `web` d'OpenRouter sur le LLM principal (≈ 2–3 s, ≈ 0,007 $ par recherche [V, 2026-10-22]). Détails et alternatives écartées : DECISIONS (13). Anglais et détection de langue essayés puis retirés : faits dans DECISIONS (14).

**Résultats de la démo (2026-10-20, script exploratoire retiré du dépôt le 2026-10-24, mesures sur 1 à 3 essais, depuis le Mac)** — faits mesurés, échantillons petits :
- LLM : `qwen/qwen3.8-27b` (principal, TTFT ≈ 380–650 ms, ≈ 77 tok/s, réflexion désactivable) ; alt. `qwen/qwen3.5-35b-a3b` (TTFT ≈ 300–630 ms). `qwen/qwen3.8-flash` : 429 en amont (limite de débit Alibaba), erreur renvoyée *dans le flux* avec HTTP 200 → à détecter. `z-ai/glm-5.3-flash` : raisonnement obligatoire (TTFT ≈ 1,9 s) → écarté. Avec réflexion activée, Qwen3.8 27B passe de 0,4 à 1,1 s de TTFT : réflexion coupée (`"reasoning": {"enabled": false}`).
- TTS : `qwen/qwen-audio-3.0-tts-flash` via `POST /audio/speech` (`voice` obligatoire ; voix : `longanhuan_v3.6`, `loongjohn`), `response_format=pcm` → PCM16 mono 24 kHz en streaming ; TTFB ≈ 0,6–1,2 s (une valeur à 2,7 s). Voix aux noms chinois : accent/qualité en français à juger à l'oreille. ElevenLabs et Kokoro refusés (404 : politique de données/guardrails du compte OpenRouter, réglable dans les paramètres).
- STT : Whisper école (`/audio/transcriptions`) ≈ 0,6–1,0 s en régime établi mais **35 s au 1er appel (démarrage à froid)** ; OpenRouter `qwen/qwen3-asr-1.7b` ≈ 0,6–1,0 s mais un pic à 33 s observé ; `qwen3-asr-0.6b` ≈ 0,6–0,7 s ; Voxtral ≈ 1,3–1,5 s ; Muse ≈ 2–3 s. WER 0 % sur une phrase propre pour tous sauf Muse (6 %). Variance forte → préchauffer au démarrage et mesurer p95.
Autres TTS listés par OpenRouter (non retenus ; plusieurs bloqués par le guardrail de l'école, voir DECISIONS 6) : `elevenlabs/eleven-flash-v2.5`, `hexgrad/kokoro-82m`, `deepgram/aura-2`, `google/gemini-3.8-flash-tts`.

Écartés car hors besoin : FLUX Video Edit, Qwen Image 3, H3/H3 Max, embeddings/reranker.
Risque levé (DECISIONS 6-7) : OpenRouter expose bien des endpoints TTS exploitables ; le plan B Kokoro local est écarté.

## Architecture : cascade vs speech-to-speech
| | Cascade STT→LLM→TTS (imposée) | Speech-to-speech (API Realtime) |
|---|---|---|
| Mesure par étape | Native (chaque brique est instrumentée) | Impossible en interne (boîte noire) ; seulement bout en bout |
| Latence | Plus élevée (3 hops), compensée par streaming/chevauchement | Plus faible [H] |
| Coût | Faible : briques mini à quelques centimes/h [H, voir CONSTRAINTS] | Audio à 32 $/1M in, 64 $/1M out (gpt-realtime-2.1) ; mini 10/20 $ **[V]** |
| Contrôle (barge-in, purge, troncature) | Total | Partiel (événements du serveur) |
| Local/hybride cloud-local | Possible brique par brique | Non |

**[R]** Cascade seule, conformément à l'exigence. Hybride : l'API Realtime n'est pas nécessaire. Option **non retenue pour le MVP** : l'utiliser comme *baseline* de comparaison bout en bout (une seule mesure externe, coût marginal) ; à trancher dans OPEN_QUESTIONS. Chaque brique est derrière une interface `Protocol` pour permettre cloud/local.

## Transport audio / client — Historique (remplacé par DECISIONS 2026-10-20 (2) : navigateur + FastAPI)
- **[R] Client Python local** (sounddevice, micro/HP du Mac) + orchestrateur asyncio dans le même processus. Latence minimale, pas de pile navigateur, écho géré par casque ou AEC système.
- Alt. navigateur (WebSocket + AudioWorklet) : plus réaliste pour une démo, ajoute AEC navigateur gratuit (`echoCancellation`) mais complexité JS et un hop réseau local.
- WebRTC (aiortc) : utile seulement si le backend est distant ; surdimensionné ici. **[V]** OpenAI recommande WebRTC pour clients navigateur/mobile pour l'API Realtime, ce qui n'est pas notre architecture.
- Plus tard : navigateur ou non (à décider après MVP console).

## Backend — Historique (remplacé : FastAPI + WebSocket, voir DECISIONS 2026-10-20 (2))
- **[R]** Python asyncio pur (tâches par brique, `asyncio.Queue`), pas de framework.
- Alt. FastAPI + WebSockets si client navigateur. aiortc si WebRTC.

## VAD — retenu : Silero (DECISIONS 2026-10-20) ; faits vérifiés conservés
- **[R]** Silero VAD en local, ONNX CPU. **[V]** MIT, <1 ms par chunk 30+ ms sur 1 thread CPU, 8 et 16 kHz. Taille exacte de chunk et version : à vérifier à l'install (512 échantillons à 16 kHz = hypothèse H).
- Alt. WebRTC VAD (py-webrtcvad) : plus léger, moins robuste au bruit.
- À choisir plus tard : seuils, hangover, durée min de parole pour déclencher un barge-in.

## STT — Historique (remplacé par DECISIONS 3, 13c, 13d)
| Option | Notes |
|---|---|
| **[R] Cloud : gpt-4o-mini-transcribe** | **[V]** ≈ 0,003 $/min (0,006 pour gpt-4o-transcribe et Whisper ; gpt-realtime-whisper / gpt-live-transcribe 0,017 $/min). Le mode streaming vrai (sessions de transcription Realtime) est à vérifier. |
| Alt. cloud : Deepgram Nova-3 / Flux | Blogs : ≈0,005–0,008 $/min streaming [source faible, à vérifier sur deepgram.com/pricing]. Faible latence, endpointing intégré. |
| **[R] Local : whisper.cpp (Metal/Core ML)** | Blogs : large-v3 ≈10× temps réel sur M5 Pro [source faible]. Modèles small/base/turbo suffisent pour la latence. |
| Alt. local : mlx-whisper, Parakeet (parakeet-mlx / FluidAudio Core ML) | Parakeet plus rapide mais anglais seul [source faible]. faster-whisper : peu avantageux sur Apple Silicon (pas de GPU Metal) [H]. |

Compromis : Whisper traite des segments (pas de vrai streaming) → latence ≈ durée d'inférence après fin de parole ; à mesurer.

## LLM — Historique (remplacé par DECISIONS 3, 8)
- **[R] Cloud streaming : gpt-5-mini ou gpt-5-nano** **[V]** 0,25/2,00 $ et 0,05/0,40 $ par 1M tokens (in/out). Coût négligeable ; mesurer le TTFT.
- Alt. cloud : un modèle Claude Haiku (API Anthropic) si clé disponible.
- **Local** : mlx-lm ou Ollama, Qwen3 ~4B quantifié 4 bits [H : ~100 tok/s, TTFT ~0,3 s ; seules estimations de blogs trouvées, aucune mesure M5 fiable]. Tient largement dans 24 Go.
- Choix du modèle exact : plus tard, sur critère latence/qualité en français.

## TTS — Historique (remplacé par DECISIONS 6, 7, 15b)
- **[R] Cloud : gpt-4o-mini-tts** **[V]** 0,60 $/1M tokens texte in, 12 $/1M tokens audio out. Streaming PCM à vérifier.
- Alt. cloud : ElevenLabs Flash v2.5 (~0,05 $/1k car. ; ~75 ms annoncé constructeur), Cartesia Sonic (facturation en crédits, sources contradictoires), Deepgram Aura-2 (0,03 $/1k car.). Latences = chiffres constructeur, une mesure indépendante donne 188 ms médian pour Cartesia.
- **[R] Local : Kokoro-82M** (mlx-audio, Apache-2.0, léger). Piper : **[V]** archivé en lecture seule le 2025-10-06 (blog), fonctionne mais plus maintenu. XTTS : MPS cassé, éviter. Qualité du français local : **à tester** [H : correcte mais inférieure au cloud].

## Routage cloud/local (extension) — local écarté (DECISIONS 13) ; routage entre APIs : ROADMAP étape 11+
Politique simple par brique (ex. repli local si timeout cloud > X ms ou erreur réseau), configurée dans `configs/`. Le mode dégradé doit être tracé (champ `route` dans les traces). Choix de la politique : après les mesures de base.

## Mesure et analyse
Traces JSONL (schéma dans @docs/METRICS.md), pandas, matplotlib, Jupyter. Rien d'autre.

## Tests
pytest ; bruit : génération numpy avec SNR fixé (bruit blanc/rose + bruit enregistré type café, libre de droits) ; dialogues scriptés (TTS pré-généré, mis en cache dans `data/`) rejoués dans l'entrée. WER : `jiwer`.

## À choisir plus tard — voir OPEN_QUESTIONS (client, langue et modèles locaux tranchés)
Voir @docs/OPEN_QUESTIONS.md (client console vs navigateur, fournisseurs STT/TTS finaux, langue, modèles locaux).
