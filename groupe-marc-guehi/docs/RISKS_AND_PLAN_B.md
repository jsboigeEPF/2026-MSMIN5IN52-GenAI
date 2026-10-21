# Risques et plan B

| Risque | Impact | Mitigation |
|---|---|---|
| **Écho / boucle audio** (le HP est capté par le micro et déclenche un faux barge-in) | Faux positifs, agent se coupe lui-même | Casque pour les mesures ; AEC du système (macOS voice processing) ou navigateur ; seuil VAD relevé + durée min pendant la sortie ; test « bruit seul / écho seul » dans la campagne |
| **VAD trop sensible / pas assez** | Faux barge-in ou fin de tour trop lente/rapide | Seuils en config, calibration sur dialogues scriptés, hangover réglable, mesurer faux positifs/négatifs |
| **Latence réseau cloud variable** | p95 instable | Beaucoup de tours, heures différentes, enregistrer RTT ; séparer latence réseau et traitement |
| **Dérive d'horloge** | Mesures fausses | Un seul processus, une seule horloge monotone ; pas de comparaison d'horloges entre machines |
| **Latence Whisper par segments** (pas de vrai streaming) | E2E élevé | Tester STT streaming cloud ; modèle local plus petit ; mesurer et documenter l'écart |
| **Chevauchement TTS/LLM mal fait** (attendre toute la réponse) | E2E dégradé | Découpe par phrases, TTS lancé à la 1re phrase |
| **Coût** | Budget dépassé | Voir CONSTRAINTS (dry-run, plafond, compteur) |
| **Throttling thermique MacBook Air** | Latence locale dégradée | Campagnes courtes, pauses, noter la charge |
| **APIs changeantes** (noms de modèles, endpoints ; sources contradictoires trouvées) | Code cassé | Interfaces `Protocol`, versions de modèles en config, re-vérifier la doc au début de l'étape concernée |
| **Périmètre trop ambitieux** (difficulté 4/5) | Rien de fini | MVP console d'abord ; extensions seulement après mesures |

## Plan B : replays audio horodatés
But : mesurer sans micro ni conversation live coûteuse/non reproductible.
- Source `AudioFile` : un fichier WAV 16 kHz (parole synthétique ou enregistrée) + fichier d'annotation (début/fin réels de parole, instants d'interruption, texte de référence) est injecté à cadence temps réel (ou accélérée pour STT seul).
- Le bruit est ajouté hors ligne à SNR fixé → reproductible.
- Les traces JSONL produites sont identiques au mode live ; l'analyse est inchangée.
- Variante dégradée supplémentaire : rejouer des **réponses cloud enregistrées** (stub providers) pour tester l'orchestrateur et la logique de barge-in sans aucun coût (latences alors simulées, signalées comme telles).
- Si le cloud est inaccessible/budget épuisé : campagne complète en tout-local (STT/LLM/TTS locaux), résultats présentés comme tels.
