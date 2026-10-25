# Omar Sy Valide — agent de code interactif avec validation humaine

## Groupe

| Membre | Contribution principale |
|---|---|
| Nassim Jazouli | À compléter au fil des phases |
| Minh Nguyen | Mise en place du projet (phase S), à compléter au fil des phases |

## Sujet

- **Identifiant du catalogue :** B1 — Processus agentique avec validation humaine
- **Problématique :** comment rendre un agent de code fiable et auditable ? Le graphe d'exécution est explicite
  et déterministe (les transitions sont du code, jamais une décision de LLM), l'état est persisté à chaque étape,
  et aucune modification n'atteint la branche principale sans validation humaine explicite.
- **MVP :** un agent interactif plein écran (Textual), dans l'esprit d'OpenCode. On lui écrit une tâche, il
  choisit le fichier à modifier et propose un plan, que l'humain valide (**Oui / Non / Affiner**). Il propose
  ensuite un diff, validé de la même façon. Une fois le diff approuvé, le fichier est écrit dans un worktree
  git isolé, où pytest et ruff le vérifient. Si les tests passent, le fichier est copié dans le dossier de
  l'utilisateur, **sans commit** (`/undo` pour annuler). Journal d'exécution SQLite, traces OpenTelemetry,
  coût suivi par run.

La spécification complète est dans [docs/SPEC.md](docs/SPEC.md).

## Architecture

Graphe : `plan → plan_approval → code → write_approval → verify → finalize`. Il comporte deux portes
humaines et trois boucles : affiner le plan (plan_approval → plan), affiner le diff (write_approval → code)
et l'auto-correction (verify → code, dont la correction repasse par la validation de l'écriture). Après
10 corrections automatiques sans succès, la main revient à l'humain avec l'erreur : affiner (10 nouveaux
essais), abandonner, ou écrire quand même (issue `written_despite_failing_tests`). Toutes les autres issues
mènent à une compensation (suppression du worktree).

**Vérification juste.** Un dépôt qui n'a aucun fichier de test passe (« aucun test ») ; un dépôt qui en avait et n'en collecte plus
échoue. ruff applique la configuration du projet
cible, mais seules les violations introduites par le changement comptent : l'IA n'est pas pénalisée pour du
code qui existait avant. Détail dans [docs/SPEC.md](docs/SPEC.md) §5.

**Pourquoi plus de Docker.** Docker servait à exécuter le code généré *avant* que l'humain l'ait vu. Le diff
est désormais validé avant toute écriture ou exécution : aucun code non relu ne tourne. Les tests sont donc
lancés en local, dans le worktree du run, avec un environnement sans secrets (pas de clé d'API) et une
limite de temps. Ce sont ceux du dépôt cible, c'est-à-dire ce que l'utilisateur lancerait lui-même avec
`pytest`. `SANDBOX_MODE=docker` reste disponible pour isoler aussi le réseau et le système de fichiers.

Moteur : boucle asynchrone maison pilotée par la table de transitions. Le SK Process Framework a été évalué en
phase 0 ([docs/phase0_sk_spike.md](docs/phase0_sk_spike.md)) : suspension et reprise possibles, mais sans
point de sauvegarde par étape et avec des exceptions d'étape silencieusement absorbées. Semantic Kernel reste
utilisé pour les services LLM et les filtres de traçage.

## Installation

Prérequis : Python 3.11+ et git. Docker et Node.js (pour pyright) sont optionnels.

```bash
cd groupe-Omar_Sy-Valide
python -m venv .venv
.venv/bin/pip install -e ".[dev]"   # Windows : .venv\Scripts\pip ; versions figées dans pyproject.toml
cp .env.example .env                # puis renseigner la clé et les modèles ; ne jamais commiter .env
```

Le `.env` est lu depuis ce dossier, quel que soit le dépôt où l'agent est lancé. Ses valeurs vides reprennent
les valeurs par défaut de `src/config.py`.

### Hook de commit (chaque membre, une fois)

Depuis la racine du dépôt :

```bash
git config core.hooksPath groupe-Omar_Sy-Valide/.githooks
```

Le hook refuse un commit sans trailer `Co-authored-by:` pour le coéquipier, ou avec un trailer Claude/Anthropic.

### Claude Code (chaque membre, une fois)

Claude Code est lancé depuis la **racine du dépôt**. Le `.gitignore` du dépôt de cours ignore `.claude/` à la
racine : la configuration partagée est donc versionnée ici, dans `.claude/settings.json`, et chaque membre la
copie à la racine.

```bash
# depuis la racine du dépôt
mkdir -p .claude && cp groupe-Omar_Sy-Valide/.claude/settings.json .claude/settings.json
npm install -g pyright
claude plugin install superpowers@claude-plugins-official --scope project
claude plugin install pyright-lsp@claude-plugins-official --scope project
claude plugin install context7@claude-plugins-official --scope project
claude plugin install security-guidance@claude-plugins-official --scope project
claude plugin install code-review@claude-plugins-official --scope project
claude plugin install commit-commands@claude-plugins-official --scope project
```

Puis `/reload-plugins` dans Claude Code (ou redémarrage).

## Lancer Omar Sy Valide

```bash
python omar.py ../../demo-calc          # le dépôt à modifier (défaut : dossier courant)
```

Le venv n'a pas besoin d'être activé : `omar.py` se relance de lui-même avec `.venv/`. Une fois le projet
installé, la commande `omar` fait la même chose depuis n'importe quel dépôt.

Dans l'interface :
- écris une tâche, par exemple « corrige add() et mean() pour que les tests passent », puis Entrée ;
- à chaque validation, **o** = Oui, **n** = Non, **a** = Affiner (tu écris ce qu'il faut changer, Entrée) ;
- `/runs`, `/resume <id>`, `/undo`, `/repo <chemin>`, `/clear`, `/help`, `/quit` (ou Ctrl+Q).

La colonne de droite suit le graphe en direct : étape courante, étapes passées, boucles, compteurs et coût.
Chaque décision est enregistrée avec ton login comme évaluateur. L'affichage se remet en page quand on
redimensionne le terminal ; les journaux des bibliothèques vont dans `omar.log` et pas à l'écran.

**Sécurité de l'affichage.** Le diff est le seul garde-fou avant l'exécution du code. Une réponse du modèle
qui contient des caractères de contrôle (par exemple `ESC[2K`, qui efface une ligne à l'écran et pourrait
cacher du code au relecteur) est donc refusée comme sortie invalide. Tout texte venant du modèle ou du dépôt
est affiché avec ces caractères rendus visibles (`\x1b`), jamais envoyés au terminal.

## CLI (mêmes runs, une commande à la fois)

Depuis `groupe-Omar_Sy-Valide/` :

```bash
python -m src.cli start --repo ../chemin/depot --prompt "Corrige add()" --scenario demo   # --file pour imposer le fichier
python -m src.cli status <run_id>        # plan, diff, vérification, compteurs, coût
python -m src.cli decide <run_id> --action approve --actor alice   # porte en attente : plan ou écriture
python -m src.cli decide <run_id> --action revise --actor alice --comment "Garde les annotations"
python -m src.cli decide <run_id> --action reject --actor alice
python -m src.cli decide <run_id> --action write_anyway --actor alice   # seulement après 10 corrections ratées
python -m src.cli undo <run_id>          # restaure le fichier écrit par un run terminé
python -m src.cli resume <run_id>        # reprise après un crash ou une erreur fournisseur
python -m src.cli sweep                  # expire les approbations dépassées
python -m src.cli runs                   # liste des runs
python -m src.cli report                 # rapport d'évaluation (JSON)
python -m src.cli credits                # consommation de la clé OpenRouter (la clé n'est jamais affichée)
```

`--mock-llm --mock-script script.json` remplace OpenRouter par le faux LLM scripté (aucun appel réseau) ;
`--trace` affiche les spans OpenTelemetry (export OTLP si `OTEL_EXPORTER_OTLP_ENDPOINT` est défini).

### Tableau de bord

```bash
streamlit run src/dashboard.py     # http://127.0.0.1:8501
```

- **Runs reconnaissables** : « 25/10 19:35 · cuisine_rag · début de la tâche · Échoué » au lieu d'un
  identifiant, avec des filtres par projet et par statut (en français).
- **Un run en un coup d'œil** : la tâche en titre, le statut expliqué en une phrase (ce qui s'est passé, et
  si ton dossier a été modifié), puis le coût, le temps machine et l'attente humaine.
- **Déroulé** : une frise lisible (tâche reçue, plan proposé, ta décision, code proposé, tests, fichier
  écrit…), avec le plan, le diff ou la sortie des tests dépliables.
- **Décider** : sur un run en attente, les mêmes réponses que dans l'agent (Oui, Non, Affiner, et
  « Écrire quand même » après 10 corrections ratées). Ton login est prérempli.
- Les onglets « Graphe » (tracé depuis la table de transitions) et « Données brutes » (journal, décisions,
  mesures, identifiant) gardent la vue technique. Chaque rafraîchissement expire les validations dépassées.

Modèles utilisés pour le run réel (à renseigner dans `.env`, autorisés par la clé du cours) :
`PLANNER_MODEL=qwen/qwen3.8-flash`, `CODER_MODEL=deepseek/deepseek-v4.1-flash`,
`SUMMARIZER_MODEL=z-ai/glm-5.3-flash`.

## Tests

```bash
pytest
ruff check src tests
pyright src      # mode strict
```

190 tests, tous sans réseau ni attente réelle (faux LLM scripté, `FakeClock`) :

- **Scénarios de bout en bout** (`tests/test_scenarios.py`, SPEC §8) : nominal (fichier choisi par l'IA),
  rejet à chaque porte, expiration, reprise en attente de validation, crash au milieu d'une étape (réponse
  LLM rejouée depuis le cache), affinage du plan puis du diff, auto-correction, conflit d'écriture, plafond
  de budget.
- **Agent interactif** (`tests/test_tui.py`) : l'interface Textual pilotée sans écran (`Pilot`). Tâche,
  plan, Oui, diff, Oui, fichier écrit sans commit ; Affiner ; Non ; `/undo` ; `/runs` ; `/resume` ; refus
  du dépôt de l'agent lui-même.
- **Table de transitions** : chaque route de la spec, gardes exhaustives et exclusives, atteignabilité.
- **Moteur, CLI, couche LLM, outils, stockage, télémétrie** : y compris la requête OpenRouter vérifiée via un
  transport HTTP simulé, et la sandbox Docker réelle (réseau coupé, montage en lecture seule).
- **Tableau de bord** : graphe et règles d'affichage testés comme fonctions pures, puis la page Streamlit
  elle-même pilotée sans navigateur (`AppTest`) : sélection d'un run, clic sur « Approuver », vérification
  du merge.
- Un test par mutation a vérifié que le scénario 5 échoue si le cache LLM est désactivé.

Les tests Docker sont ignorés si l'image `mini-opencode-sandbox` n'est pas construite. Avec un Docker
installé via snap, ils échouent (déjà le cas avant ce changement) : le confinement snap ne voit pas le `/tmp`
de l'hôte, où pytest crée ses dépôts de test.

## Résultats et évaluation

Run réel sur un dépôt de démonstration (`calc.py` avec deux bugs), détaillé dans
[docs/phase4_live_run.md](docs/phase4_live_run.md) :

| Mesure | Valeur |
|---|---|
| Complétion | 1 / 1 (COMPLETED après 1 auto-correction et 1 révision humaine) |
| Temps de calcul moyen (P95) | plan 8,9 s (11,7 s), code 2,0 s (3,0 s), vérification 1,3 s (1,9 s) |
| Attente humaine moyenne | 103 s, comptée à part du temps de calcul |
| Coût | 0,000829 USD, soit 0,00076 EUR (0,008 % du budget de 10 EUR) |

Ce run a révélé deux défauts corrigés dans la phase 4 : un faux positif du linter dans la sandbox Docker
(règle EXE002 déclenchée par les permissions du montage Windows, que le modèle « corrigeait » en ajoutant un
shebang) et des erreurs fournisseur affichées sous forme de trace complète. Un seul run réel ne constitue pas
une mesure statistique : les chiffres de taux de complétion sont à compléter par d'autres runs.

## Limites et plan B

- **Un seul fichier cible par run**, choisi par l'IA (ou imposé avec `--file` en CLI).
- **Pas de commit** : le fichier est écrit dans le dossier de travail, et le commit reste à la main de
  l'utilisateur. Si le fichier est modifié pendant le run, rien n'est écrasé (`write_failed`).
- **`MAX_FIX` (10 par défaut)** est remis à zéro quand l'humain affine le diff. `MAX_REVISIONS` (5 par
  défaut) est partagé par les deux portes. Une valeur dans `.env` remplace ces défauts.
- **Modèles limités par la clé du cours** (8 modèles autorisés) ; un modèle refusé laisse le run reprenable.
- **Exécution locale par défaut** : seul du code relu et approuvé tourne, mais sans isolation réseau ni
  disque. `SANDBOX_MODE=docker` ajoute cette isolation.
- **Tableau de bord sans authentification** : outil local mono-utilisateur. `.streamlit/config.toml` le limite
  à `127.0.0.1` ; le nom de l'évaluateur est déclaratif. Il ne doit pas être exposé sur un réseau sans un
  proxy authentifiant devant lui. Les textes issus du LLM et des outils sont affichés échappés (pas de
  Markdown actif, donc pas d'image ni de lien injectés).
- **Faux LLM réservé aux tests** : `pytest` n'appelle jamais OpenRouter. L'agent interactif utilise
  toujours les vrais modèles du `.env`.

## Répartition du travail

Une branche par phase, fusionnée par Pull Request relue par l'autre membre. Chaque commit porte un trailer
`Co-authored-by:` pour le coéquipier. À compléter au fil des phases.

## Utilisation d'agents IA

Ce projet est développé avec l'assistance de **Claude Code (Anthropic)**, utilisé comme agent de code pour la
mise en place du projet, la rédaction de code, de tests et de documentation. Les commits ne portent pas de
trailer Claude (choix de l'équipe, imposé par le hook de commit) : cette section tient lieu de déclaration de
la contribution de l'agent, conformément à `AGENTS.md`. Tout code produit par l'agent est relu par l'équipe
avant commit.

## Licence et références

- Code : licence du dépôt (voir `LICENSE` à la racine).
- Références : [Semantic Kernel — Process Framework](https://learn.microsoft.com/en-us/semantic-kernel/frameworks/process/process-framework),
  [OpenRouter](https://openrouter.ai/docs), [OpenTelemetry](https://opentelemetry.io/docs/), OpenCode (inspiration).
