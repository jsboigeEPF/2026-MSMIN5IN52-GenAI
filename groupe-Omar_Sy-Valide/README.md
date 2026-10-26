# Omar Sy Valide

Un agent de code qui ne touche à rien sans ton accord. Tu lui écris une tâche, il te propose un plan, puis
une modification. À chaque étape, tu réponds **Oui**, **Non** ou **Affiner**.

## Groupe

| Membre | Contribution principale |
|---|---|
| Minh Nguyen | Socle du projet : configuration Claude Code, étude de Semantic Kernel, modèles de données et stockage SQLite, outils (worktree git, patch, sandbox), couche LLM OpenRouter et faux LLM de test, graphe et moteur d'exécution, CLI, scénarios de bout en bout, premier run réel, première version du tableau de bord. |
| Nassim Jazouli | Agent interactif Omar (interface plein écran, lancement en une commande, alias) ; validation en deux temps (plan puis écriture) ; choix du fichier par l'IA et sélecteur `@fichier` ; écriture sans commit et `/undo` ; vérification adaptée au langage du fichier ; main rendue à l'humain quand les corrections échouent ; réponses en direct (streaming) ; `/resume` avec l'historique ; refonte du tableau de bord (vue d'ensemble, frise, français) ; sécurité de l'affichage. |

## Sujet

- **Catalogue :** B1 — Processus agentique avec validation humaine.
- **Problématique :** comment rendre un agent de code fiable et contrôlable ? Les étapes de l'agent suivent
  un graphe fixe écrit en code (jamais décidé par l'IA), chaque étape est enregistrée, et rien n'est écrit
  sans validation humaine.
- **MVP :** un agent interactif dans le terminal. Il choisit le fichier à modifier, propose un plan, puis un
  diff. Une fois le diff validé, il teste la modification et, si les tests passent, écrit le fichier dans ton
  dossier (sans commit). Tout est journalisé : décisions, coût, temps.

## Architecture

```
plan ─▶ [ toi : valider le plan ] ─▶ code ─▶ [ toi : valider le diff ] ─▶ tests ─▶ écriture
          │ affiner                             │ affiner                  │ échec : correction
          └─▶ nouveau plan                      └─▶ nouveau code           └─▶ nouveau code (repasse par toi)
```

- **Graphe** (`src/process/graph.py`) : une table de transitions, seule source du routage.
- **Moteur** (`src/process/engine.py`) : exécute les étapes, enregistre l'état après chacune, s'arrête aux
  validations humaines et reprend où il en était après un arrêt.
- **IA** (`src/llm/`) : trois rôles appelés via OpenRouter et Semantic Kernel. Le *planner* fait le plan, le
  *coder* écrit le code et le *summarizer* résume les erreurs de test. Les réponses sont du JSON validé.
- **Vérification** (`src/tools/checks.py`) : les tests et le lint du projet, selon le langage du fichier
  (pytest/ruff, npm, go, cargo). Ils sont lancés dans une copie isolée du dépôt (worktree git).
- **Journal** (`src/storage/`) : base SQLite avec l'état des runs, les événements, les décisions et le coût.
  Traces OpenTelemetry.
- **Interfaces** : l'agent interactif (`src/tui/`, Textual), une CLI (`src/cli.py`) et un tableau de bord
  (`src/dashboard.py`, Streamlit). Les trois pilotent le même moteur.

Spécification complète : [docs/SPEC.md](docs/SPEC.md).

## Installation

Prérequis : Python 3.11+ et git.

```bash
cd groupe-Omar_Sy-Valide
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows : .venv\Scripts\pip install -e ".[dev]"
cp .env.example .env
```

Puis remplis `.env` (ne le commite jamais) :

```
OPENROUTER_API_KEY=ta-clé
PLANNER_MODEL=qwen/qwen3.8-flash
CODER_MODEL=deepseek/deepseek-v4.1-flash
SUMMARIZER_MODEL=z-ai/glm-5.3-flash
```

### L'alias `omar` (une seule fois)

Pour lancer l'agent depuis n'importe quel projet en tapant simplement `omar`, ajoute cette ligne à la fin de
`~/.bashrc` (ou `~/.zshrc`) en adaptant le chemin :

```bash
alias omar="python3 $HOME/chemin/vers/2026-MSMIN5IN52-GenAI/groupe-Omar_Sy-Valide/omar.py"
```

Puis recharge ton terminal :

```bash
source ~/.bashrc
```

Le venv n'a pas besoin d'être activé : `omar.py` le retrouve tout seul.

## Utilisation

Le projet à modifier doit être un dépôt git.

```bash
cd ~/mon-projet
omar                      # ou : omar ~/mon-projet
```

Dans l'agent :

| Action | Comment |
|---|---|
| Donner une tâche | Écris-la puis **Entrée**, par ex. « corrige add() pour que les tests passent ». |
| Imposer le fichier | Tape **@** : une liste des fichiers s'ouvre (↑/↓, Entrée ou Tab pour choisir). Sans @, l'IA choisit. |
| Répondre | **o** = Oui, **n** = Non, **a** = Affiner (tu écris ce qu'il faut changer). |
| Annuler la dernière écriture | `/undo` |
| Reprendre le dernier run | `/resume` (réaffiche toute la conversation) |
| Autres commandes | `/runs`, `/repo <chemin>`, `/clear`, `/help`, `/quit` (ou Ctrl+Q) |

Les réponses de l'IA s'affichent en direct pendant leur génération. La colonne de droite montre l'étape en
cours et le coût du run.

Si les tests échouent encore après 10 corrections automatiques, l'agent te rend la main : tu peux affiner,
abandonner, ou écrire le fichier quand même.

Pour imposer les commandes de test d'un projet, ajoute un `.omar.toml` à sa racine :

```toml
[checks]
cwd = "frontend"
test = "npm test"
lint = "npm run lint"
```

### Tableau de bord

```bash
streamlit run src/dashboard.py        # puis http://127.0.0.1:8501
```

La page d'accueil résume tous les runs : coût total, fichiers écrits, taux de réussite, temps passé. Choisis
un run à gauche pour voir son déroulé et, s'il attend une décision, y répondre.

### CLI

Les mêmes runs, une commande à la fois (pratique pour les scripts) :

```bash
python -m src.cli start --repo ~/mon-projet --prompt "Corrige add()"
python -m src.cli decide <run_id> --action approve --actor nassim
python -m src.cli --help
```

## Tests

```bash
pytest
ruff check src tests
pyright src
```

Les tests n'appellent jamais OpenRouter : ils utilisent un faux LLM scripté et une horloge simulée. Ils
couvrent le graphe (chaque transition), les scénarios de bout en bout (validation, refus, expiration, reprise
après crash, affinage, corrections automatiques, conflit d'écriture, budget), la vérification par langage,
le streaming, l'agent interactif piloté sans écran et le tableau de bord.

Résultat actuel : 212 tests, 208 passent. Les 4 tests Docker (optionnels) échouent sur une machine où
Docker est installé via snap, qui ne voit pas le dossier `/tmp` de l'hôte.

## Résultats et évaluation

Run réel sur un dépôt de démonstration (`calc.py` avec deux bugs), détaillé dans
[docs/phase4_live_run.md](docs/phase4_live_run.md) :

| Mesure | Valeur |
|---|---|
| Complétion | 1 / 1, après 1 correction automatique et 1 affinage humain |
| Temps de calcul moyen | plan 8,9 s, code 2,0 s, vérification 1,3 s |
| Attente humaine moyenne | 103 s, mesurée à part |
| Coût | 0,00076 € (0,008 % du budget de 10 €) |

Les runs suivants sont visibles dans le tableau de bord, avec leur statut, leur coût et leur déroulé. Un
seul run détaillé ne fait pas une mesure statistique : le taux de réussite est à confirmer sur plus de runs.

## Limites et plan B

- Un seul fichier modifié par tâche.
- Pas de commit : le fichier est écrit dans ton dossier, tu commites toi-même (`/undo` pour annuler).
- Les tests tournent sur ta machine, sans isolation réseau. `SANDBOX_MODE=docker` dans `.env` les isole.
- Seuls les modèles autorisés par la clé du cours sont utilisables.
- Le tableau de bord n'a pas de connexion : il n'écoute que sur `127.0.0.1`.
- Coût : quelques centimes par tâche, plafonné par `MAX_COST_EUR_PER_RUN` (0,50 € par défaut).
- Plan B de démonstration : la CLI avec `--mock-llm --mock-script <fichier.json>` rejoue des réponses
  scriptées sans réseau.

## Répartition du travail

Le travail a été découpé en branches, une par phase ou par fonctionnalité, chacune fusionnée par Pull Request
et relue par l'autre membre. Minh a construit le socle (phases S à 5) ; Nassim en a fait l'agent interactif
et a refondu le tableau de bord (voir le tableau du début). Chaque commit porte un trailer `Co-authored-by`
pour le coéquipier.

## Utilisation d'agents IA

Ce projet a été développé avec **Claude Code (Anthropic)** comme agent de code : code, tests et
documentation. Les commits ne portent pas de trailer Claude (règle de l'équipe, appliquée par le hook
`.githooks/commit-msg`) : cette section déclare donc la contribution de l'agent, conformément à `AGENTS.md`.
Tout ce qu'il a produit a été relu par l'équipe avant d'être commité.

## Licence et références

- Code : licence du dépôt (`LICENSE` à la racine).
- [Semantic Kernel](https://learn.microsoft.com/en-us/semantic-kernel/), [OpenRouter](https://openrouter.ai/docs),
  [Textual](https://textual.textualize.io/), [Streamlit](https://docs.streamlit.io/),
  [OpenTelemetry](https://opentelemetry.io/docs/). Inspiration : OpenCode et Claude Code.
