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
cp .env.example .env               # puis renseigner les valeurs localement ; ne jamais commiter .env
```

Le fichier de dépendances (`pyproject.toml`) arrive en phase 1.

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

```bash
python -m src.cli --help   # disponible à partir de la phase 4
```

## Tests

```bash
pytest
```

Les tests (outils, table de transitions, 9 scénarios en mode mock) arrivent à partir de la phase 1.

## Résultats et évaluation

À compléter en phase 4 (rapport `generate_evaluation_report` : taux de complétion, temps de calcul moyen/P95
par étape et par modèle, itérations, coût total face au budget de 10 EUR).

## Limites et plan B

À compléter. Plan B prévu : mode `--mock-llm` (zéro appel réseau) pour la démonstration ; moteur maison si le
SK Process Framework ne permet pas la reprise à une étape donnée (phase 0).

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
