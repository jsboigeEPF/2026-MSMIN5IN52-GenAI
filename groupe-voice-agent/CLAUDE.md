# Contexte du projet

Lire README.md, docs/ARCHITECTURE.md et docs/DECISIONS.md.
Python 3.11/3.12. Local uniquement, français, une conversation à la fois.
P0/P1 : pipeline vocal, interruption, traces, replay, statistiques.
P2 cloud et routage auto hors livraison actuelle.

## Règles de contribution

- Une responsabilité par module ; fonctions courtes et noms explicites.
- Ne jamais charger un modèle à l'import d'un module.
- Ne jamais exécuter une inférence native sur la boucle asyncio.
- Ne jamais conserver comme entendu un texte seulement généré.
- Pas de résultat matériel inventé. Les mocks gardent le mode `simulated`.
- Ne pas remplacer l'annulation structurée par des tâches détachées.
- Ajouter un test de régression pour une correction de concurrence ou de métrique.

## Vérification

```bash
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m voiceagent demo --mock
```
