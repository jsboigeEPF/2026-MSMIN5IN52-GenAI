# Roadmap

Une étape = une session. ☐ à faire · ◐ commencé · ☑ fait. Échéance : **lundi 2026-10-26** (prototype minimal). Les extensions (voix multiples, routage entre APIs) passent après ; local et anglais écartés.

| # | Étape | Critère de validation |
|---|---|---|
| 0 ☑ | Cadrage, recherche, docs | Docs créés |
| 1 ☑ | Squelette : `.env`, `pyproject`, config, interfaces, `Tracer`, venv Python 3.12 | Fait ; les 3 services répondent |
| 2 ☑ | Providers réels (Whisper école, DeepSeek V4.1 Flash, MiniMax mp3 → PCM en flux), compteur de coût indicatif, `--dry-run`, `Recorder` (traces + coût), découpe en phrases, `run_turn` | **Validé** : tour réel texte→audio ≈ 1,1 s au 1er audio (hors fin de parole), aller-retour STT fidèle ; 15 tests verts, ruff propre |
| 3 ☑ | Orchestrateur : VAD Silero + endpointing + session WebSocket (`src/vad`, `src/orchestrator/session.py`), STT spéculatif dès 200 ms de silence — **fait** ; campagne p50/p95 = étape 7 | Conversation réelle validée (ws + Chrome) ; STT spéculatif ≈ 260 ms gagnés (DECISIONS 13c) |
| 4 ☑ | Serveur FastAPI + page web (micro AudioWorklet, lecture, transcript, états, mode texte) — **fait** (`src/transport/`) | Validé dans Chrome (micro simulé) et **au vrai micro par l'étudiant (2026-10-22)** |
| 5 ☑ | Barge-in : VAD permanent (seuil relevé 0,75 et ≥ 300 ms de parole pendant que l'agent parle), annulation LLM/TTS, arrêt de la lecture côté client, troncature de l'historique au texte entendu, reprise de la phrase non transcrite — **fait** | Validé avec les vrais services : micro simulé WebSocket + Chrome (1 interruption, 2 questions comprises) ; **taux d'interruptions correctes à mesurer (étape 7)** |
| 6 ☑ | Mode texte de secours : champ texte dans l'UI, même chaîne ; **bascule automatique** si le STT échoue (STT de secours OpenRouter, puis mode texte après 2 échecs, bouton « Réessayer la voix ») — **fait** | Saisie clavier → réponse vérifiée dans Chrome ; bascule vérifiée par test (`tests/test_session_search.py`), pas encore en panne réelle |
| 7 ☑ | Campagne de mesures : `scripts/run_campaign.py`, `configs/campaign.yaml`, `notebooks/analysis.ipynb` (WER/SNR, VAD, latence p50/p95, interruptions, balayages de calibration) — **fait** (run `data/campaign/run1`) | Tableaux et figures reproductibles ; résultats et recommandations dans DECISIONS (11) |
| 8 ☑ | Polissage UI (rôles, badge « interrompu », voyant de voix, médiane de session, mobile, `prefers-reduced-motion`, focus du champ texte sur erreur), `README.md`, docs à jour — **fait** | Critères « terminé » vérifiés dans DECISIONS (12) ; reste à valider au vrai micro/casque par l'étudiant |
| 9 ☑ | Accès internet (outil `web_search`, accroche d'attente, protection contre les appels d'outil écrits en texte) — **fait** | Vérifié avec les vrais services, en Chrome : question avec recherche (DECISIONS 13) |
| 10+ ☐ | Extensions restantes : routage entre APIs (STT/LLM/TTS de repli), plus de voix. **Local écarté** (consigne du 2026-10-22 : APIs uniquement) | Après lundi |

Cible réaliste : étapes 1–6 pour lundi (prototype), 7 si le temps le permet. Difficulté 4/5 en 6 jours : le barge-in propre et la campagne de mesure complète sont les éléments à risque.

## Pistes d'amélioration (retours de l'étudiant et idées)
- **Mémoire au sein de la conversation** *(piste de l'étudiant)* : les 5 derniers messages pourraient suffire. État actuel : l'historique est déjà conservé pendant la session (`session.max_history_messages: 20` dans `configs/base.yaml`, troncature au texte entendu en cas d'interruption). À faire : fixer la valeur (5 ?) en comparant qualité des réponses et latence/coût du LLM, et décider si la mémoire doit survivre à une reconnexion (aujourd'hui, une nouvelle conversation repart de zéro).
- **Attente de recherche** : 2–3 s de silence après l'accroche « je regarde ça » ; piste : seconde accroche si la recherche dépasse un seuil, ou recherche lancée plus tôt (spéculative).
- LLM spéculatif sur transcription partielle : voir DECISIONS.
- **Débit de voix** réglable par voix dans `configs/base.yaml` (`tts.speeds`).
