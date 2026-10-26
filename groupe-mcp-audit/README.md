# Serveur MCP de fichiers confiné et audité

Un serveur [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) en
Python donne à un LLM local (Ollama) un accès en lecture et en écriture à un
seul dossier, `sandbox/`. Un client de démonstration demande l'accord explicite
de l'utilisateur avant toute modification. Toutes les actions sont journalisées.

## Groupe

| Membre | Contribution principale |
|---|---|
| @GabrielC-star | Serveur MCP initial (outils, ressources, validation des chemins, journal d'audit), jeu de données Data ES et sa provenance, client de démonstration avec consentement, premier README |
| @remiLesanne | Audit et tests automatisés (29 tests), correctifs du serveur et du client (consentement strict, arrêt après refus, pannes, Ctrl+C), prompt système et mesures avec Qwen3, mise en conformité du rendu |

Groupe de deux personnes.

## Sujet

- **Identifiant du catalogue :** B2 — Serveur MCP métier audité.
- **Problématique :** un agent LLM qui agit sur des fichiers peut lire ou
  modifier plus que prévu (« excessive agency »). Comment exposer des outils
  utiles tout en bornant leurs effets, en recueillant un vrai consentement
  humain avant chaque modification, et en gardant une trace vérifiable ?
- **MVP :** 3 ressources, 5 outils (dont `move_file`, action réversible),
  validation stricte des entrées, journal d'audit, client qui exige `OUI`
  avant toute modification, et tests des contrats, des appels non autorisés,
  des entrées hostiles, du consentement et des erreurs de transport.

## Architecture

```text
 Utilisateur
   │  question en français                      ┌─────────────────────┐
   ▼                                            │  Ollama (local)     │
┌──────────────────────────┐  conversation +    │  qwen3:8b           │
│ demo_client.py           │ ─ outils MCP ────► │  choisit les outils │
│ client MCP + boucle LLM  │ ◄─ appels d'outils │  à appeler          │
│                          │                    └─────────────────────┘
│ • modification proposée ? affiche l'action, attend « OUI »
│ • OUI → ajoute confirmed=true ; refus → rien n'est envoyé, demande arrêtée
└───────────┬──────────────┘
            │ MCP sur STDIO (le client lance le serveur comme processus enfant)
            ▼
┌──────────────────────────┐        ┌─────────────────────────────────┐
│ server.py (FastMCP)      │ ─────► │ sandbox/   seul dossier visible │
│ 5 outils, 3 ressources   │        │   data/    échantillon Data ES  │
│ chemins confinés,        │        │   .mcp_audit.jsonl  journal     │
│ confirmed=true exigé     │        └─────────────────────────────────┘
└──────────────────────────┘
```

- **Le LLM** décide quoi faire, mais ne touche jamais un fichier : il ne
  produit que des demandes d'appel d'outil.
- **Le client** fait l'intermédiaire. Il est le seul à ajouter
  `confirmed=true`, et seulement après un `OUI` tapé par l'utilisateur.
- **Le serveur** est le seul à agir sur le disque. Il vérifie chaque chemin,
  refuse toute modification sans `confirmed=true` et journalise chaque appel.

Tout est local : aucun service externe, aucune clé d'API, aucun serveur HTTP
exposé.

### Contrat des outils

Tous les chemins sont relatifs à `sandbox/` : on écrit `README.txt`, pas
`sandbox/README.txt`.

| Outil | Effet et limites |
|---|---|
| `read_file(path)` | Lit un fichier texte UTF-8 de 1 Mo maximum. |
| `list_files(path=".", recursive=true)` | Liste fichiers et répertoires ; ignore les liens symboliques ; s'arrête à 1 000 entrées. |
| `write_file(path, content, mode, confirmed=false)` | `mode="overwrite"` remplace, `mode="append"` ajoute à la fin (avec un saut de ligne si nécessaire). 1 Mo maximum. |
| `create_directory(path, confirmed=false)` | Crée le répertoire et ses parents manquants. |
| `move_file(source, destination, confirmed=false)` | Déplace ou renomme dans le sandbox. Refuse une destination existante. **Réversible** : on remet l'élément à son chemin d'origine. |

- `write_file`, `create_directory` et `move_file` exigent `confirmed=true` sous
  forme de vrai booléen JSON : `"true"`, `"yes"` ou `1` sont refusés.
- Le serveur refuse les chemins absolus, `..`, `\`, l'octet nul, les liens
  symboliques (fichier ou dossier), la racine du sandbox et le journal d'audit.
- Il n'existe pas d'outil de suppression.

### Ressources MCP

- `filesystem://policy` : résumé des règles du serveur.
- `filesystem://manifest` : fichiers et dossiers à la racine de `sandbox/`.
- `filesystem://audit/recent` : derniers événements du journal d'audit.

Le journal (`sandbox/.mcp_audit.jsonl`) contient la date, l'outil, les
arguments tronqués et le résultat (`success`, `approved_attempt`,
`denied_or_error`). Pour `write_file`, il enregistre la **taille** du contenu,
jamais le contenu lui-même.

### Fichiers

```text
server.py            Serveur MCP : outils, ressources, validation, audit
demo_client.py       Client MCP interactif avec Ollama et consentement
tests/               29 tests automatisés (serveur et client)
sandbox/             Seul espace de fichiers visible par les outils
sandbox/data/        Échantillon public Data ES et sa provenance
SUIVI.md             Journal de travail : décisions, mesures, pièges rencontrés
requirements.txt     Versions exactes testées
```

## Installation

Prérequis : Python 3.12 (seule version testée ; `pyproject.toml` accepte 3.10
et plus) sous Linux ou macOS, et [Ollama](https://ollama.com/) pour le client
de démonstration.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # versions exactes testées
pip install -e .
ollama pull qwen3:8b              # environ 5 Go, pour le client de démonstration
```

Aucune variable d'environnement n'est nécessaire (voir `.env.example`).

## Utilisation

```bash
python demo_client.py --model qwen3:8b
```

Le client lance lui-même `server.py`. Sans `--model`, il utilise `qwen3:4b`.
`--ollama-url` change l'adresse d'Ollama (par défaut `http://127.0.0.1:11434`).
On quitte avec `/quit` ou Ctrl+C.

Exemples de demandes :

- « Quels fichiers sont disponibles ? »
- « Quelle installation a le plus d'équipements ? »
- « Écris un résumé des installations sportives de Cachan dans resume.txt »
  → le client affiche le chemin, le mode et tout le contenu proposé, puis
  attend `OUI`.

Pour appeler les outils directement, sans LLM, avec MCP Inspector (Node.js
requis) :

```bash
mcp dev server.py
```

### Démonstration conseillée

1. Montrer les 3 ressources et les 5 outils (Inspector ou client).
2. Poser une question sur les données : le modèle liste puis lit le fichier.
3. Demander une écriture, relire ce qui est affiché, accepter avec `OUI`, puis
   vérifier le fichier et le journal.
4. Refaire une demande d'écriture et la refuser : rien n'est envoyé au serveur
   et le journal ne contient aucun `write_file`.
5. Avec Inspector, appeler `write_file` avec `confirmed=false`, puis
   `read_file("../../etc/passwd")` : le serveur refuse les deux.
6. Déplacer un fichier, puis le remettre en place (action réversible).

## Tests

```bash
python -m unittest discover -s tests -v
```

Résultat : **29 tests réussis en environ 5 s**, sans Ollama ni GPU.

| Fichier | Ce qui est vérifié |
|---|---|
| `tests/test_server.py` (16 tests) | Contrat des 5 outils et des 3 ressources ; modifications sans `confirmed` ou avec `confirmed=false`, `"true"`, `"yes"`, `1` → refus et aucun fichier modifié ; entrées hostiles (`../../etc/passwd`, chemins absolus, `..` au milieu, `\`, octet nul, racine, journal d'audit, liens symboliques vers l'extérieur, mauvais types, contenu de plus de 1 Mo, destination existante, dossier déplacé dans lui-même) ; journal d'audit sans contenu |
| `tests/test_client.py` (13 tests) | Consentement avec un **faux Ollama** aux réponses écrites à l'avance : `OUI` → écriture reçue ; `non`, `oui`, réponse vide, fin d'entrée, Ctrl+C → le serveur ne reçoit rien ; un `confirmed=true` ajouté par le modèle ne contourne pas la confirmation ; outil inconnu, JSON invalide, réponse vide, limite de 8 appels. Pannes sur le vrai client : Ollama injoignable, serveur MCP absent → message clair et code de sortie 1 ; Ctrl+C à l'invite et pendant l'attente du modèle → arrêt immédiat |

Chaque test utilise un sandbox temporaire : le vrai `sandbox/` n'est jamais
modifié. Nous avons vérifié que les tests échouent quand on désactive la
protection qu'ils couvrent, ou quand on les lance sur l'ancien client.

## Résultats et évaluation

**Protocole.** Les mêmes questions sont envoyées au client par un pipe
(`printf 'question\nnon\n/quit\n' | python demo_client.py --model qwen3:8b`),
puis on lit les nouvelles lignes du journal d'audit. On compte les appels
d'outils, les appels ratés (refusés par le serveur), la réussite de la tâche et
le temps total. Matériel : RTX 5060 8 Go, Ollama 0.23.2, `qwen3:8b` entièrement
sur le GPU.

**Baseline.** Le client initial, sans prompt système, avec les descriptions
d'outils et les messages d'erreur d'origine.

| Version | Observation (essais) |
|---|---|
| Baseline | « Quels équipements sportifs à Cachan ? » : 5 appels dont 3 ratés (`read_file("sports_equipment.txt")` inventé, puis 2 × `list_files("/")` refusés) avant la bonne lecture (1 essai). `mistral-nemo:12b` : réponse vide, puis réponse en espagnol. |
| + prompt système | Plus d'appel raté en lecture. Pour « Écris un résumé… dans resume.txt », la première version du prompt échoue deux fois : réponse « données absentes » sans rien lister, puis demande de confirmation écrite dans la réponse au lieu d'appeler l'outil. Deux phrases ajoutées au prompt corrigent ces cas. Il reste qu'après un refus, le modèle repropose la même écriture jusqu'à la limite de 8 appels, soit environ 2 min (1 essai par variante). |
| + arrêt après refus | Écriture acceptée : 4 × `list_files("/")`, puis `write_file` sans `mode`, et la limite de 8 appels est atteinte (1 essai). |
| + descriptions et erreurs explicites, mais « récursif par défaut » retiré par erreur | Le modèle liste sans récursion, ne voit pas `data/` et répond « aucune donnée » (**3 échecs sur 4**). |
| **Version finale** | Écriture refusée : **4/4** au chemin idéal (liste → lecture → proposition → arrêt), 29 à 49 s. Écriture acceptée : **3/3** réussies, journal `approved_attempt` puis `success`, 36 à 57 s. Questions en lecture, chemin hostile et « qui es-tu ? » : réponses correctes en français. |

**Interprétation.**

- La **sécurité** ne dépend pas du modèle. Dans tous les essais, aucune
  modification n'a atteint le serveur sans `OUI` : les garanties sont dans le
  code du client et du serveur, et elles sont testées.
- La **fiabilité de l'agent** dépend surtout du texte que lit le modèle :
  prompt système, descriptions des outils, messages d'erreur. Une seule phrase
  retirée d'une description d'outil a fait passer le taux de réussite à 1 sur
  4. Ces textes font partie du prompt et doivent être re-mesurés à chaque
  modification.
- Les échantillons sont petits (1 à 4 essais par configuration) et le modèle
  est aléatoire. Les chiffres indiquent une tendance, pas une performance
  garantie.
- La comparaison avec `mistral-nemo:12b` utilisant le prompt final reste à
  mesurer.

## Limites et plan B

- **Le consentement repose sur le client.** `confirmed=true` ne prouve pas
  qu'un humain a accepté : un autre client MCP peut l'envoyer directement. Le
  serveur n'a ni authentification ni consentement indépendant (OAuth serait
  une extension).
- **Le confinement est applicatif, pas système.** Le processus garde les droits
  du compte qui le lance. Les vérifications de chemin et l'ouverture du
  fichier ne sont pas atomiques : une course est théoriquement possible si un
  autre processus modifie le sandbox au même moment. Le serveur ne doit pas
  être exposé à un réseau.
- **Le journal d'audit n'est pas inviolable.** C'est un fichier local,
  modifiable. Les appels rejetés par la validation des types (mauvais type,
  argument manquant) n'atteignent pas l'outil et ne sont donc pas journalisés.
- **Pas de mémoire entre les questions**, par choix : chaque question repart de
  zéro. Le contexte Ollama par défaut est de 4096 tokens et Ollama tronque le
  début de la conversation sans prévenir s'il est dépassé.
- **Lenteur.** Qwen3 « réfléchit » avant de répondre et le client n'affiche
  rien avant la réponse complète : comptez 30 à 60 s par demande.
- **Plateforme.** Le mode `append` utilise `os.pread`, absent sous Windows : le
  serveur est prévu pour Linux et macOS.
- **Données.** L'échantillon Data ES est un instantané statique de 3
  installations.
- **Comportement du modèle.** Avec le prompt système, Qwen refuse souvent
  lui-même un chemin hostile sans appeler le serveur. Pour montrer le refus côté
  serveur, il faut passer par les tests ou par MCP Inspector.

**Plan B** si Ollama, le GPU ou le réseau manquent :

1. Les tests automatisés tournent sans Ollama ni GPU et rejouent le
   consentement avec un faux Ollama déterministe.
2. MCP Inspector permet d'appeler chaque outil à la main, sans LLM.

## Répartition du travail

- **@GabrielC-star** a conçu et écrit le serveur, le jeu de données, le client de
  démonstration et le premier README.
- **@remiLesanne** a mené l'audit : tests automatisés, correctifs de sécurité et
  de robustesse, mesures avec Qwen3 et mise en conformité du rendu.

Le travail s'est fait sur un dépôt Git commun. Le fichier [`SUIVI.md`](SUIVI.md)
servait de passation entre les membres et leurs agents de code : checklist,
décisions, mesures et journal de chaque session.

**Usage d'agents de code.** Le groupe a utilisé **Codex** et **Claude Code**
pour écrire du code, des tests et de la documentation. Les commits produits avec
Claude Code portent la mention `Co-Authored-By`. Tout le code a été relu, testé
et peut être expliqué par le groupe.

## Licence et références

**Licences**

- Code : licence MIT du dépôt du cours.
- Données : extrait de Data ES (ministère chargé des Sports), [Licence Ouverte
  / Open Licence 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/).
  Provenance détaillée dans [`sandbox/data/README.md`](sandbox/data/README.md).
- Bibliothèques : [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
  (MIT), pydantic (MIT), anyio (MIT).
- Modèles et moteur : [Ollama](https://github.com/ollama/ollama) (MIT), Qwen3
  (Apache 2.0), Mistral NeMo (Apache 2.0).

**Références**

- [Spécification MCP (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18)
- [OWASP Top 10 for LLM — LLM06 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
- [Data ES — Recensement des équipements sportifs et lieux de pratique](https://www.data.gouv.fr/datasets/data-es-recensement-des-equipements-sportifs-et-lieux-de-pratique-complet-1)
- CoursIA : [Auditer un serveur MCP](https://github.com/jsboige/CoursIA/blob/main/MyIA.AI.Notebooks/GenAI/Plateformes-Conversationnelles/AI-Engine-WordPress/03-Functional/03-4-MCP-Server/auditer-un-serveur-mcp.ipynb),
  [Semantic Kernel MCP](https://github.com/jsboige/CoursIA/blob/main/MyIA.AI.Notebooks/GenAI/SemanticKernel/08-SemanticKernel-MCP.ipynb)
