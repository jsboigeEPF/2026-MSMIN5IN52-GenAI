# Roadmap

Une étape = une session. ☐ à faire · ◐ commencé · ☑ fait. Échéance : **lundi 2026-10-26** (prototype minimal). Les extensions (local, voix multiples, anglais) passent après.

| # | Étape | Critère de validation |
|---|---|---|
| 0 ☑ | Cadrage, recherche, docs | Docs créés |
| 1 ☑ | Squelette : `.env`, `pyproject`, config, interfaces, `Tracer`, venv Python 3.12 | Fait ; les 3 services répondent |
| 2 ☑ | Providers réels (Whisper école, DeepSeek V4.1 Flash, MiniMax mp3 → PCM en flux), compteur de coût indicatif, `--dry-run`, `Recorder` (traces + coût), découpe en phrases, `run_turn` | **Validé** : tour réel texte→audio ≈ 1,1 s au 1er audio (hors fin de parole), aller-retour STT fidèle ; 15 tests verts, ruff propre |
| 3 ◐ | Orchestrateur : VAD Silero + endpointing + session WebSocket — **fait** (`src/vad`, `src/orchestrator/session.py`) ; reste : campagne p50/p95 sur ≥ 20 tours replay, découpe STT par morceaux | Conversation réelle validée (ws + Chrome) ; p50/p95 à produire |
| 4 ◐ | Serveur FastAPI + page web (micro AudioWorklet, lecture, transcript, états, mode texte) — **prototype fonctionnel** (`src/transport/`) ; reste : polissage UI, barge-in (étape 5) | Conversation vocale dans Chrome validée avec micro simulé ; à valider au vrai micro/casque par l'étudiant |
| 5 ☑ | Barge-in : VAD permanent (seuil relevé 0,75 et ≥ 300 ms de parole pendant que l'agent parle), annulation LLM/TTS, arrêt de la lecture côté client, troncature de l'historique au texte entendu, reprise de la phrase non transcrite — **fait** | Validé avec les vrais services : micro simulé WebSocket + Chrome (1 interruption, 2 questions comprises) ; **taux d'interruptions correctes à mesurer (étape 7)** |
| 6 ◐ | Mode texte de secours : champ texte dans l'UI, même chaîne — **fait** ; reste : bascule automatique si le STT échoue (aujourd'hui : message d'erreur + invitation à écrire) | Saisie clavier → réponse vérifiée dans Chrome |
| 7 ☑ | Campagne de mesures : `scripts/run_campaign.py`, `configs/campaign.yaml`, `notebooks/analysis.ipynb` (WER/SNR, VAD, latence p50/p95, interruptions, balayages de calibration) — **fait** (run `data/campaign/run1`) | Tableaux et figures reproductibles ; résultats et recommandations dans DECISIONS (11) |
| 8 ☐ | Polissage UI, README, docs à jour | Critères « terminé » vérifiés |
| 9+ ☐ | Extensions : local (whisper.cpp/Kokoro), routage cloud/local, plusieurs voix, anglais | Après lundi |

Cible réaliste : étapes 1–6 pour lundi (prototype), 7 si le temps le permet. Difficulté 4/5 en 6 jours : le barge-in propre et la campagne de mesure complète sont les éléments à risque.

## Pistes d'amélioration (retours de l'étudiant et idées)
- **Accès internet** *(piste de l'étudiant)* : l'agent n'a aucun accès au web (actualité, météo, etc.). Piste : outil de recherche appelé par le LLM (function calling) ; prévoir une accroche adaptée (« je regarde ») seulement à ce moment-là.
- **Mémoire au sein de la conversation** *(piste de l'étudiant)* : les 5 derniers messages pourraient suffire. État actuel : l'historique est déjà conservé pendant la session (`session.max_history_messages: 20` dans `configs/base.yaml`, troncature au texte entendu en cas d'interruption). À faire : fixer la valeur (5 ?) en comparant qualité des réponses et latence/coût du LLM, et décider si la mémoire doit survivre à une reconnexion (aujourd'hui, une nouvelle conversation repart de zéro).
- **STT par morceaux** (transcription pendant que l'utilisateur parle) : reporté à la demande de l'étudiant ; gain estimé ≈ 0,3–0,5 s.
- **Anglais**, TTS local (Kokoro) pour le 1er morceau, LLM spéculatif sur transcription partielle : voir DECISIONS.
- **Débit de voix** réglable par voix dans `configs/base.yaml` (`tts.speeds`).
