# Suivi du projet — à lire avant de reprendre

Ce fichier sert de passation entre les membres du groupe et leurs agents de
code. Il dit **où en est le projet, comment travailler dessus et quoi faire
ensuite**. Le [README](README.md) décrit le projet lui-même ; ce fichier
décrit le travail en cours.

**Règle : à la fin de chaque étape, mettre à jour la checklist (§4), le
journal (§6) et, si besoin, les décisions en attente (§5).**

## 1. Contexte en bref

- Projet de cours (EPF, GenAI) : serveur MCP Python qui donne à un LLM un accès
  confiné au dossier `sandbox/` (5 outils, 3 ressources), plus un client de
  démo `demo_client.py` qui passe par Ollama et demande `OUI` avant toute
  modification. Détails dans le [README](README.md).
- **Date limite de la PR : lundi 26 octobre 2026, 23:59.** La PR peut être
  complétée après, mais doit être ouverte avant.
- Le rendu final doit se faire dans un fork du dépôt du cours, dans un dossier
  `groupe-<nom-court>/` (voir §4, bloc D). Pour l'instant, le travail se fait
  dans ce dépôt séparé.

## 2. Commandes

```bash
# Installation (une fois)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Tests automatisés (sans Ollama, sandbox temporaire, < 1 s)
python -m unittest discover -s tests -v

# Client de démo (Ollama doit tourner)
ollama pull qwen3:8b
python demo_client.py --model qwen3:8b

# Vérifier que le modèle est bien sur le GPU (colonne PROCESSOR = 100% GPU)
ollama ps
```

Machine de référence : RTX 5060 8 Go, Ollama 0.23.2, Python 3.12, `mcp` 1.30.0.

## 3. Conventions de travail

- Messages, docs et interface en **français**. Les tests sont entièrement en
  français (noms, commentaires, variables) ; `server.py` et `demo_client.py`
  gardent leurs identifiants anglais d'origine.
- Tests avec `unittest` (bibliothèque standard), pas de dépendance ajoutée sans
  raison. Tous les tests passent par une vraie session MCP en mémoire
  (`mcp.shared.memory`) pour tester le contrat vu par un client.
- Les tests ne touchent **jamais** le vrai `sandbox/` : `server.ROOT` et
  `server.AUDIT_FILE` sont redirigés vers un dossier temporaire (voir
  `SandboxTestCase` dans [tests/test_server.py](tests/test_server.py)).
- Pour vérifier qu'un test n'est pas creux : désactiver volontairement la
  protection testée (par exemple `mock.patch.object(server, "_require_confirmation", ...)`)
  et vérifier que le test échoue.
- Avancer étape par étape, tester chaque changement, et **demander à
  l'utilisateur** avant tout choix de comportement (voir §5).
- Git : tout le travail se fait sur la branche **`finalisation-projet`**
  (partie de `master`). Un commit par changement logique, message en
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
- [ ] Flèches et historique au clavier (`import readline`)
- [ ] Ctrl+C : quitter proprement (aujourd'hui : traceback, voir §6)
- [ ] Tests du client avec un **faux Ollama** : accord `OUI` → appel envoyé ; refus → aucun appel de modification reçu par le serveur
- [ ] Tests des pannes : Ollama arrêté, serveur MCP qui ne démarre pas → message clair et sortie propre
- [ ] Le faux Ollama sert aussi de plan B pour la soutenance si Ollama manque

### Bloc C — Mesures avec de vrais modèles
- [ ] Même scénario sur `qwen3:8b`, puis `mistral-nemo:12b` : bon outil, bons chemins, enchaînement, langue, temps de réponse
- [ ] Reporter les résultats dans le README (section Résultats)

### Bloc D — Conformité du rendu (guide du cours)
- [ ] Fork de `jsboigeEPF/2026-MSMIN5IN52-GenAI`, branche de travail, un seul dossier `groupe-<nom-court>/`
- [ ] README : membres (pseudos GitHub uniquement), architecture, tests, résultats, limites, répartition du travail, licences et références
- [ ] Remplacer la section « À faire » du README par l'état final
- [ ] `.env.example` (le projet n'utilise pas de variables d'environnement : le dire dans le fichier)
- [ ] Retirer `sandbox_filesystem_mcp.egg-info/` du dépôt et l'ajouter au `.gitignore`
- [ ] Décider du sort de `sandbox/test1/toto.txt` et du contenu de test de `sandbox/README.txt`
- [ ] Slides (PDF), éventuellement dans une PR complémentaire
- [ ] Ouvrir la PR et remplir sa checklist

## 5. Décisions en attente (à faire trancher par l'utilisateur)

1. **3 ou 5 outils ?** Le sujet parle de « trois outils ». Recommandation :
   garder les 5 et présenter `read_file`, `list_files` et `write_file` comme le
   cœur.
2. *(Mineur)* En cas de refus, les outils renvoient un texte `Erreur ...` avec
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
