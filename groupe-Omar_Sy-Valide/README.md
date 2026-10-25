# Mini-OpenCode — processus agentique de code avec validation humaine

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
- **MVP :** un agent qui planifie, génère un patch sur un fichier cible dans un worktree git isolé, le vérifie
  (pytest + ruff en sandbox), le soumet à un humain (approuver / réviser / rejeter, avec expiration), puis le
  commite ou le compense (suppression du worktree, `git revert`). Journal d'exécution SQLite, traces
  OpenTelemetry, coût suivi par run, mode `--mock-llm` sans réseau.

La spécification complète est dans [docs/SPEC.md](docs/SPEC.md).

## Architecture

Graphe : `plan → code → verify → approval → finalize`, avec boucle d'auto-correction (verify → code), boucle de
révision humaine (approval → plan) et compensation terminale. Détail dans [docs/SPEC.md](docs/SPEC.md) §5.

Moteur : boucle asynchrone maison pilotée par la table de transitions. Le SK Process Framework a été évalué en
phase 0 ([docs/phase0_sk_spike.md](docs/phase0_sk_spike.md)) : suspension et reprise possibles, mais sans
point de sauvegarde par étape et avec des exceptions d'étape silencieusement absorbées. Semantic Kernel reste
utilisé pour les services LLM et les filtres de traçage.

## Installation

Prérequis : Python 3.11+, git, Docker (optionnel, sandbox), Node.js (pour pyright).

```bash
cd groupe-Omar_Sy-Valide
python -m venv .venv
# Windows : .venv\Scripts\activate    —    Linux/macOS : source .venv/bin/activate
pip install -e ".[dev]"            # versions figées dans pyproject.toml
cp .env.example .env               # puis renseigner les valeurs localement ; ne jamais commiter .env
```

Les valeurs vides de `.env` reprennent les valeurs par défaut de `src/config.py`.

Sandbox Docker (requise par défaut : `SANDBOX_MODE=auto` refuse de s'exécuter sans Docker et sans l'image,
plutôt que de lancer le code généré sur la machine hôte) :

```bash
docker build -t mini-opencode-sandbox sandbox/   # pytest + ruff, exécuté avec --network none
```

Sans Docker, `SANDBOX_MODE=subprocess` dans `.env` exécute pytest et ruff sur l'hôte (environnement sans
secrets, délai limité) : c'est un choix explicite, signalé par un avertissement à chaque exécution.

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

## Utilisation

Depuis `groupe-Omar_Sy-Valide/`, sur un dépôt git cible dont la branche de base est extraite :

```bash
python -m src.cli start --repo ../chemin/depot --file calc.py --prompt "Corrige add()" --scenario demo
python -m src.cli status <run_id>        # plan, diff, vérification, compteurs, coût
python -m src.cli decide <run_id> --action approve --actor alice
python -m src.cli decide <run_id> --action revise --actor alice --comment "Retire le shebang"
python -m src.cli decide <run_id> --action reject --actor alice
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
streamlit run src/dashboard.py
```

Liste des runs, graphe du processus tracé depuis la table de transitions (étape courante surlignée, étapes
parcourues en bleu, issue finale en vert ou rouge), plan et diff, sortie de vérification, journal
d'exécution, historique des décisions, coût et temps (calcul et attente humaine séparés). Les boutons
**Approuver / Réviser / Rejeter** enregistrent la décision avec le nom de l'évaluateur ; « Approuver » est
désactivé tant que la vérification n'a pas réussi. Chaque rafraîchissement expire les approbations
dépassées.

Modèles utilisés pour le run réel (à renseigner dans `.env`, autorisés par la clé du cours) :
`PLANNER_MODEL=qwen/qwen3.8-flash`, `CODER_MODEL=deepseek/deepseek-v4.1-flash`,
`SUMMARIZER_MODEL=z-ai/glm-5.3-flash`.

## Tests

```bash
pytest
ruff check src tests
pyright src      # mode strict
```

140 tests, tous sans réseau ni attente réelle (faux LLM scripté, `FakeClock`) :

- **9 scénarios de bout en bout** (`tests/test_scenarios.py`, SPEC §8) : nominal, rejet, expiration, reprise
  en attente d'approbation, crash au milieu d'une étape (réponse LLM rejouée depuis le cache), boucle de
  révision, boucle d'auto-correction, compensation post-commit (`git revert`), plafond de budget.
- **Table de transitions** : chaque route de la spec, gardes exhaustives et exclusives, atteignabilité.
- **Moteur, CLI, couche LLM, outils, stockage, télémétrie** : y compris la requête OpenRouter vérifiée via un
  transport HTTP simulé, et la sandbox Docker réelle (réseau coupé, montage en lecture seule).
- **Tableau de bord** : graphe et règles d'affichage testés comme fonctions pures, puis la page Streamlit
  elle-même pilotée sans navigateur (`AppTest`) : sélection d'un run, clic sur « Approuver », vérification
  du merge.
- Un test par mutation a vérifié que le scénario 5 échoue si le cache LLM est désactivé.

Les tests Docker sont ignorés si l'image `mini-opencode-sandbox` n'est pas construite.

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

- **Un seul fichier cible par run**, et la branche de base doit être extraite dans le dépôt de l'utilisateur
  (sinon le run se termine en `commit_failed`, sans changer de branche à sa place).
- **`MAX_FIX` couvre tout le run** : il n'est pas remis à zéro après une révision humaine.
- **Modèles limités par la clé du cours** (8 modèles autorisés) ; un modèle refusé laisse le run reprenable.
- **Sandbox** : Docker requis par défaut ; `SANDBOX_MODE=subprocess` exécute le code généré sur l'hôte et
  n'est qu'un choix explicite (avertissement à chaque exécution).
- **Plan B de démonstration** : mode `--mock-llm` (aucun appel réseau, comportement déterministe).

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
