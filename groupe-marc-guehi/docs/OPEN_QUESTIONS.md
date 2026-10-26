# Questions ouvertes

Tranchées (voir DECISIONS) : langue = français ; échéance = lundi 2026-10-26 ; interface = navigateur ; livrables d'évaluation = plus tard ; clés, modèles, endpoints STT/LLM/TTS et crédit OpenRouter = résolus aux étapes 1-2 (questions 1 à 3 closes, voir TECH_STACK et DECISIONS 6-8) ; casque = recommandé, non vérifié (question 5).

| # | Question | À trancher avant | Hypothèse actuelle |
|---|---|---|---|
| 1 | ~~URL et clé du serveur Whisper de l'école ; clé OpenRouter ; identifiants exacts des modèles~~ **close** | Étape 1 | `.env` rempli |
| 2 | ~~OpenRouter expose-t-il un endpoint TTS (et STT) en streaming ?~~ **close** : oui (DECISIONS 6-7) | Étape 1 | — |
| 3 | ~~Crédit OpenRouter et prix par modèle~~ **close** : prix dans `configs/pricing.yaml`, coût suivi dans `data/cost_ledger.jsonl` (CONSTRAINTS) | Étape 2 | — |
| 4 | Baseline speech-to-speech Realtime : autorisée malgré l'exigence cascade ? | Étape 7 | Non |
| 5 | Casque disponible pour les tests live ? | Étape 4 | Oui |
| 6 | Seuils de l'« interruption correcte » | Étape 5 | Valeurs de `configs/campaign.yaml` (`barge.thresholds`), définition dans METRICS |
| 7 | Livrables d'évaluation (rapport, slides) | Étape 8 | Plus tard |
