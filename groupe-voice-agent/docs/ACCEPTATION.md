# Matrice de livraison

`Vérifié` signifie exécuté ici. `Implémenté, à valider sur PC` n'est pas une preuve
de performance ou de bon fonctionnement matériel.

| Exigence | État | Preuve / action restante |
|---|---|---|
| F1 capture continue | Implémenté, à valider sur PC | PortAudio 16 kHz/512, compteurs et débordements |
| F2 VAD / endpoint | Logique vérifiée, Silero à valider | Tests seuils, pré-roll, 512 ms de silence |
| F3 STT français | Adaptateur implémenté | faster-whisper fr, anti-hallucination, GPU à tester |
| F4 LLM streaming / 6 tours | Client et historique vérifiés | NDJSON, erreurs, intégration Ollama à tester |
| F5 TTS par phrase | Orchestration vérifiée | Audio simulé commence avant fin du LLM ; vrais modèles à tester |
| F6 sortie non bloquante | Logique vérifiée | Lecteur testé avec callback et horloge DAC contrôlés |
| F7 annulation / barge-in | Logique vérifiée | Génération et lecture interrompues ; test acoustique restant |
| F8 traces | Vérifié | Quatre sessions de validation incluses |
| F9 mode texte | Vérifié avec doublures | Avec/sans audio, lecture stdin, EOF propre |
| F10 bruits courts | Logique vérifiée | Toux simulée rejetée ; toux et écho réels à mesurer |
| F11 historique joué | Implémenté avec limite | Granularité phrase complète ; pas d'alignement mot à mot |
| F12 rapport | Vérifié | CSV/JSON, n, bootstrap, Wilson, PNG |
| F13 replay / SNR | Vérifié avec fixture | VAD réel et 40 WAV à produire sur PC |
| F14 WER | Calcul vérifié | Normalisation française, échecs inclus, corpus réel restant |
| F15 cloud | Non implémenté, P2 | Route rejetée explicitement |
| F16 choix de voix | Implémenté | `--voice`, moteur sélectionné dans YAML |
| F17 routage auto | Non implémenté, P2 | Dépend de la route cloud |
| Latence ≤ 1,2 s / 2,0 s | Non mesurée sur cible | Ne pas utiliser les chiffres simulés comme preuve |
| VRAM ≤ 7 Go | Instrumentation fournie | Valeurs GPU indisponibles ici |
| Stabilité 30 minutes | Non vérifiée sur cible | Exécuter une campagne longue et analyser les traces |
| Rapport 4–6 pages | Livré | `rapport-technique.pdf`, cinq pages |
| Vidéo de 2 min | À filmer sur PC | Storyboard et commandes dans DEMO.md |
| GitHub | Sources prêtes, pas de publication | Initialisation et push depuis ton compte |

## Vérifications exécutées

- 52 tests `unittest`, tous réussis ; sortie conservée dans `test-results.txt`.
- Ruff : analyse statique et formatage propres.
- Résolution complète des dépendances Linux / CPython 3.12 acceptée par pip.
- Démo mock : cinq tours audio simulés, un tour interrompu, un dernier tour texte.
- Replay : fixture PCM16 et bruit rose à 10 dB, traces et WER de test.
- Interruption contrôlée : un signal long accepté, un signal court rejeté.
- Mode texte sans audio : génération, trace et sortie propre à EOF.

Les doublures reproduisent la mécanique ; elles renvoient volontairement des
textes fixes. Leur WER nul ne constitue aucune mesure de qualité STT.
