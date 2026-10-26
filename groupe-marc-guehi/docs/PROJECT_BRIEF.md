# Project brief

## Énoncé (D1, difficulté 4/5)
Agent vocal temps réel sous contrainte de latence. Assembler STT, LLM et TTS en flux duplex, mesurer chaque étape, gérer l'interruption par l'utilisateur (barge-in). Mots-clés : realtime, voice-agent, latency, barge-in. Composition musicale exclue.

Ressources fournies : CoursIA (jsboige/CoursIA : notebooks GenAI/Audio 03-3 Realtime Voice API, 03-2 Audio Pipeline Orchestration, 01-2 OpenAI Whisper STT), doc OpenAI Realtime, WebRTC, Silero VAD.

## Contrainte ajoutée par l'étudiant
Architecture **cascade STT → LLM → TTS** obligatoire (permet la mesure par étape).

## Périmètre
**MVP**
- Conversation vocale en streaming (micro → réponse audio).
- Interruption (barge-in) : l'agent se tait et reprend l'écoute.
- Traces temporelles par étape (JSONL).
- Mode texte de secours (clavier → LLM → texte/TTS).

**Critères de réussite**
- Latence p50/p95 (fin de parole → premier octet audio agent) et par étape.
- Taux d'interruptions correctement gérées.
- WER de la transcription sous bruit contrôlé (SNR fixé) ; tests automatisés.

**Extensions** (par ordre de priorité proposé)
1. VAD local Silero (inclus de fait dans le MVP, voir DECISIONS) → réglage/comparaison.
2. Routage cloud/local (STT/LLM/TTS local en repli ou selon latence).
3. Plusieurs voix.

## Hors périmètre
Composition musicale ; speech-to-speech comme architecture principale ; entraînement/fine-tuning de modèles ; clonage de voix ; multi-utilisateur/production ; app mobile ; téléphonie (SIP).

## Définition de "terminé"
- MVP démontrable en direct (conversation + barge-in + repli texte).
- Une campagne de mesure complète (protocole de @docs/METRICS.md) exécutée, traces archivées, notebook produisant p50/p95, taux de barge-in, WER par SNR.
- Tests `pytest` verts (dont tests sur audio bruité rejoué).
- Coût cloud cumulé < 15 € documenté.
- Docs à jour ; rapport/slides hors périmètre de ce dépôt sauf demande.
