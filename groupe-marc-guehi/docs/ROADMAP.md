# Roadmap

Une étape = une session. ☐ à faire · ◐ commencé · ☑ fait. Échéance : **lundi 2026-10-26** (prototype minimal). Les extensions (voix multiples, routage entre APIs) passent après ; local et anglais écartés.

| # | Étape | Critère de validation |
|---|---|---|
| 0 ☑ | Cadrage, recherche, docs | Docs créés |
| 1 ☑ | Squelette : `.env`, `pyproject`, config, interfaces, `Tracer`, venv Python 3.12 | Fait ; les 3 services répondent |
| 2 ☑ | Providers réels (Whisper école, DeepSeek V4.1 Flash, MiniMax mp3 → PCM en flux), compteur de coût indicatif, `--dry-run`, `Recorder` (traces + coût), découpe en phrases, `run_turn` | **Validé** : tour réel texte→audio ≈ 1,1 s au 1er audio (hors fin de parole), aller-retour STT fidèle ; tests verts, ruff propre |
| 3 ☑ | Orchestrateur : VAD Silero + endpointing + session WebSocket (`src/vad`, `src/orchestrator/session.py`), STT spéculatif dès 200 ms de silence — **fait** ; campagne p50/p95 = étape 7 | Conversation réelle validée (ws + Chrome) ; STT spéculatif ≈ 260 ms gagnés (DECISIONS 13c) |
| 4 ☑ | Serveur FastAPI + page web (micro AudioWorklet, lecture, transcript, états, mode texte) — **fait** (`src/transport/`) | Validé dans Chrome (micro simulé) et **au vrai micro par l'étudiant (2026-10-22)** |
| 5 ☑ | Barge-in : VAD permanent (seuil relevé (`session.barge_in_min_prob`) et durée minimale (`session.barge_in_min_speech_ms`) pendant que l'agent parle), annulation LLM/TTS, arrêt de la lecture côté client, troncature de l'historique au texte entendu, reprise de la phrase non transcrite — **fait** | Validé avec les vrais services : micro simulé WebSocket + Chrome (1 interruption, 2 questions comprises) ; **taux d'interruptions correctes à mesurer (étape 7)** |
| 6 ☑ | Mode texte de secours : champ texte dans l'UI, même chaîne ; **bascule automatique** si le STT échoue (STT de secours OpenRouter, puis mode texte après 2 échecs, bouton « Réessayer la voix ») — **fait** | Saisie clavier → réponse vérifiée dans Chrome ; bascule vérifiée par test (`tests/test_session_search.py`), pas encore en panne réelle |
| 7 ☑ | Campagne de mesures : `scripts/run_campaign.py`, `configs/campaign.yaml`, `notebooks/analysis.ipynb` (WER/SNR, VAD, latence p50/p95, interruptions, balayages de calibration) — **fait** (run `data/campaign/run1`) | Tableaux et figures reproductibles ; résultats et recommandations dans DECISIONS (11) |
| 8 ☑ | Polissage UI (rôles, badge « interrompu », voyant de voix, médiane de session, mobile, `prefers-reduced-motion`, focus du champ texte sur erreur), `README.md`, docs à jour — **fait** | Critères « terminé » vérifiés dans DECISIONS (12) ; reste à valider au vrai micro/casque par l'étudiant |
| 9 ☑ | Accès internet (outil `web_search`, accroche d'attente, protection contre les appels d'outil écrits en texte) — **fait** | Vérifié avec les vrais services, en Chrome : question avec recherche (DECISIONS 13) |
| 10 ☑ | Robustesse (2026-10-24) : l'agent **dit ce qu'il cherche** pendant la recherche web + 2e phrase si elle traîne ; **TTS de secours** (autre modèle OpenRouter) et réponse écrite si toute la voix est en panne ; bascule vers le mode texte **testée dans Chrome** — **fait** | Vérifié avec les vrais services (DECISIONS 15) ; mesure de la durée du silence résiduel à refaire en campagne |
| 11 ◐ | Refactor de `ConversationSession` et cohérence des docs — **fait** (DECISIONS 17) ; condition écho (simulée hors ligne **faite**, en direct **prête mais non lancée**), balayages de seuils et de fin de tour, étude d'une fin de tour adaptative — **faits hors ligne** (DECISIONS 18) ; réglages recommandés **non appliqués** | Tests, `browser_smoke.py` et `ws_barge_test.py` en dry-run ; mesure en direct de l'écho et réglages : en attente de l'accord de l'étudiant |
| 11b ◐ | Audit (2026-10-26) : lot A sécurité et coûts **fait** (DECISIONS 25) ; lots B robustesse (DECISIONS 26) et C mesure (DECISIONS 27) **faits** ; lot E docs **fait** (DECISIONS 28) ; lot D (propositions D1-D3) rédigé, en attente d'arbitrage | Tests, `browser_smoke.py` en dry-run ; vérification par un auditeur indépendant à chaque lot |
| 12+ ☐ | Extensions restantes : routage entre APIs selon la latence, plus de voix. **Local écarté** (consigne du 2026-10-22 : APIs uniquement, confirmée le 2026-10-24) | Après lundi |

### Évolutions faites après l'étape 11 (détail et preuves dans DECISIONS)
- (19) accroche d'ouverture systématique et porte de niveau du VAD ; (20)-(21) recherche web forcée avec échappatoire `no_search` ;
- (22) bouton « Couper le micro » ; (23) interface en deux vues Voix / Écrit, sous-titres par groupes de mots ; (24) accroches dupliquées corrigées ;
- (25)-(27) audit du 2026-10-26 : sécurité et coûts, robustesse, mesure (`playback_first_sample`, horloge `monotonic_ns`). Propositions D1-D3 : en attente d'arbitrage de l'étudiant ; lot E (docs) fait.

Cible réaliste : étapes 1–6 pour lundi (prototype), 7 si le temps le permet. Difficulté 4/5 en 6 jours : le barge-in propre et la campagne de mesure complète sont les éléments à risque.

## Pistes d'amélioration (retours de l'étudiant et idées)
- **Mémoire au sein de la conversation** *(piste de l'étudiant)* : les 5 derniers messages pourraient suffire. État actuel : l'historique est déjà conservé pendant la session (`session.max_history_messages: 20` dans `configs/base.yaml`, troncature au texte entendu en cas d'interruption). À faire : fixer la valeur (5 ?) en comparant qualité des réponses et latence/coût du LLM, et décider si la mémoire doit survivre à une reconnexion (aujourd'hui, une nouvelle conversation repart de zéro).
- **Attente de recherche** : annonce parlée + 2e phrase faites (étape 10) ; reste la piste d'une recherche lancée plus tôt (spéculative).
- LLM spéculatif sur transcription partielle : voir DECISIONS.
- **Débit de voix** réglable par voix dans `configs/base.yaml` (`tts.speeds`).
