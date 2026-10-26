# Vidéo de secours : deux minutes

Enregistrer le terminal et l'audio avec OBS Studio sur le poste réel. Ne pas
présenter la simulation comme une conversation avec les modèles. Préparer la
configuration AEC et le warmup avant le début de l'enregistrement.

| Temps | Action | Ce que le jury doit constater |
|---|---|---|
| 0:00–0:12 | Montrer les modèles locaux et la commande de lancement | Contexte matériel et absence d'appel cloud |
| 0:12–0:35 | « Explique-moi en deux phrases ce qu'est un VAD. » | Réponse française, premiers sons avant fin de génération |
| 0:35–1:00 | Demander l'histoire d'Internet, puis « Stop, plutôt le Web » | Arrêt, nouvelle réponse, historique tronqué |
| 1:00–1:15 | Tousser brièvement pendant une réponse | Rejet du faux départ si la calibration le permet |
| 1:15–1:35 | Mode `--text`, nouvelle question au clavier | Même pipeline et solution de repli |
| 1:35–2:00 | Ouvrir le rapport de la session réelle | n, p50/p95, limites et graphiques |

Avant de filmer, faire un essai de cinq tours et vérifier que l'enregistrement OBS
ne provoque pas un dépassement de VRAM. En cas d'échec acoustique, activer
`--half-duplex`, dire explicitement que l'interruption est désactivée et présenter
le résultat E5 qui justifie ce choix. Ne pas couper au montage un échec pour le
présenter comme une mesure réussie.

Plan B pour le terminal : `python -m voiceagent demo --mock`. Cette commande est
une démonstration technique de l'orchestration ; elle est étiquetée `simulated`.
Le véritable plan B pour le jury reste le replay de tes WAV réels.
