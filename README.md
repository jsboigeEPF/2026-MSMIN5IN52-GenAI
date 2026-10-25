# Serveur MCP filesystem confiné

Ce serveur expose cinq outils MCP qui travaillent uniquement dans `sandbox/`,
un sous-répertoire de ce projet :

- `read_file(path)` lit un fichier texte UTF-8.
- `list_files(path=".", recursive=true)` liste les fichiers et répertoires, récursivement par défaut.
- `write_file(path, content, mode, confirmed)` crée/remplace (`mode="overwrite"`) ou ajoute à la fin (`mode="append"`).
- `create_directory(path, confirmed)` crée le répertoire et ses parents manquants.
- `move_file(source, destination, confirmed)` déplace ou renomme un fichier ou répertoire.

Les chemins sont relatifs à `sandbox/`. Les chemins absolus, `..`, les liens
symboliques, les fichiers de plus de 1 Mo et les destinations déjà existantes
pour `move_file` sont refusés. Pour `write_file`, choisis `mode="overwrite"`
pour remplacer le contenu ou `mode="append"` pour l'ajouter à la fin. Les trois
outils qui modifient le sandbox exigent `confirmed=true`.
`list_files` ignore les liens symboliques et limite sa réponse à 1 000 entrées.

Le paramètre `confirmed` est une barrière côté serveur, mais ne prouve pas à lui
seul qu'une personne a donné son accord. Le client MCP utilisé pour la démo doit
demander une confirmation explicite à l'utilisateur avant d'envoyer une
opération d'écriture, de création de répertoire ou de déplacement.

Chaque appel d'outil est inscrit dans `sandbox/.mcp_audit.jsonl`. Le journal
contient les chemins, le résultat et l'heure, jamais le contenu des fichiers.
Trois ressources en lecture seule sont exposées : la politique, le manifeste
du sandbox et les événements récents du journal.

## Lancer

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python server.py
```

Pour l'essayer avec MCP Inspector :

```bash
mcp dev server.py
```

## Client de démonstration avec Ollama

Le client `demo_client.py` utilise un modèle Ollama local pour choisir les
outils MCP. Il démarre lui-même `server.py` en transport STDIO et découvre ses
outils. Ollama doit être lancé sur la machine et le modèle doit être disponible :

```bash
ollama pull qwen3:4b
python demo_client.py
```

Le modèle par défaut est `qwen3:4b`. On peut en choisir un autre avec
`python demo_client.py --model nom-du-modele`. Le client appelle l'API Ollama
sur `http://127.0.0.1:11434` ; l'option `--ollama-url` permet de changer cette
adresse.

Pour `write_file`, `create_directory` et `move_file`, le client affiche les
arguments proposés, puis attend que l'utilisateur tape exactement `OUI`.
Toute autre réponse annule l'appel. Le client ajoute lui-même
`confirmed=true` seulement après cet accord. Le modèle ne peut donc pas
autoriser sa propre opération. `read_file` et `list_files` ne demandent pas de
confirmation.

L'accord porte sur les arguments affichés ; relis notamment le chemin, le mode
d'écriture et le contenu avant de taper `OUI`. Le modèle peut se tromper dans
le choix d'un outil ou dans ses arguments. Écris `/quit` pour arrêter le client.

Les chemins de test sont par exemple `README.txt` ou `notes/nouvelle-note.txt`.
`write_file` peut créer les répertoires parents après leur création par
`create_directory`. Un essai avec `../../etc/passwd` doit être refusé et consigné
dans le journal.

## Jeu de données public de démonstration

Un petit extrait local de trois installations sportives de Cachan est disponible
dans `sandbox/data/equipements_sportifs_cachan.json`. Il provient du jeu Data ES
du ministère chargé des Sports, sous Licence Ouverte 2.0. Les détails de source,
d'attribution et de périmètre sont indiqués dans
[`sandbox/data/README.md`](sandbox/data/README.md). Le snapshot est volontairement
petit et ne remplace pas le jeu national ni son API.
