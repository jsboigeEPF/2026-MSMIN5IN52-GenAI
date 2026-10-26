# Agent vocal temps réel (cascade STT → LLM → TTS)

## Groupe

Groupe 6 — `marc-guehi`. Membres : @pierre-ingrachen, @MaloD25.

## Sujet

- **Identifiant du catalogue :** D1 — Agent vocal temps réel sous contrainte de latence.
- **Problématique :** assembler STT, LLM et TTS en flux duplex, mesurer chaque étape (p50/p95), gérer l'interruption par l'utilisateur (barge-in) et rester utilisable si une brique tombe.
- **MVP :** conversation vocale en streaming, interruption, traces temporelles JSONL, mode texte de secours.

## Architecture

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

Toutes les commandes se lancent depuis le dossier du projet :

```bash
git clone https://github.com/jsboigeEPF/2026-MSMIN5IN52-GenAI.git && cd 2026-MSMIN5IN52-GenAI/groupe-marc-guehi
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev,analysis]"
cp .env.example .env   # puis renseigner les clés (jamais commitées)
```

### Windows (PowerShell)

Les commandes de ce README sont écrites pour macOS/Linux ; sous Windows :

| macOS / Linux | Windows (PowerShell) |
|---|---|
| `.venv/bin/python` | `.venv\Scripts\python` |
| `.venv/bin/uvicorn` | `.venv\Scripts\uvicorn` |
| `cp .env.example .env` | `Copy-Item .env.example .env` |
| `VOICE_DRY_RUN=1 commande` | `$env:VOICE_DRY_RUN="1"; commande` (puis `Remove-Item Env:VOICE_DRY_RUN`) |

Pour afficher correctement les accents dans la console : `$env:PYTHONUTF8="1"`. Port déjà utilisé : `netstat -ano | findstr :8000`, ou choisir un autre `--port` (les scripts de test visent le port 8765 ou l'URL passée en option). Le test `tests/test_mp3.py` est ignoré sans `ffmpeg`. Le code a été développé sous macOS ; le comportement sous Windows n'a pas été exécuté (lecture du code et correction des points non portables seulement).

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
.venv/bin/python -m pytest -q          # tests unitaires et d'intégration, sans réseau
.venv/bin/ruff check src tests
# Tests de bout en bout : démarrer d'abord un serveur sur le port 8765, au choix
VOICE_DRY_RUN=1 .venv/bin/uvicorn src.transport.app:app --port 8765   # faux fournisseurs, rien à payer
.venv/bin/uvicorn src.transport.app:app --port 8765                   # vrais services (clés dans .env, payant)
.venv/bin/python scripts/browser_smoke.py   # Chrome, micro simulé
.venv/bin/python scripts/ws_barge_test.py data/audio/question1.wav data/audio/question2.wav   # interruption (même serveur)
.venv/bin/python scripts/run_campaign.py all --dry-run   # plan, durée, coût
.venv/bin/python scripts/run_campaign.py all --out data/campaign/<run>
# écho simulé (hors ligne, gratuit) ; la variante en direct `echo` est payante
.venv/bin/python scripts/run_campaign.py echosweep --out data/campaign/echo1 --stimuli data/campaign/<run>
```

Les WAV de test (`data/audio/question1.wav`, `question2.wav`, gitignorés, absents d'un clone neuf) sont à fournir : parole mono, voix d'un membre du groupe ou synthèse. **Sans eux**, `browser_smoke.py` et `ws_barge_test.py` ne sont pas utilisables ; tout le reste marche : `pytest`, `ruff`, `run_turn.py --dry-run`, le serveur `VOICE_DRY_RUN=1` et `run_campaign.py all --dry-run`. Les phases de campagne synthétisent leurs propres stimuli.

Analyse : `notebooks/analysis.ipynb` (`CAMPAIGN_RUN=data/campaign/<run>`). Protocole et définitions : [docs/METRICS.md](docs/METRICS.md). Résultats du run1 : [docs/DECISIONS.md](docs/DECISIONS.md) (entrée 11).

## Résultats et évaluation

Protocole (stimuli synthétiques, bruits blanc et « foule », SNR de 20 à 0 dB, serveur réel avec micro simulé) : [docs/METRICS.md](docs/METRICS.md). Résultats du run1 (voix et bruits synthétiques, sans écho, petits échantillons) :

- STT Whisper : WER ≈ 0 % jusqu'à 5 dB de SNR (bruit blanc 0 dB : 16 %, foule 0 dB : 67 %), ≈ 0,45 s.
- Latence perçue (p50/p95) sans accroche : 2,06 / 2,45 s ; avec accroche, premier son ≈ 0,52 s mais le début du contenu reste ≈ 2,1 s.
- Interruptions correctement gérées : 14/21 (67 %) ; arrêt propre 21/21. Les échecs viennent d'une détection trop lente sur phrase très courte et de phrases coupées en plusieurs tours.
- Foule synthétique : fin de parole jamais détectée dès 10 dB.

Mesures complémentaires (écho simulé, balayages de seuils, fin de tour adaptative) : DECISIONS 18. Tous les détails, limites et chiffres à jour sont dans [docs/DECISIONS.md](docs/DECISIONS.md) ; le projet n'a pas de baseline « sans GenAI », la comparaison porte sur les variantes de réglage.

## Limites et plan B

Plan B de démonstration sans réseau ni clé : `VOICE_DRY_RUN=1` (faux fournisseurs) ; mode texte de secours en cas de panne du STT ; STT, LLM et TTS ont chacun un modèle de secours. Si OpenRouter lui-même tombe, tout tombe (même fournisseur). Coût d'un tour réel ≈ 0,01 $.

## Limites connues

- Mono-utilisateur (un VAD à état partagé).
- Pas d'AEC serveur : sans casque, l'écho peut déclencher de faux barge-in (étude simulée : DECISIONS 18 ; l'annulation d'écho du navigateur n'a pas été mesurée).
- Foule de voix : la fin de tour n'est pas détectée dès 10 dB de SNR ; taux d'interruptions correctes du run1 : voir DECISIONS (11).
- L'accroche réduit le temps au premier son, pas l'attente du contenu ; latences p50/p95 mesurées : DECISIONS (11).
- Recherche internet : 2–3 s d'attente (couverte par une accroche) ; le résultat brut n'est pas gardé en mémoire de conversation.
- Réglages recommandés par les mesures (fin de tour adaptative, traitement de l'écho) étudiés mais non appliqués : voir DECISIONS (18).
- Mesures sur voix et bruits synthétiques, petits échantillons.

## Données et confidentialité

`.env`, `data/` (audio, traces, coûts) sont gitignorés. L'audio du micro est envoyé au serveur STT de l'école et le texte à OpenRouter : ne rien dire de personnel. Coût cumulé du projet : somme des `cost_usd` de `data/cost_ledger.jsonl` (ne pas lire `total_usd`, propre à chaque processus ; voir [docs/CONSTRAINTS.md](docs/CONSTRAINTS.md)).

## Assistance par IA

Ce projet a été développé avec l'aide de Claude Code (Anthropic), qui a écrit une grande partie du code et des docs sous la direction des membres.

## Licence et références

- Code du groupe : pas de licence spécifique ajoutée (usage pédagogique).
- Silero VAD (MIT) : <https://github.com/snakers4/silero-vad>. Autres bibliothèques : voir `pyproject.toml`.
- Modèles utilisés via API : Whisper large-v3-turbo (serveur de l'école), DeepSeek V4.1 Flash et MiniMax Speech 2.8 Turbo via OpenRouter, soumis à leurs conditions d'utilisation.
- Données : audio de test synthétique ou voix d'un membre du groupe, aucune donnée de tiers.
