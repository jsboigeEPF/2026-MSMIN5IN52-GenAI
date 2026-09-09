# Sujets réalisés lors des promotions précédentes

Cette annexe évite de reproposer sous un autre nom un projet déjà exploré. Elle recense des **thèmes**, pas les personnes ni les évaluations. Les sujets ci-dessous ne font donc pas partie du catalogue actif 2026.

## MSMIN5IN52 — promotion précédente

| Thème réalisé | Technique centrale observée | Décision 2026 |
|---|---|---|
| Planificateur de voyage autonome | Recherche web, appels d'outils et APIs externes | Exclu |
| Recrutement augmenté et matching CV–offre | RAG, embeddings, TF-IDF, scoring LLM et classification d'e-mails | Exclu — plusieurs réalisations |
| Histoire interactive multimodale | Génération de texte, mémoire narrative et génération d'images | Exclu |
| Compositeur de bandes sonores et d'ambiances | MusicGen et services de génération audio | Exclu — plusieurs réalisations |
| Assistant personnel / majordome | LLM, Gmail, calendrier, météo et appels d'outils | Exclu |
| Analyse d'arguments hybride | LLM et moteur de logique symbolique | Exclu |
| Audit des biais des LLM | Prompts contrefactuels, métriques de sentiment et rapports | Exclu |
| Génération de documents structurés | Sorties structurées, agents, génération de PDF, CV et factures | Exclu |

Un créateur d'assets 3D et un storyboard vidéo avaient été proposés mais aucune implémentation complète n'a été constatée : ces directions peuvent être reprises uniquement avec un verrou technique et un périmètre 2026 clairement renouvelés.

## MSBNS3IN03 — promotion précédente

| Thème réalisé | Technique centrale observée | Décision 2026 |
|---|---|---|
| Générateur de quiz | RAG et calibration des questions | Exclu |
| Agent de recherche web | Recherche, extraction et synthèse | Exclu |
| Extracteur de documents structurés | Parsing documentaire et structuration par LLM | Exclu |
| Créateur de posts pour réseaux sociaux | Génération multimodale | Exclu |
| AI code reviewer | Analyse de diffs GitHub et revue par LLM | Exclu |
| Rédacteur de rapports | Recherche, base vectorielle, génération de texte et PDF | Exclu |
| Data Analyst Agent | Analyse tabulaire, visualisation et appels d'outils | Exclu |

## Thèmes connexes observés dans d'autres cursus

Ces thèmes ont été vérifiés dans les dépôts ou archives EPITA et ECE. Ils ne sont pas automatiquement interdits, mais un sujet 2026 doit expliciter un verrou GenAI différent.

| Thème observé | Technique centrale déjà explorée | Condition de renouvellement en 2026 |
|---|---|---|
| GraphRAG et questions sur graphes de connaissances | Extraction d'entités, graphe RDF/knowledge graph et retrieval | Isoler et mesurer un problème GenAI différent du simple pipeline GraphRAG |
| Serveur MCP d'outils symboliques | Exposition SAT/SMT/OWL par outils MCP | Porter l'évaluation sur l'autorisation, l'audit ou la fiabilité d'un autre domaine |
| Comparaison LLM contre solveur symbolique | Benchmark de raisonnement face à Z3 | Employer une tâche, un oracle et une hypothèse expérimentale nouveaux |
| Génération neuro-symbolique validée par contraintes | Génération puis validation CSP | Changer le mécanisme de validation et démontrer son apport par ablation |
| Analyse de sentiment et trading algorithmique | NLP de sentiment, indicateurs et stratégie de marché | Ne pas refaire une prédiction ou stratégie de trading ; privilégier provenance et auditabilité sans conseil financier |
| Chatbot médical | Interface conversationnelle appliquée à la santé | Exiger citations, périmètre fermé, abstention mesurée et absence de diagnostic réel |
| Modèles de diffusion multimodaux | Démonstration de génération image/vidéo | Évaluer contrôle, cohérence temporelle ou restauration sur un protocole reproductible |
| Génération procédurale et jeux | Solveurs, heuristiques et création de niveaux | Le verrou doit porter sur une capacité GenAI mesurée, pas sur le solveur ou le jeu seul |

## Règle de différenciation

Un nouveau sujet proche d'un thème historique doit changer son **verrou technique central**, pas seulement son nom, son secteur ou son interface. Par exemple :

- évaluer systématiquement un agent de code n'est pas refaire un code reviewer ;
- mesurer une mémoire persistante n'est pas refaire un assistant personnel généraliste ;
- produire et évaluer un doublage multi-locuteurs n'est pas recomposer une ambiance musicale ;
- tester la conformité d'un JSON Schema n'est pas extraire ou générer des documents métier.

En cas de doute, le groupe doit décrire explicitement ce qui différencie son MVP des réalisations listées ici et obtenir une validation avant de commencer.
