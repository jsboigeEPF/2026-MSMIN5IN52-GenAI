# Agent vocal temps réel (projet D1, 5A)

## Objectif
- Agent vocal duplex en cascade **STT → LLM → TTS** (architecture imposée), streaming de bout en bout.
- Mesurer chaque étape (p50/p95), gérer le barge-in, avoir un mode texte de secours.
- Réussite = latences p50/p95, taux d'interruptions bien gérées, WER sur bruit contrôlé. Budget cloud < 15 €.

## Contraintes dures
- Architecture cascade obligatoire. Pas de speech-to-speech comme solution principale (voir @docs/DECISIONS.md).
- MacBook Air M5 24 Go, aucun GPU externe. Cloud = APIs payantes uniquement.
- Budget cloud indicatif < 15 € (non prioritaire) : tout appel payant passe par le compteur de coût ; `--dry-run` sans réseau (@docs/CONSTRAINTS.md).
- Clés API dans `.env` (gitignoré), jamais dans le code, les logs ou les traces.
- Audio de test/traces dans `data/` (gitignoré). Pas de données personnelles de tiers.
- Hors sujet : composition musicale.

## Règle de travail
1. Avant d'agir, lire les docs pertinents ci-dessous.
2. Une étape de @docs/ROADMAP.md par session ; ne pas sortir de son périmètre.
3. Après chaque étape : mettre à jour `docs/DECISIONS.md` et `docs/ROADMAP.md`.
4. Un fait vit dans un seul fichier ; ne pas dupliquer. Distinguer fait vérifié (source) / hypothèse / recommandation.
5. Ne rien installer ni lancer d'appel payant sans accord explicite.

## Conventions de code
- Python 3.12, `asyncio`, type hints, `ruff` + `pytest`. Un module = une brique (interface `Protocol` + implémentations interchangeables).
- Horloge : `time.monotonic_ns()` pour les durées, horodatage mur UTC dans les traces. Toute mesure passe par `src/metrics`.
- Config dans `configs/*.yaml`, jamais de constantes magiques. Docs et commentaires en français, identifiants en anglais.

## Structure du dépôt
`src/{audio,vad,stt,llm,tts,orchestrator,transport,metrics,replay,fallback}`, `tests/`, `scripts/`, `data/` (gitignoré), `notebooks/`, `configs/`, `docs/`. Détail : @docs/ARCHITECTURE.md

## Commandes usuelles (à compléter)
- Install : `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev,analysis]"`
- Un tour réel : `.venv/bin/python scripts/run_turn.py --text "..." [--play]` ; sans réseau : `--dry-run`
- Tests : `.venv/bin/python -m pytest -q` ; lint : `.venv/bin/ruff check src tests`
- Serveur web : `.venv/bin/uvicorn src.transport.app:app --port 8000` puis ouvrir http://localhost:8000 (`VOICE_DRY_RUN=1` = faux fournisseurs)
- Test navigateur automatisé (Chrome, micro simulé) : `.venv/bin/python scripts/browser_smoke.py` ; micro simulé WebSocket : `scripts/ws_client.py` ; interruption : `scripts/ws_barge_test.py` (serveur sur le port 8765) ; bascule texte : serveur lancé avec `VOICE_FAIL_STT=1` puis `scripts/browser_fallback_test.py`
- Campagne de mesures : `.venv/bin/python scripts/run_campaign.py all --dry-run` puis `all [--quick] --out data/campaign/<run>` ; écho simulé hors ligne : `run_campaign.py echosweep --out data/campaign/echo1 --stimuli data/campaign/run1` ; analyse : `notebooks/analysis.ipynb`

## Docs détaillés
- @docs/PROJECT_BRIEF.md — énoncé, périmètre, "terminé"
- @docs/TECH_STACK.md — choix techniques par brique, faits vérifiés
- @docs/ARCHITECTURE.md — flux, états, annulation, repli texte
- @docs/METRICS.md — définitions, JSONL, protocole, WER
- @docs/CONSTRAINTS.md — budget, matériel, clés, confidentialité
- @docs/RISKS_AND_PLAN_B.md — risques et replays
- @docs/ROADMAP.md — étapes ; @docs/DECISIONS.md — journal ; @docs/OPEN_QUESTIONS.md
