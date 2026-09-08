# Guide de réalisation et de soumission

## 1. Réserver un sujet

Choisissez un sujet du [catalogue](CATALOGUE.md) ou soumettez une proposition libre avant de commencer. Deux groupes ne peuvent pas traiter le même sujet. Une proposition libre doit respecter les mêmes exigences de faisabilité, de mesure et de différenciation que le catalogue.

## 2. Préparer le fork

1. Forkez `jsboigeEPF/2026-MSMIN5IN52-GenAI`.
2. Clonez votre fork.
3. Ajoutez le dépôt du cours comme remote `upstream`.
4. Créez une branche de travail.
5. Créez **un seul** dossier `groupe-<nom-court>` à la racine à partir de [`template-groupe/`](../template-groupe/).

Ne renommez, ne déplacez et ne supprimez aucun fichier appartenant au dépôt ou à un autre groupe. Avant d'ouvrir la PR, vérifiez que le diff ne contient que votre dossier.

## 3. Travailler progressivement

- Commitez régulièrement avec des messages décrivant le changement.
- Répartissez les tâches et relisez le code des autres membres.
- Gardez une baseline simple pour mesurer l'apport du système GenAI.
- Testez les modes d'échec : réponse invalide, indisponibilité d'une API, entrée vide, timeout, absence de GPU ou dépassement de budget.
- Utilisez des données publiques, synthétiques ou dûment autorisées.

## 4. Contenu obligatoire du dossier

```text
groupe-<nom-court>/
├── README.md
├── .env.example
├── .gitignore
├── src/                 # ou application équivalente
├── tests/
├── notebooks/           # si pertinent
└── slides/              # PDF recommandé
```

Le README du groupe doit présenter : membres, sujet, problématique, architecture, installation, exécution, tests, résultats, limites, répartition du travail, licences et références.

Le projet contient également :

- le code source ;
- un notebook avec analyses/visualisations **ou** une UI/démo fonctionnelle ;
- des tests exécutables et leur résultat ;
- les slides de soutenance ;
- un `.env.example` ne contenant que les noms des variables.

## 5. Secrets, données et fichiers lourds

Ne commitez jamais :

- clé API, token, mot de passe, cookie ou fichier `.env` réel ;
- donnée personnelle ou confidentielle ;
- poids de modèle, environnement virtuel, `node_modules`, cache ou archive de build ;
- contenu dont la licence interdit la redistribution.

Si un secret a été commité, révoquez-le immédiatement puis prévenez l'enseignant. Le supprimer dans un commit ultérieur ne le retire pas de l'historique Git.

## 6. Ouvrir la Pull Request

1. Synchronisez votre branche avec `upstream/main` sans réécrire l'historique partagé.
2. Inspectez la liste des fichiers modifiés.
3. Poussez votre branche vers votre fork.
4. Ouvrez une PR vers `jsboigeEPF/2026-MSMIN5IN52-GenAI:main`.
5. Remplissez toute la checklist du modèle de PR.

La PR est obligatoire : un lien vers un dépôt externe, un fichier envoyé séparément ou un push direct ne constitue pas une remise.

**Date limite : lundi 19 octobre 2026 à 23:59.**

## 7. Préparer la soutenance

La démonstration doit pouvoir repartir depuis un environnement propre. Préparez un plan B enregistré ou mocké si une API externe, le réseau ou un GPU peut manquer. Chaque membre doit être capable d'expliquer l'architecture, les choix, les mesures et les limites.
