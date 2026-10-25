# Fabrique de Schémas Pédagogiques (Projet GenAI - C2)

## Groupe - G13

| Membre | Contribution principale |
| --- | --- |
| soso0706 | Développement des tests unitaires et intégration de la suite d'évaluation globale |
| AdamBenEPF | Conception de l'architecture du Plan A (OpenRouter / Qwen) et gestion des pipelines d'images |
| nehadyasmine-creator | Implémentation du Plan B intelligent (Groq & Pillow) et du moteur de secours local |

## Sujet

- **Identifiant du catalogue :** C2 - Pedagogical Schema Factory
- **Problématique :** Comment concevoir un système automatisé et résilient de génération de fiches et schémas pédagogiques structurés pour l'enseignement de concepts techniques, capable de surmonter les pannes d'API ou les restrictions de politiques de sécurité (guardrails) ?
- **MVP :** Un générateur Python capable de produire des fiches pédagogiques de haute qualité via OpenRouter (Plan A) ou de basculer de manière transparente sur un Plan B intelligent (Groq + Pillow) et un mode hors-ligne local.

## Architecture

Le projet repose sur un script central de génération (`src/generator.py`) et une suite d'évaluation.

Composants clés :

- **src/generator.py :** Pilote la logique de double niveau (Plan A et Plan B).
- **PIL (Pillow) :** Moteur de rendu graphique pour la mise en page structurée des fiches textuelles de secours.

Flux de données et services externes :

- **Plan A :** Requête POST vers l'API OpenRouter utilisant le modèle d'image qwen/qwen-image-3.

- **Plan B :** En cas d'échec du Plan A (limite de débit, erreur de guardrail 404), appel HTTP direct vers l'API Groq avec le modèle openai/gpt-oss-20b pour structurer le contenu pédagogique, mis en forme visuellement via Pillow.

- **Secours local :** Analyse sémantique locale par mots-clés du domaine (Droit, Maths, Code) si aucun réseau n'est disponible.

## Installation

```bash
git clone https://github.com/jsboigeEPF/2026-MSMIN5IN52-GenAI.git
cd squadz
```

Copiez `.env.example` vers `.env`, puis renseignez localement les valeurs (`OPENROUTER_API_KEY` et `GROQ_API_KEY`). Ne commitez jamais `.env`.

## Utilisation

```bash
python -m src.generator
```

### Interface Streamlit

Depuis le dossier `groupe-squadz`, installez les dépendances puis lancez :

```bash
cd groupe-squadz
pip install -r requirements.txt
streamlit run app.py
```

L'interface propose les 10 concepts du MVP et un concept personnalisé. Elle affiche immédiatement les images présentes dans `data/outputs/system/`, et le bouton **Générer / Régénérer** appelle le pipeline résilient Plan A, Plan B, puis le secours local. Une génération reste possible sans clé API grâce au rendu Pillow de secours. Le schéma affiché est accompagné de son score de lisibilité OCR, de son plan d'origine et d'un bouton de téléchargement PNG.

## Tests

```bash
python -m tests.test_single
python -m tests.run_evaluation
```

**Cas d'échec couverts :** Erreurs 404 de guardrails OpenRouter, indisponibilité temporaire des services d'images, absence totale de connexion réseau. Le système bascule automatiquement sans interruption du processus d'évaluation.

## Résultats et évaluation

- **Baseline :** Génération basique d'une image avec un texte minimaliste sans structure pédagogique avancée.

- **Protocole :** Comparaison de la qualité visuelle et de la pertinence des informations générées à travers une suite de concepts cibles.

- **Métriques :** Taux de succès de génération, temps de réponse, lisibilité et exactitude sémantique.

- **Interprétation :** Le système avancé surpasse nettement la baseline en termes de structure pédagogique et offre une résilience de 100% grâce au double plan de secours.

## Limites et plan B

- **Limites connues :** Dépendance aux quotas des fournisseurs externes (OpenRouter / Groq) et rendu textuel purement programmatique sur Pillow en mode secours.

- **Coût :** Gratuit ou faible coût selon les tokens/requêtes API utilisés.

- **Mode de démonstration de secours :** Le basculement automatique en cas de coupure réseau ou de blocage par les guardrails permet une démonstration fluide et sans accroc devant le jury.

## Répartition du travail

Le projet a été mené de manière collaborative avec une répartition agile des tâches :

- **AdanBenEPF** s'est concentré sur la configuration des appels API d'IA générative d'images et la structure globale du dépôt.

- **nehadyasmine-creator** a développé et affiné la logique du Plan B intelligent (intégration Groq & mise en page Pillow).

- **soso0706** a pris en charge les scripts de test, l'évaluation et la validation de la robustesse.

Collaboration quotidienne via GitHub et tests unitaires continus.

## Licence et références

- **Code source :** Licence MIT / Projet académique EPF (2026).

- **Modèles et données :** Utilisation des API OpenRouter (qwen/qwen-image-3) et Groq (llama3-70b-8192).

- **Références :** Documentation officielle Pillow, requêtes HTTP Python, et spécifications du module GenAI.