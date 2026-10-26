# Décisions et écarts explicites

1. **Budget de latence.** Les valeurs de la spec totalisent 1 380 ms, pas 1 200 ms.
   À 32 ms par trame, 500 ms de silence donnent un endpoint à 512 ms minimum.
   La cible globale reste un objectif à vérifier. Aucune optimisation ne la prouve.
2. **Interruption.** Le seuil de 300 ms demande dix trames, soit 320 ms. Le délai
   « validation → arrêt » est séparé de « début de parole → arrêt ». Les deux ne
   doivent pas être présentés comme la même mesure.
3. **Streaming.** Whisper transcrit un segment terminé. LLM et TTS avancent en
   flux de fragments et de phrases. Il ne s'agit pas d'un STT incrémental.
4. **Historique.** Sans alignement français fiable, on conserve les unités de
   synthèse entièrement jouées. La phrase partiellement jouée est omise. F11 est
   donc satisfaite de façon conservatrice, pas au mot près.
5. **Annulation native.** `Task.cancel()` ne tue pas un kernel CUDA. Les résultats
   obsolètes sont abandonnés et les moteurs sérialisés, pour éviter le chevauchement.
6. **Horodatage.** DAC estimé pour le début ; accusé hôte de `abort()` pour l'arrêt.
   E6 doit ajouter un contrôle acoustique avant d'affirmer « son arrêté en 300 ms ».
7. **Bruit.** Il existe 13 conditions distinctes, soit 520 cas. Les 600 cas de la
   grille brute répètent le propre trois fois. Le script offre les deux variantes.
8. **Porte d'énergie.** L'atténuation attendue de -10 dB est configurable. Ce n'est
   pas une mesure de transfert acoustique. La calibrer sur le vrai couple micro/HP.
9. **Modèles.** `main` est seulement le point de téléchargement initial ; `prepare`
   écrit les SHA et le digest Ollama. Le manifeste des dépendances complet est
   résolu, tandis que l'installation GPU complète reste à valider sur le poste.
10. **Cloud P2.** Route désactivée explicitement. Aucun modèle OpenAI ni tarif n'est
    supposé valide sur la seule base du PRD. L'événement Realtime `response.done`
    fournit de l'usage ; ce n'est pas, par hypothèse, une facture en euros. Une
    extension sérieuse doit vérifier le modèle disponible, la tarification, le
    change, réserver le coût du prochain tour et persister le budget entre sessions.
    Sans cela, annoncer un plafond garanti de 15 € serait trompeur.
11. **Routage auto.** Non livré : il dépend d'une route cloud validée et d'une
    comparaison avec métriques homogènes. Plusieurs voix locales sont sélectionnables.
12. **Livrables matériels.** La voix humaine, l'acoustique réelle, les traces E1–E6
    et la vidéo live ne peuvent pas être produits dans un conteneur sans le PC.
    Les scripts, textes, protocole et storyboard sont fournis pour les réaliser.
