# Protocole E1 à E6

## Corpus et consentement des mesures

`eval/corpus/refs.csv` contient 40 textes originaux : 20 à enregistrer et 20 à
synthétiser avec Piper UPMC, différent de la voix de réponse Siwis. Aucun fichier
de parole humaine n'est inventé ni inclus. Un WAV contient une seule question.
Format : PCM16, mono ; les fichiers d'une autre fréquence sont rééchantillonnés.

```bash
python -m scripts.corpus record --seconds 6
python -m scripts.corpus synthesize --model models/fr_FR-upmc-medium.onnx
python -m scripts.corpus record --manifest interruptions.csv --seconds 2
python -m scripts.corpus noise --directory eval/noise --seconds 60
```

Les fichiers déjà présents sont conservés. Pour refaire une prise, déplacer ou
supprimer explicitement ce WAV, puis relancer. Enregistrer les vraies interruptions
avec au moins 320 ms de parole continue ; un « stop » de 120 ms est hors du domaine
de détection retenu. Les faux départs doivent rester sous 300 ms. Réécouter les WAV
et ajuster les références pour qu'elles correspondent exactement à ce qui est dit.

## E1 et E2 : latence et WER

```bash
python -m scripts.launch replay eval/corpus --config config.lock.yaml --noise white --snr clean
python -m scripts.launch replay eval/corpus --config config.lock.yaml --noise pink --snr 10
python -m scripts.launch replay eval/corpus --config config.lock.yaml --noise real --noise-file eval/noise/room.wav --snr 5
python -m scripts.campaign --config config.lock.yaml
```

Le script de campagne applique le même lanceur CUDA aux sous-processus.
Il réchauffe les modèles à chaque condition. La durée totale dépend aussi de la
durée des questions et des réponses : les 25 minutes évoquées dans le PRD ne sont
pas garanties par un replay cadencé en temps réel.

Par défaut : 40 × (1 propre + 4 SNR × 3 bruits) = **520 tours distincts**. Le produit
600 de la spec inclut trois copies du cas propre ; `--full-grid` le reproduit,
mais ne pas considérer ces copies comme trois observations indépendantes.

Le bruit blanc et rose est fixé par seed et index. Le bruit réel est répété si trop
court. Le SNR est mesuré sur la durée du WAV source, avant le silence ajouté par le
harnais. Un gain commun évite l'écrêtage sans changer le SNR. Le bruit continue
pendant le silence de fin, pour ne pas faciliter artificiellement le VAD.

Chaque entrée passe par le détecteur de tour. Le STT est lancé après endpoint,
puis le pipeline de réponse habituel. L'historique et le VAD sont remis à zéro
entre les cas. Le rejeu est cadencé à 32 ms, et n'accélère pas artificiellement les
temps ADC. Un cas sans endpoint compte comme une transcription vide dans le WER.

Le warmup est exclu, mais ses événements restent dans le JSONL. Les latences sont
conditionnelles aux tours ayant atteint les deux événements ; le nombre total,
les échecs et les tours interrompus restent visibles dans `turns.csv`.

## E3 et E4 : interruption contrôlée

```bash
python -m scripts.launch bench eval/corpus/interruptions.csv --config config.lock.yaml --delay 2
```

Le LLM est remplacé par une réponse longue et fixe pour isoler le test d'arrêt.
Le moteur TTS et le lecteur restent réels. Le fichier d'interruption est mélangé
à la référence de sortie atténuée de 10 dB et retardée de 30 ms. Ce signal passe
par Silero, le détecteur et la porte d'énergie. L'injection survient environ deux
secondes après le premier audio (arrondi à la trame de 32 ms).

Ce montage simule l'écho, il ne reproduit pas l'acoustique d'une pièce ni l'AEC.
La classification `handled` exige une détection après l'injection et un accusé
d'arrêt hôte en ≤ 300 ms. Une détection trop précoce est fausse et ne valide pas
le cas utilisateur. Un bruit court sans détection est `rejected`.

## E5 : effet réel de l'AEC

Effectuer 20 réponses dans une pièce silencieuse, sans intervenir, AEC activé,
puis refaire 20 réponses avec le même volume et le même placement, AEC désactivé.
Noter le routage exact. Annoter chaque réponse comme `false` si elle est interrompue
par l'écho, ou `rejected` sinon. Une session half-duplex ne mesure pas l'efficacité
de l'AEC : elle empêche structurellement toutes les interruptions.

```bash
python -m scripts.annotate runs/SESSION --turn 1 --experiment E5-AEC --expected false --outcome rejected
```

## E6 : vingt interruptions humaines

Faire vingt essais avec des énoncés de plus de 320 ms. Filmer ou enregistrer
simultanément la sortie et la voix pour contrôler l'arrêt acoustique. Après avoir
fermé la session, annoter le numéro du tour assistant interrompu :

```bash
python -m scripts.annotate runs/SESSION --turn 4 --experiment E6 --expected true --outcome handled
```

Un utilisateur non détecté est `missed`. Ne pas fabriquer une annotation à partir
du seul VAD : sans vérité terrain, une interruption peut provenir de l'écho.

## Rapport et statistiques

```bash
python -m voiceagent report runs/SESSION
python -m voiceagent.report runs/SESSION
```

Les fichiers JSONL sont la source unique. Le rapport produit p50/p95, min/max si
n < 20, n, IC95 du p50 par bootstrap déterministe (1 000 tirages), WER pondéré par
le nombre de mots, proportions avec intervalle de Wilson. Les groupes de bruit,
SNR, type de corpus et mode sont distincts. Les arrêts de nettoyage/shutdown ne
sont pas inclus dans les temps d'interruption. La normalisation du WER conserve
les accents et les chiffres ; elle normalise casse, ponctuation et espaces.

`nvidia-smi` mesure l'occupation de la carte entière une fois par seconde, incluant
Ollama et les autres applications. `torch.cuda.max_memory_allocated` ne couvre que
PyTorch dans le processus. Un pic bref entre deux sondes peut être manqué par
`nvidia-smi`. Les deux valeurs doivent être publiées avec cette limite.

Pour le test de stabilité, laisser une vraie conversation/replay tourner 30 min,
surveiller les événements `error`, `capture_stats` et `memory_sample`, puis vérifier
que la VRAM et la RAM ne croissent pas au fil des tours. Ce test n'a pas été réalisé
sur le matériel cible dans la livraison.
