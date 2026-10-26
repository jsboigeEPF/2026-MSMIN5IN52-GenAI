# Contraintes

## Budget cloud : < 15 € au total (cible interne : 10 €, réserve 5 €)
**Précision (2026-10-20) :** l'énoncé fixe 15 € par prudence ; l'étudiant indique que ce n'est pas une priorité. Le compteur (`data/cost_ledger.jsonl`) avertit au dépassement mais ne bloque pas (`hard_stop` activable). Ordre de grandeur mesuré : ≈ 0,01 $ par tour réel complet, ≈ 0,017 $ avec recherche internet (≈ 0,007 $ la recherche). **Coût cumulé** : source unique = la somme des `cost_usd` de `data/cost_ledger.jsonl` (ne pas lire `total_usd`, propre à chaque processus ; DECISIONS 13f) ; le chiffre à jour est dans la dernière entrée de DECISIONS.
**Historique** (cadrage du 2026-10-20, avant le choix OpenRouter/école ; tarifs réels : `configs/pricing.yaml`). Tarifs OpenAI **[V]** 2026-10-20 (<https://developers.openai.com/api/docs/pricing>) : gpt-4o-mini-transcribe ≈ 0,003 $/min ; gpt-4o-mini-tts 0,60 $/1M tokens texte + 12 $/1M tokens audio ; gpt-5-mini 0,25/2,00 $ par 1M tokens (in/out). Conversion $→€ : hypothèse 1 $ = 1 €  (marge de sécurité).
Hypothèse [H] : durée TTS ≈ 1 min audio ≈ quelques centimes (équivalence tokens audio/seconde non vérifiée → à calibrer sur un appel réel).

**Méthode d'estimation par campagne**
`coût = N_tours × (durée_parole_min × prix_STT + tokens_in × prix_in + tokens_out × prix_out + durée_réponse_s × prix_TTS_par_s)`.
Ordre de grandeur [H] : 1 tour ≈ 0,001–0,005 €. Campagne complète de ≈1 000 tours ≈ 1–5 €. Le cloud n'est donc pas le facteur limitant en cascade ; les modèles realtime speech-to-speech (32/64 $ par 1M tokens audio) le seraient.

**Garde-fous** (état réel, vérifié dans le code le 2026-10-26 ; le cadrage initial prévoyait plus)
- `--dry-run` : calcule et affiche le coût estimé sans appel payant (providers factices).
- Compteur de coût persistant `data/cost_ledger.jsonl` ; le plafond (`BUDGET_MAX_EUR` dans `.env`) est **indicatif** : avertissement au dépassement, arrêt dur seulement si `hard_stop` est activé (`CostLedger`, désactivé par défaut). Pas de `configs/budget.yaml`.
- *Non implémenté* : plafond par campagne et confirmation interactive au-delà de 1 € (la campagne affiche plan, durée et coût avec `--dry-run`).
- Limites de dépense configurées aussi côté tableau de bord du fournisseur (action manuelle).
- Cache des audios TTS de test (un dialogue n'est synthétisé qu'une fois).

## Matériel
MacBook Air M5, 24 Go, pas de GPU externe. Seul le VAD Silero tourne en local (STT, LLM et TTS sont des APIs, DECISIONS 13). Historique du cadrage : STT Whisper small/turbo ou Parakeet, LLM ~4B 4 bits, TTS Kokoro, écartés. Non réaliste : LLM > 14B en temps réel, plusieurs modèles lourds simultanés, fine-tuning. Pas de refroidissement actif sur Air : throttling possible sur sessions longues → noter température/charge, campagnes courtes, cloud et local jamais comparés en même temps (CPU partagé).

## Clés API
`.env` local (gitignoré), `.env.example` sans valeur commitée. Chargement via variables d'environnement. Jamais dans traces, logs, notebooks. Révoquer les clés en fin de projet.

## Confidentialité
Enregistrements : uniquement voix de l'étudiant ou audio synthétique/libre de droits. Pas de voix de tiers sans consentement. L'audio envoyé aux fournisseurs cloud sort de la machine : ne rien dire de personnel ; vérifier la politique de rétention du fournisseur (non vérifiée). `data/` gitignoré ; traces sans audio brut.

## Deadlines
Inconnues (voir @docs/OPEN_QUESTIONS.md). Roadmap dimensionnée en étapes d'une session ; dates à poser après réponse.
