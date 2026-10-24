# Agent vocal temps réel (cascade STT → LLM → TTS)

Projet D1 (5A) : conversation vocale en français dans le navigateur, avec streaming de bout en bout, accès internet (recherche web appelée par le LLM), interruption par la voix (barge-in), mode texte de secours (automatique si le STT tombe) et traces de latence par étape. APIs uniquement, aucun modèle local.

```
navigateur (micro 16 kHz) ─WebSocket→ VAD Silero → STT (Whisper) → LLM (stream) → TTS (stream) ─WebSocket→ navigateur (lecture)
                                  ↑ VAD permanent : barge-in → annule LLM/TTS/lecture
```

| Brique | Choix | Config |
|---|---|---|
| STT | Whisper large-v3-turbo (serveur de l'école) | `.env`, `configs/base.yaml` |
| LLM | `deepseek/deepseek-v4.1-flash` via OpenRouter (repli `qwen/qwen3.8-27b`), outil `web_search` | idem |
| Recherche web | plugin `web` d'OpenRouter (pas de clé en plus) | `search:` dans `configs/base.yaml` |
| TTS | `minimax/speech-2.8-turbo`, voix française tirée au hasard par conversation (`TTS_VOICES`) | idem |
| VAD | Silero (ONNX, local) | idem |

Décisions et justifications : [docs/DECISIONS.md](docs/DECISIONS.md). Architecture : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Installation

Prérequis : Python 3.12, [uv](https://docs.astral.sh/uv/), Google Chrome (pour le test navigateur).

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev,analysis]"
cp .env.example .env   # puis renseigner les clés (jamais commitées)
```

## Utilisation

```bash
# Sans réseau ni clé (faux fournisseurs)
VOICE_DRY_RUN=1 .venv/bin/uvicorn src.transport.app:app --port 8000
# Réel
.venv/bin/uvicorn src.transport.app:app --port 8000
```

Ouvrir <http://localhost:8000>, cliquer sur « Démarrer la conversation », autoriser le micro et parler. Demandez la météo ou l'actualité : l'agent dit « je regarde ça » puis cherche sur internet. **Casque recommandé** (pas d'annulation d'écho côté serveur). Le champ texte en bas est le mode de secours : même chaîne, sans STT. Chaque réponse affiche sa latence perçue et le détail STT / LLM / TTS ; l'en-tête donne la médiane de la conversation.

Un tour sans navigateur : `.venv/bin/python scripts/run_turn.py --text "Bonjour" [--play] [--dry-run]`.

## Tests et mesures

```bash
.venv/bin/python -m pytest -q          # 68 tests, sans réseau
.venv/bin/ruff check src tests
.venv/bin/python scripts/browser_smoke.py   # Chrome, micro simulé (serveur sur le port 8765)
.venv/bin/python scripts/run_campaign.py all --dry-run   # plan, durée, coût
.venv/bin/python scripts/run_campaign.py all --out data/campaign/<run>
```

Analyse : `notebooks/analysis.ipynb` (`CAMPAIGN_RUN=data/campaign/<run>`). Protocole et définitions : [docs/METRICS.md](docs/METRICS.md). Résultats du run1 : [docs/DECISIONS.md](docs/DECISIONS.md) (entrée 11).

## Limites connues

- Mono-utilisateur (un VAD à état partagé).
- Pas d'AEC serveur : sans casque, l'écho peut déclencher de faux barge-in.
- Foule de voix : la fin de tour n'est pas détectée dès 10 dB de SNR ; interruptions correctes 67 % (run1).
- Latence perçue ≈ 2 s (p50 sans accroche) ; l'accroche réduit le temps au premier son (≈ 0,5 s), pas l'attente du contenu.
- Recherche internet : 2–3 s d'attente (couverte par une accroche) ; le résultat brut n'est pas gardé en mémoire de conversation.
- Mesures sur voix et bruits synthétiques, petits échantillons.

## Données et confidentialité

`.env`, `data/` (audio, traces, coûts) sont gitignorés. L'audio du micro est envoyé au serveur STT de l'école et le texte à OpenRouter : ne rien dire de personnel. Coût cumulé du projet : somme des `cost_usd` de `data/cost_ledger.jsonl` (≈ 2,2 $ au 2026-10-22 ; ne pas lire `total_usd`, propre à chaque processus).
