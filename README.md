# Serveur MCP de fichiers confiné

## Objectif du projet

Ce projet est un serveur [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
écrit en Python. Il permet à un client — ici un petit client de démonstration
branché sur Ollama — de consulter et de modifier un espace de fichiers limité à
`sandbox/`.

Le sujet consiste à construire un serveur sur des données publiques, un client
qui demande réellement l'accord de l'utilisateur avant les modifications, et
un audit des frontières d'autorité. Le serveur et le client sont locaux et
utilisent le transport MCP STDIO ; le projet n'expose pas de serveur HTTP public.

## État d'avancement

Suivi détaillé, checklist et décisions en attente : [`SUIVI.md`](SUIVI.md).

### Déjà réalisé

- Serveur MCP STDIO dans `server.py`.
- Trois ressources en lecture seule : politique, manifeste du sandbox et
  métadonnées récentes du journal d'audit.
- Cinq outils : lecture, inventaire, écriture, création de répertoire et
  déplacement. `move_file` est réversible en déplaçant l'élément vers son
  chemin d'origine, si celui-ci est libre.
- Jeu de démonstration contenant trois installations sportives de Cachan,
  extrait du jeu public Data ES. Source, attribution et licence :
  [`sandbox/data/README.md`](sandbox/data/README.md).
- Client de démonstration `demo_client.py` : il découvre les outils MCP et
  utilise un modèle Ollama local pour décider lesquels appeler.
- Avant `write_file`, `create_directory` ou `move_file`, le client affiche
  l'action proposée et attend que l'utilisateur tape exactement `OUI`. Il
  ajoute lui-même `confirmed=true` après cet accord. Un refus n'envoie pas
  l'appel de modification au serveur et arrête la demande en cours.
- Prompt système en français : chemins relatifs à `sandbox/`, lister avant de
  deviner, ne pas demander de confirmation dans la réponse. Les appels en
  lecture sont affichés avec leurs arguments.
- Validation des chemins et limites d'accès au niveau applicatif ; métadonnées
  d'audit enregistrées dans `sandbox/.mcp_audit.jsonl`.
- Tests automatisés : `python -m unittest discover -s tests -v` (29 tests,
  environ 5 s, sans Ollama). Ils couvrent le contrat du serveur, le
  consentement (avec un faux Ollama aux réponses écrites à l'avance), les
  entrées hostiles, le journal d'audit, les pannes (Ollama injoignable,
  serveur absent) et Ctrl+C.
- Vérification manuelle du client : découverte des cinq outils et appel réussi
  de `list_files`.

### À faire pour terminer l'audit demandé

- Écrire et exécuter des tests automatisés pour les contrats de toutes les
  ressources et de tous les outils.
- Tester les appels de modification sans consentement, avec `confirmed=false`
  ou sans `confirmed`, et vérifier qu'aucun fichier n'est modifié.
- Tester les entrées hostiles : `../../etc/passwd`, chemin absolu, lien
  symbolique, mauvais types, contenu trop grand et destination déjà existante.
- Tester le consentement du client : accepter une opération et vérifier le
  résultat ; la refuser et vérifier que le serveur ne reçoit aucun appel de
  modification.
- Tester les erreurs de transport : Ollama indisponible, serveur MCP arrêté ou
  erreur de démarrage ; documenter le message obtenu et la fin propre du client.
- Vérifier dans le journal d'audit les succès, refus/erreurs et les limites des
  données enregistrées.
- Décider si le barème exige exactement trois outils. Le serveur en expose
  actuellement cinq ; cela dépasse le MVP formulé comme « trois outils » et
  peut nécessiter de choisir trois outils principaux pour la présentation.

OAuth, prompts MCP et intégration Open WebUI sont des extensions facultatives,
à envisager seulement après avoir terminé les tests du MVP.

## Structure utile

```text
server.py                         Serveur MCP et contrats des outils/ressources
demo_client.py                    Client MCP interactif avec Ollama
sandbox/                          Seul espace de fichiers visible par les outils
sandbox/data/                     Échantillon public Data ES et sa provenance
sandbox/.mcp_audit.jsonl          Journal créé à l'exécution (ignoré par Git)
```

Les autres fichiers déjà présents dans `sandbox/` sont des exemples locaux de
test. Ne pas y placer de données personnelles ou confidentielles pour la démo.

## Contrat du serveur

Tous les chemins fournis aux outils sont relatifs à `sandbox/`. Il faut donc
passer `README.txt`, par exemple, et non `sandbox/README.txt`.

| Outil | Effet et limites |
|---|---|
| `read_file(path)` | Lit un fichier texte UTF-8 de 1 Mo maximum. |
| `list_files(path=".", recursive=true)` | Liste les fichiers et répertoires ; ignore les liens symboliques et s'arrête à 1 000 entrées. |
| `write_file(path, content, mode, confirmed=false)` | Écrase avec `mode="overwrite"` ou ajoute avec `mode="append"`. En append, un saut de ligne est ajouté avant le nouveau contenu si le fichier existant n'en a pas déjà un à la fin. Limite de 1 Mo. |
| `create_directory(path, confirmed=false)` | Crée le répertoire ainsi que ses parents manquants. |
| `move_file(source, destination, confirmed=false)` | Déplace ou renomme un fichier ou répertoire dans le sandbox. Refuse une destination existante ; ne la remplace pas. |

`write_file`, `create_directory` et `move_file` exigent `confirmed=true` côté
serveur, sous forme de vrai booléen JSON : `"true"`, `"yes"` ou `1` sont
refusés. Un appel refusé pour un mauvais type d'argument est rejeté par la
validation MCP avant d'atteindre l'outil ; il n'apparaît donc pas dans le
journal d'audit. Ce booléen seul ne prouve pas qu'une personne a consenti : c'est le
client de démonstration qui recueille l'accord. Un autre client MCP pourrait
envoyer directement `confirmed=true` ; le serveur ne dispose pas d'un mécanisme
d'authentification ou de consentement indépendant.

Le serveur refuse les chemins absolus, `..`, les liens symboliques, l'accès à
la racine et au journal. Il ne fournit pas d'outil de suppression. Les chemins
sont confinés par le code du serveur, mais pas par un bac à sable système : le
processus Python conserve les droits du compte qui le lance. Le serveur ne doit
donc pas être exposé tel quel à un réseau ou à un client distant.

## Ressources MCP

- `filesystem://policy` : résumé des limites et règles du serveur.
- `filesystem://manifest` : fichiers et répertoires à la racine de `sandbox/`.
- `filesystem://audit/recent` : derniers événements d'audit.

Le journal contient la date, le nom de l'outil, ses arguments bornés et le
résultat. Pour `write_file`, il enregistre la taille du contenu plutôt que son
contenu. Ce journal est local et modifiable par le compte qui exécute le
serveur ; ce n'est pas un journal inviolable.

## Données publiques

Le fichier [`sandbox/data/equipements_sportifs_cachan.json`](sandbox/data/equipements_sportifs_cachan.json)
est un petit extrait de trois installations de Cachan, sélectionné et
reformaté pour la démonstration. Il s'appuie sur le jeu national Data ES du
ministère chargé des Sports. La base complète est plus vaste et mise à jour
régulièrement ; le fichier local est un instantané préparé le 25 octobre 2026,
pas une copie actualisée automatiquement. Voir la note de provenance et la
licence dans [`sandbox/data/README.md`](sandbox/data/README.md).

## Installation et lancement

Depuis la racine du projet :

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Le serveur seul se lance avec :

```bash
python server.py
```

Il utilise STDIO et reste généralement lancé par un client MCP. Pour le
parcourir manuellement avec MCP Inspector :

```bash
mcp dev server.py
```

## Client de démonstration avec Ollama

Ollama doit être installé, son service local doit tourner, et le modèle choisi
doit avoir été téléchargé. Le client appelle `http://127.0.0.1:11434` par
défaut et lance lui-même `server.py` comme processus MCP enfant.

Le modèle par défaut est `qwen3:4b` :

```bash
ollama pull qwen3:4b
python demo_client.py
```

On peut choisir un autre modèle, par exemple `qwen3.5:4b`, ainsi :

```bash
ollama pull qwen3.5:4b
python demo_client.py --model qwen3.5:4b
```

L'adresse Ollama peut être changée avec `--ollama-url`. Pour quitter le client,
taper `/quit`. Exemples de demandes :

- « Liste les fichiers »
- « Lis `README.txt` »
- « Crée le dossier `demo`, puis écris dans `demo/bonjour.txt` le texte Bonjour »

La dernière demande entraîne une confirmation pour la création, puis une autre
pour l'écriture. Avant de taper `OUI`, vérifier le chemin, le mode d'écriture,
la source et la destination éventuelles, ainsi que tout le contenu affiché.
`read_file` et `list_files` sont en lecture seule et ne demandent pas de
confirmation.

## Démonstrations conseillées pour la soutenance

1. Montrer les trois ressources et les cinq outils découverts par Inspector ou
   le client.
2. Demander l'inventaire du sandbox et lire le jeu Data ES.
3. Demander une écriture de démonstration, relire les arguments affichés,
   accepter avec `OUI`, puis vérifier le fichier et le journal.
4. Refaire une modification et la refuser ; montrer que le fichier n'a pas
   changé et qu'aucun appel de modification n'a été envoyé.
5. Tenter un appel direct au serveur avec `confirmed=false`, puis un chemin
   hostile comme `../../etc/passwd` ; vérifier le refus et le journal.
6. Déplacer un fichier de test, puis le remettre à son emplacement d'origine
   pour montrer l'action réversible.

Ces démonstrations manuelles complètent les tests automatisés à écrire ; elles
ne les remplacent pas.
