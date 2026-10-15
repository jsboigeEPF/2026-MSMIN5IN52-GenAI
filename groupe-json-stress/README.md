# A3 — JSON Stress

**Cours :** MSMIN5IN52 — IA générative et chatbots  
**Établissement :** EPF  
**Identifiant du sujet :** A3 — Contrats JSON sous stress  
**Statut :** MVP en cours de développement

## 1. Groupe

| Membre | Contribution principale |
|---|---|
| À compléter | Conception des JSON Schemas, validation et tests |
| À compléter | Générateur, gestion des erreurs et stratégie de reprise |
| À compléter | Interface en ligne de commande, évaluation et rapport |

Tous les membres participent à l'intégration, à la revue de code, aux tests de bout en bout et à la préparation de la soutenance.

## 2. Sujet et problématique

### Sujet

Contrats JSON sous stress : évaluer la fiabilité des sorties JSON produites par les modèles de langage et mesurer l'efficacité des mécanismes de validation et de reprise.

### Problématique

Un modèle de langage peut produire un JSON syntaxiquement invalide, omettre un champ obligatoire, utiliser un type incorrect ou enfreindre une contrainte métier. Ces erreurs peuvent empêcher l'intégration de ses réponses dans une application.

Ce projet vise à répondre aux questions suivantes :

- Quel est le taux de conformité initial des sorties générées ?
- Comment la complexité du JSON Schema affecte-t-elle ce taux ?
- Dans quelle mesure une stratégie de validation et de reprise améliore-t-elle la conformité finale ?
- Quel est le coût de cette amélioration en nombre d'appels, en latence et en consommation d'API ?
- Comment le système réagit-il aux erreurs persistantes, aux timeouts et aux indisponibilités de service ?

### MVP

Le MVP comprend :

1. Cinq JSON Schemas de difficulté croissante.
2. Un validateur syntaxique et structurel.
3. Un générateur utilisant d'abord un modèle simulé, puis un modèle réel si l'API est disponible.
4. Une stratégie de reprise avec un nombre maximal de tentatives.
5. Une interface en ligne de commande.
6. Des tests automatisés exécutables sans API externe.
7. Une campagne d'évaluation d'au moins 200 générations.
8. Un rapport présentant les métriques et la comparaison avec une baseline.

## 3. Architecture

Le projet utilise Python et sépare la génération, la validation, la reprise et l'évaluation.

### 3.1. Schémas

Cinq schémas couvrent des contraintes de complexité croissante :

- **S1 :** objet simple, types et champs obligatoires.
- **S2 :** objets imbriqués et tableaux.
- **S3 :** énumérations et contraintes conditionnelles.
- **S4 :** structures complexes et contraintes sur les éléments.
- **S5 :** contraintes croisées et règles métier complémentaires.

Les schémas définitifs et leurs critères de validation seront documentés dans le dépôt.

### 3.2. Générateur

Le générateur reçoit une entrée et un schéma, puis produit une réponse JSON sous forme de texte.

Une interface commune permettra d'utiliser :
- un générateur simulé pour les tests déterministes ;
- un adaptateur pour le modèle réel lorsque l'API sera choisie.

Le modèle, ses paramètres et les informations disponibles sur les coûts seront enregistrés pour chaque expérience.

### 3.3. Validateur

Le validateur effectue deux contrôles distincts :

1. Vérifier que le texte peut être analysé comme du JSON.
2. Vérifier que la structure respecte le JSON Schema attendu.

Des contrôles métier supplémentaires pourront vérifier la cohérence entre plusieurs champs.

### 3.4. Stratégie de reprise

En cas d'échec de validation, le système peut transmettre les erreurs au générateur et demander une nouvelle réponse.

La stratégie doit :
- limiter le nombre de tentatives ;
- distinguer les erreurs de validation des erreurs de service ;
- éviter les boucles infinies ;
- respecter les limites de coût et de durée ;
- conserver les résultats des tentatives précédentes.

### 3.5. Évaluation

L'évaluateur enregistre les résultats de chaque génération et calcule les métriques par schéma et par stratégie.

Le flux principal est le suivant :

Entrée + JSON Schema → génération → parsing JSON → validation → reprise éventuelle → enregistrement → analyse.

## 4. Technologies

Les technologies envisagées sont :

- Python pour la logique applicative ;
- `jsonschema` pour la validation ;
- `pytest` pour les tests ;
- `pandas` et `matplotlib` pour l'analyse et la visualisation ;
- JSONL ou CSV pour les résultats expérimentaux.

Le choix du modèle et du fournisseur d'API reste à confirmer. L'architecture ne doit pas dépendre d'un fournisseur particulier.

Les versions définitives des dépendances seront fixées dans un fichier de dépendances ou un lockfile adapté.

## 5. Installation

### Prérequis

- Python 3.11 ou une version compatible avec les dépendances retenues ;
- Git ;
- une connexion à l'API pour les expériences réelles, si nécessaire.

### Cloner le dépôt

```bash
git clone https://github.com/TONGTONG0113/2026-MSMIN5IN52-GenAI.git
cd 2026-MSMIN5IN52-GenAI
git switch feature/json-stress
cd groupe-json-stress
```

Si le dépôt est déjà cloné, il n'est pas nécessaire de le cloner à nouveau.

### Créer un environnement virtuel

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Sous Windows :

```powershell
.venv\Scripts\Activate.ps1
```

### Installer les dépendances

Après la création de `requirements.txt` :

```bash
python -m pip install -r requirements.txt
```

### Configurer les variables d'environnement

```bash
cp .env.example .env
```

Les variables nécessaires seront définies après le choix du fournisseur. Les valeurs réelles doivent rester locales. Le fichier `.env` ne doit jamais être commité.

## 6. Utilisation

L'interface principale sera une commande en ligne de commande.

Les fonctionnalités prévues sont :

- valider une sortie JSON ;
- sélectionner un schéma ;
- lancer une génération avec ou sans reprise ;
- exécuter une campagne d'évaluation ;
- exporter les résultats.

Les commandes définitives seront documentées lorsque le point d'entrée sera implémenté.

Le mode simulé permettra d'exécuter les tests et de démontrer les mécanismes de validation sans dépendre du réseau ou d'une clé API.

## 7. Tests

La commande prévue pour exécuter les tests est :

```bash
python -m pytest -q
```

Les tests couvriront notamment :

- les JSON valides et invalides ;
- les champs obligatoires manquants ;
- les types incorrects ;
- les contraintes des cinq schémas ;
- les entrées vides et les réponses tronquées ;
- les erreurs de génération ;
- les timeouts et les indisponibilités du service ;
- les limites du nombre de tentatives ;
- les échecs persistants ;
- les limites de coût configurées.

Les tests unitaires utiliseront des réponses simulées afin de rester reproductibles et de ne pas nécessiter de clé API.

Les résultats réels des tests seront ajoutés après leur exécution.

## 8. Protocole expérimental et métriques

L'évaluation portera sur au moins 200 générations. Le protocole précisera le modèle, les entrées, les schémas, les paramètres de génération et les limites de reprise.

La baseline sans reprise sera comparée à la stratégie avec reprise sur des entrées comparables.

Les métriques seront :

- **Taux de conformité initiale :** proportion de sorties valides au premier essai.
- **Taux de conformité finale :** proportion de résultats valides après la stratégie de reprise.
- **Taux d'échec définitif :** proportion de résultats toujours invalides après épuisement des tentatives.
- **Nombre de tentatives :** nombre d'appels effectués pour chaque génération.
- **Latence :** durée de génération et durée totale du traitement.
- **Coût :** coût estimé des appels réels au modèle.

Les résultats seront ventilés par schéma et par stratégie. Les essais simulés seront identifiés séparément des appels réels au modèle.

Aucun résultat ne sera déclaré avant d'avoir été mesuré. Les tableaux et les graphiques seront ajoutés après l'exécution des expériences.

## 9. Limites et plan B

Les limites possibles comprennent :

- la variabilité des sorties des modèles ;
- les réponses incomplètes ou invalides ;
- les indisponibilités et les quotas d'API ;
- le coût supplémentaire des reprises ;
- les différences de comportement entre modèles ;
- les contraintes métier difficiles à exprimer dans un JSON Schema.

Le plan B utilisera des réponses simulées et des scénarios enregistrés pour garantir une démonstration reproductible sans API externe.

Un modèle local pourra être évalué ultérieurement si les ressources et le temps disponibles le permettent. Il ne sera présenté comme solution opérationnelle qu'après vérification.

## 10. Répartition du travail

- **Membre A — Schémas et validation :** cinq JSON Schemas, validateur, classification des erreurs et tests associés.
- **Membre B — Génération et reprise :** interface du générateur, mode simulé, adaptateur du modèle réel, gestion des erreurs et stratégie de reprise.
- **Membre C — Évaluation et CLI :** point d'entrée en ligne de commande, protocole expérimental, collecte des résultats, calcul des métriques et rapport.

Tous les membres participent à l'intégration, à la revue de code, aux tests de bout en bout et à la soutenance. Les noms et contributions individuelles seront complétés par le groupe.

## 11. Licence et références

Les licences des bibliothèques, des modèles et des données seront vérifiées avant leur utilisation et documentées dans cette section.

Références techniques :

- JSON Schema : https://json-schema.org/
- Documentation de `jsonschema` : https://python-jsonschema.readthedocs.io/
- Documentation de `pytest` : https://docs.pytest.org/

Les références du modèle et du fournisseur d'API seront ajoutées après leur sélection.
