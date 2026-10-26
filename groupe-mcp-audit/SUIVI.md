# Suivi du projet — à lire avant de reprendre

Ce fichier sert de passation entre les membres du groupe et leurs agents de
code. Il dit **où en est le projet, comment travailler dessus et quoi faire
ensuite**. Le [README](README.md) décrit le projet lui-même ; ce fichier
décrit le travail en cours.

**Règle : à la fin de chaque étape, mettre à jour la checklist (§4), le
journal (§6) et, si besoin, les décisions en attente (§5).**

## 0. Prochaine session : par où commencer

**Le rendu est fait : PR n°21 ouverte le 26/10 avant la date limite**
(https://github.com/jsboigeEPF/2026-MSMIN5IN52-GenAI/pull/21). Chaque push sur
la branche `groupe-mcp-audit` du fork met la PR à jour. Si l'enseignant l'a
déjà fusionnée, ouvrir une PR complémentaire. **Tout doit être en place avant
la soutenance du mercredi 28/10 à 13:15.**

Reste à faire, dans l'ordre :

1. B4 : démo de secours sans Ollama, c'est-à-dire un client déterministe, le
   plan B de la fiche B2 (15 min).
2. Bloc C : mesurer `mistral-nemo:12b` avec le prompt final, puis compléter
   la section Résultats du README (1 h).
3. Slides (PDF) dans `slides/`, et préparation de la soutenance (chaque
   membre doit savoir expliquer architecture, choix, mesures et limites).
4. Extensions facultatives (OAuth, prompts MCP, Open WebUI), seulement si
   tout le reste est fait.

### Où travailler (depuis le 26/10)

- **Uniquement dans le fork** `remiLesanne/2026-MSMIN5IN52-GenAI`, branche
  **`groupe-mcp-audit`**, dossier **`groupe-mcp-audit/`**. Ne rien modifier
  en dehors de ce dossier (règle de `AGENTS.md` à la racine du dépôt).
- L'ancien dépôt `GabrielC-star/filesystem_MCP_server` est archivé de fait :
  tout son historique a été importé ici. Ne plus y travailler.
- Pour un nouveau membre ou un nouvel ordinateur :
  ```bash
  git clone git@github.com:remiLesanne/2026-MSMIN5IN52-GenAI.git
  cd 2026-MSMIN5IN52-GenAI
  git checkout groupe-mcp-audit
  git remote add upstream https://github.com/jsboigeEPF/2026-MSMIN5IN52-GenAI.git
  cd groupe-mcp-audit   # puis l'installation du §2
  ```
- Avant de commencer : `git pull --rebase`. Ensuite : petits commits, puis
  `git push`. Les deux membres poussent sur la même branche ; prévenir l'autre
  de ce sur quoi on travaille pour éviter les conflits.
- Pour pousser, il faut être **collaborateur du fork** (Settings →
  Collaborators sur GitHub).

## 1. Contexte en bref

- Projet de cours (EPF, GenAI) : serveur MCP Python qui donne à un LLM un accès
  confiné au dossier `sandbox/` (5 outils, 3 ressources), plus un client de
  démo `demo_client.py` qui passe par Ollama et demande `OUI` avant toute
  modification. Détails dans le [README](README.md).
- **Date limite de la PR : lundi 26 octobre 2026, 23:59.** La PR peut être
  complétée après, mais doit être ouverte avant.
- Le rendu se fait dans le fork `remiLesanne/2026-MSMIN5IN52-GenAI`, dossier
  `groupe-mcp-audit/`, par la PR n°21 vers le dépôt du cours (voir §0).
- **Sujet : B2 « Serveur MCP métier audité »** (`docs/CATALOGUE.md` du dépôt
  du cours). MVP : 3 ressources, 3 outils dont une action réversible,
  validation des entrées, journal d'audit. Réussite : tests des contrats,
  appels non autorisés, entrées hostiles, consentement, erreurs de
  transport. Plan B : client de test déterministe. Extensions : OAuth,
  prompts MCP, Open WebUI.
- **Soutenance : mercredi 28/10/2026, 13:15-17:00, salle A202.** Quatre
  critères notés sur 10 : présentation, théorie, technique, organisation
  (répartition, **historique Git**, documentation). Points valorisés
  (`docs/EVALUATION.md`) : MVP fonctionnel, résultats mesurés,
  **comparaison à une baseline simple**, analyse honnête des échecs, tests
  reproductibles, contribution visible de chaque membre.
- `AGENTS.md` du dépôt du cours : l'historique Git fait partie du livrable,
  la contribution d'un agent de code doit être déclarée (`Co-Authored-By:`
  ou une phrase dans le README), et on ne modifie que son dossier de groupe.

## 2. Commandes

```bash
# Installation (une fois, depuis le dossier groupe-mcp-audit/)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# Tests automatisés : 29 tests, environ 5 s, sans Ollama ni GPU
# (tests/test_server.py : serveur ; tests/test_client.py : client)
python -m unittest discover -s tests -v

# Client de démo (Ollama doit tourner)
ollama pull qwen3:8b
python demo_client.py --model qwen3:8b

# Vérifier que le modèle est bien sur le GPU (colonne PROCESSOR = 100% GPU)
ollama ps
```

Machine de référence : RTX 5060 8 Go, Ollama 0.23.2, Python 3.12, `mcp` 1.30.0,
Node 24 (nécessaire pour `mcp dev server.py`, c'est-à-dire MCP Inspector).

## 3. Conventions de travail

- Messages, docs et interface en **français**. Les tests sont entièrement en
  français (noms, commentaires, variables) ; `server.py` et `demo_client.py`
  gardent leurs identifiants anglais d'origine.
- Tests avec `unittest` (bibliothèque standard), pas de dépendance ajoutée sans
  raison. Tous les tests passent par une vraie session MCP en mémoire
  (`mcp.shared.memory`) pour tester le contrat vu par un client.
- Les tests ne touchent **jamais** le vrai `sandbox/` : `server.ROOT` et
  `server.AUDIT_FILE` sont redirigés vers un dossier temporaire (voir
  `TestAvecSandbox` dans [tests/test_server.py](tests/test_server.py)).
- Tests du client ([tests/test_client.py](tests/test_client.py)) : le **faux
  Ollama** remplace `demo_client.ollama_chat` par une fonction qui renvoie des
  réponses écrites à l'avance (`appel_outil(...)`, `texte(...)`), et
  `builtins.input` est remplacé par la liste des saisies de l'utilisateur.
  Les pannes et Ctrl+C sont testés en lançant le vrai client dans un
  processus séparé.
- Piège : `pkill -f demo_client.py` tue aussi le shell dont la commande
  contient ce texte. Utiliser `pgrep -f "[d]emo_client"` avec prudence.
- Pour vérifier qu'un test n'est pas creux : désactiver volontairement la
  protection testée (par exemple `mock.patch.object(server, "_require_confirmation", ...)`)
  et vérifier que le test échoue.
- Avancer étape par étape, tester chaque changement, et **demander à
  l'utilisateur** avant tout choix de comportement (voir §5).
- Git : tout le travail se fait dans le fork, branche **`groupe-mcp-audit`**
  (voir §0). Un commit par changement logique, message en
  français préfixé `test:`, `fix:`, `feat:` ou `docs:`. La mise à jour de ce
  fichier part dans le commit du changement qu'elle décrit.
- Modèle principal : `qwen3:8b`. `mistral-nemo:12b` sert de comparaison, à
  traiter plus tard.

## 4. Checklist

### Bloc A — Tests du serveur ✅ (26/10)
- [x] Contrat des 5 outils et 3 ressources
- [x] Modifications sans consentement (`confirmed` absent ou `false`) → refus, aucun fichier modifié
- [x] Entrées hostiles : `..`, chemins absolus, `\`, octet nul, racine, journal d'audit, liens symboliques (fichier et dossier), mauvais types, contenu > 1 Mo, destination existante, dossier déplacé dans lui-même
- [x] Journal d'audit : succès et refus enregistrés, contenu jamais journalisé (seulement sa taille)

### Bloc B — Client
- [x] Prompt système en français (`SYSTEM_PROMPT` dans `demo_client.py`) : rôle, chemins relatifs, lister avant de deviner, ne pas demander de confirmation en texte
- [x] Afficher chaque appel en lecture avec ses arguments (`Appel en lecture : ...`)
- [x] Signaler une réponse vide du modèle
- [x] Un refus (ou une fin d'entrée pendant la confirmation) arrête la demande en cours
- [x] Serveur : descriptions d'outils et messages d'erreur plus explicites pour le LLM (`.` = racine, récursif par défaut, `mode` obligatoire)
- [x] Flèches et historique au clavier (`import readline`, ignoré sous Windows)
- [x] Ctrl+C : quitte immédiatement avec « Fin du client. », à l'invite comme pendant l'attente du modèle
- [x] Tests du client avec un **faux Ollama** : `OUI` → écriture reçue ; refus (`non`, `oui`, vide...) → rien reçu et modèle pas relancé ; fin d'entrée et Ctrl+C pendant la confirmation → rien reçu ; `confirmed=true` mis par le modèle ne contourne pas la confirmation ; outil inconnu, JSON invalide, réponse vide, limite de 8 appels
- [x] Tests des pannes : Ollama injoignable, serveur MCP absent → message clair, code 1, pas de traceback
- [ ] Le faux Ollama sert aussi de plan B pour la soutenance si Ollama manque

### Bloc C — Mesures avec de vrais modèles
- [ ] Même scénario sur `qwen3:8b`, puis `mistral-nemo:12b` : bon outil, bons chemins, enchaînement, langue, temps de réponse
- [ ] Reporter les résultats dans le README (section Résultats)

### Bloc D — Conformité du rendu (guide du cours)
Plan prévu :
1. Un membre forke `jsboigeEPF/2026-MSMIN5IN52-GenAI` sur GitHub (`gh` n'est
   pas installé : passer par le site) et ajoute les autres comme
   collaborateurs.
2. Cloner le fork, ajouter le remote `upstream`, créer une branche.
3. `git subtree add --prefix=groupe-<nom> <ce dépôt> master` : le projet
   arrive dans le dossier **avec tout son historique**, commits de chaque
   membre compris (critère « organisation »).
4. Dans le dossier : `.gitignore` fusionné avec celui de
   `template-groupe/`, `.env.example`, suppression d'`egg-info`, README
   réécrit selon les sections de `template-groupe/README.md`.
5. Vérifier : tests lancés depuis le dossier, diff limité au dossier, aucun
   secret ni fichier lourd.
6. Pousser, puis ouvrir la PR sur le site avec le texte préparé (modèle
   `.github/PULL_REQUEST_TEMPLATE.md` : dossier, sujet B2, membres en
   `@pseudo`, résumé, 10 cases à cocher, démonstration et plan B).
- [x] Fork `remiLesanne/2026-MSMIN5IN52-GenAI`, branche `groupe-mcp-audit`, un seul dossier `groupe-mcp-audit/` (import par `git subtree`, historique conservé)
- [x] README réécrit selon `template-groupe/README.md` : groupe, sujet B2, architecture, installation, utilisation, tests, résultats (baseline et versions successives, échecs compris), limites et plan B, répartition, agents, licences et références
- [x] Section « À faire » du README supprimée (le suivi est ici)
- [x] `.env.example` (aucune variable, expliqué en commentaire), `requirements.txt` (versions exactes)
- [x] `egg-info` retiré du suivi, `.gitignore` fusionné avec celui du modèle
- [x] Sandbox : `test1/toto.txt` supprimé, `README.txt` présente le sandbox
- [x] Usage d'agents de code déclaré dans le README (Codex et Claude Code)
- [x] Limite Windows (`os.pread`) citée dans le README
- [ ] Slides (PDF), éventuellement dans une PR complémentaire
- [x] PR n°21 ouverte et checklist remplie (26/10) : 17 commits, 13 fichiers, tous dans `groupe-mcp-audit/`

## 5. Décisions en attente (à faire trancher par l'utilisateur)

1. ~~3 ou 5 outils~~ : on garde les 5 (la fiche B2 fixe un minimum de 3).
2. **Modèle par défaut du client** : `qwen3:4b` dans `demo_client.py` et le
   README, alors que le modèle principal est `qwen3:8b`. Proposition :
   passer le défaut à `qwen3:8b`.
3. **Lenteur de Qwen3** : il « réfléchit » avant chaque réponse, et le client
   attend la réponse complète d'Ollama (`stream: False`), donc rien ne
   s'affiche pendant 30 à 60 s. Options : passer `think: false` dans la
   requête (plus rapide, peut-être moins fiable, à mesurer), ou afficher la
   réponse en streaming. Rien n'est décidé.
4. *(Mineur)* En cas de refus, les outils renvoient un texte `Erreur ...` avec
   `isError=false`, au lieu de lever une exception (`isError=true`). Les LLM le
   comprennent bien ; les tests acceptent les deux formes.

## 6. Journal

### 26/10 — session 1
- Projet installé et lancé avec `qwen3:8b` : 100 % GPU, ça fonctionne.
- Qwen3 a mis 5 appels pour « Quels équipements sportifs à Cachan ? ».
  D'après le journal d'audit, il a essayé `read_file("sports_equipment.txt")`,
  puis deux fois `list_files("/")` (refusé), avant `list_files(".")` et la
  bonne lecture. Il se corrige grâce aux messages d'erreur, mais un prompt
  système éviterait ces essais (bloc B).
- `mistral-nemo:12b` : réponse vide à « Quels fichiers sont disponibles ? »,
  puis réponse en espagnol à « qui es-tu ? ». Cause probable : aucun prompt
  système, et des appels d'outils mal formatés par ce modèle dans Ollama.
- Ctrl+C pendant la saisie n'arrête pas le client : `asyncio.run` intercepte
  le signal, `input()` continue, puis un traceback `KeyboardInterrupt` apparaît
  à la sortie suivante.
- Bloc A terminé : [tests/test_server.py](tests/test_server.py), 15 tests OK.
  Les tests détectent bien une protection désactivée (consentement ou chemins).
  A révélé la décision 1 du §5.
- Tests traduits en français à la demande de l'utilisateur.
- **Décidé : pas d'historique entre les questions dans le client.** Chaque
  question repart de zéro ; seuls les appels d'outils d'une même question
  restent en contexte (comportement actuel, `messages` réinitialisé dans
  `run_chat`). À mentionner dans les limites du README.
- **Corrigé : `confirmed` est un booléen strict** (`StrictBool` de pydantic
  dans `server.py`). Avant, `"true"`, `"yes"`, `"1"` et `1` étaient convertis
  en `true` par la validation MCP et ouvraient la modification ; le journal
  enregistrait alors `true`. Test : `test_confirmed_doit_etre_le_booleen_true`
  (il échouait avant le correctif). 16 tests OK. Le schéma vu par le LLM ne
  change pas (`boolean`).
- Limite constatée : un appel rejeté par la validation des types (mauvais
  type, argument manquant) n'atteint pas l'outil et n'est donc **pas
  journalisé**. Documenté dans le README (contrat du serveur).

### 26/10 — session 1, étape B1 (client avec Qwen)
- Méthode de mesure, sans clavier : envoyer les questions par un pipe, puis
  lire les nouvelles lignes du journal d'audit, par exemple
  `printf 'question\nnon\n/quit\n' | python demo_client.py --model qwen3:8b`.
  La ligne qui suit une question sert de réponse à la confirmation si le
  modèle propose une modification. Le modèle est aléatoire : toujours faire
  plusieurs essais.
- Ajouts : prompt système, affichage des appels en lecture, réponse vide
  signalée.
- Défauts trouvés et corrigés :
  - Après un refus, Qwen reproposait la même écriture jusqu'à la limite de 8
    appels (environ 2 min). **Désormais, un refus arrête la demande.**
  - Une fin d'entrée (`EOFError`) pendant la confirmation était traitée comme
    une erreur d'outil et la boucle continuait. Elle compte maintenant comme
    un refus.
  - Qwen demandait parfois la confirmation dans sa réponse au lieu d'appeler
    l'outil ; le prompt système le lui interdit.
  - Qwen essayait `list_files("/")` jusqu'à 4 fois et oubliait `mode`. Les
    descriptions d'outils (docstrings, que le LLM lit) et le message d'erreur
    des chemins absolus indiquent maintenant quoi faire.
  - Piège rencontré : en réécrivant la docstring de `list_files`, la mention
    « récursif par défaut » avait disparu. Qwen passait alors
    `recursive=false`, ne voyait pas `data/` et répondait « aucune donnée »
    (3 échecs sur 4). La mention a été remise. **Les docstrings des outils font
    partie du prompt : les modifier avec prudence et re-mesurer.**
- Mesures finales avec `qwen3:8b` (scénario « Écris un résumé des
  installations sportives de Cachan dans resume.txt ») :
  - refus : 4 essais sur 4 avec le chemin idéal (liste récursive → lecture →
    proposition → arrêt), 29 à 49 s ;
  - accord `OUI` : 3 essais sur 3 avec le fichier écrit et le journal
    `approved_attempt` puis `success`, 36 à 57 s ;
  - questions en lecture, `../../etc/passwd` et « qui es-tu ? » : réponses
    correctes en français. Avec le prompt système, Qwen refuse lui-même
    `../../etc/passwd` sans appeler le serveur : pour montrer le refus côté
    serveur, utiliser les tests ou MCP Inspector.
  - Avant ces changements : 5 appels dont 3 ratés pour une simple lecture.
- Questions utilisées pour les mesures, à réutiliser au bloc C :
  `Quels fichiers sont disponibles ?`, `Quelle installation a le plus
  d'équipements ?`, `Lis ../../etc/passwd`, `qui es-tu ?`, puis `Écris un
  résumé des installations sportives de Cachan dans resume.txt` avec `non`,
  puis avec `OUI` (supprimer `sandbox/resume.txt` après chaque essai).
- Limites à reporter dans le README :
  - le contexte Ollama par défaut est de **4096 tokens** (colonne `CONTEXT`
    de `ollama ps`). Au-delà, Ollama tronque le début de la conversation
    **sans prévenir**. Ce n'est pas gênant tant que chaque question repart
    de zéro, mais à surveiller si on lit de gros fichiers ;
  - pas de mémoire entre les questions (décision ci-dessus).
- Mistral n'a **pas** été re-testé avec le nouveau prompt système. D'après
  `ollama show`, `mistral-nemo:12b` gère bien les outils (`tools`), mais il
  pèse 7,1 Go pour 8 Go de mémoire vidéo : vérifier avec `ollama ps` qu'il
  est à 100 % sur le GPU (bloc C).
- Fin de session : branche poussée et fusionnée dans `master`. Prochaine
  étape : §0.

### 26/10 — session 2 : bloc D terminé, PR n°21
- Dépôt du cours cloné, remote `upstream` ; fork de `remiLesanne` en `origin`
  (SSH). Branche `groupe-mcp-audit` créée depuis `upstream/main` (5cbfabc).
- `git subtree add --prefix=groupe-mcp-audit <ancien dépôt> finalisation-projet` :
  17 commits importés, dont les 3 de @GabrielC-star.
- Vérifié depuis un environnement neuf en suivant le README (`pip install -r
  requirements.txt && pip install -e .`) : 29 tests OK ; `.venv` et
  `egg-info` bien ignorés.
- Branche poussée avec l'accord de l'utilisateur, PR ouverte par lui :
  https://github.com/jsboigeEPF/2026-MSMIN5IN52-GenAI/pull/21 (vérifiée par
  l'API : ouverte, pas en brouillon, aucun fichier hors du dossier).

### 26/10 — session 2 (matin) : bloc D, préparation
- Décisions de l'utilisateur : dossier **`groupe-mcp-audit`** ; **2 membres**
  (`@remiLesanne`, `@GabrielC-star`), donc +1 point d'ajustement ; agents
  utilisés : **Codex** et **Claude Code** ; `SUIVI.md` gardé dans le rendu ;
  **après la PR, on travaille directement dans le fork**. Le fork sera créé
  par `remiLesanne` sur le site GitHub.
- Répartition dans le README déduite de l'historique Git (premiers commits
  de @GabrielC-star : serveur, données, client, README ; ensuite @remiLesanne : audit,
  tests, correctifs, mesures). **À faire valider par le groupe.**
- Préparé dans ce dépôt avant l'import (`git subtree`) : nettoyage,
  `.gitignore`, `.env.example`, `requirements.txt`, sandbox, README.

### 26/10 — session 2 (matin) : B2 et B3
- Comportement constaté **avant** correctif, en lançant le vrai client :
  - Ollama injoignable : le message prévu (« Impossible de joindre
    Ollama… ») était noyé dans un long traceback. La session MCP (anyio)
    enveloppe l'erreur dans un `ExceptionGroup`, que
    `except (OSError, RuntimeError)` ne capturait pas ;
  - serveur MCP introuvable : traceback finissant par
    `McpError: Connection closed` ;
  - Ctrl+C à l'invite : **aucun effet**, même après Entrée.
    `asyncio.run` remplace le gestionnaire de Ctrl+C par un autre qui
    attend le prochain `await`, ce que `input()` ne fait jamais.
- Correctifs dans `demo_client.py` :
  - `root_error()` extrait l'erreur réelle du groupe. Message clair, code de
    sortie 1. Serveur absent → « le serveur MCP s'est arrêté ou n'a pas pu
    démarrer » ;
  - `signal.signal(SIGINT, default_int_handler)` au début de `main()` ;
  - Ollama est appelé directement, **sans `asyncio.to_thread`**. Sinon,
    Ctrl+C devait attendre la fin de la réponse du modèle, car l'arrêt
    d'asyncio attend les threads. Rien d'autre n'utilise la boucle pendant
    ce temps ;
  - `import readline` pour les flèches et l'historique.
- Tests : `tests/test_client.py`, 13 tests, **29 au total**, environ 5 s.
  Vérifié : avec l'ancien `demo_client.py`, les 5 tests de pannes et de
  Ctrl+C échouent.
- Vérification réelle avec `qwen3:8b` après correctif : « Quelle
  installation a le plus d'équipements ? » → liste, lecture, bonne
  réponse, 26 s.
- Remarque : `sandbox/test1/toto.txt` a été modifié à 09:59 par un essai
  manuel de l'utilisateur (ajout de « coucou » via `write_file`). Ce
  changement n'est **pas** commité ; voir le bloc D pour le sort de ce
  fichier.
