# Installation sur EndeavourOS

## Préparer le poste

Le projet cible Python 3.12 (3.11 accepté), x86_64 et un pilote NVIDIA compatible
avec les bibliothèques CUDA 12.6 de PyTorch 2.7.1. Vérifier `nvidia-smi` avant
d'installer Python. `uv venv --python 3.12` évite de dépendre de la version Python
courante d'Arch Linux. Les dépendances installées comprennent les bibliothèques
CUDA utilisateurs ; ne pas modifier le pilote avec les commandes du projet.

Suivre les cinq étapes du README. `scripts.launch` ajoute les dossiers `nvidia/*/lib`
de l'environnement Python à `LD_LIBRARY_PATH` dans un sous-processus, afin que
CTranslate2 trouve cuBLAS/cuDNN. Si le pilote est trop ancien, utiliser le profil
CPU/Piper avant de décider d'une mise à jour du système.

Ollama est un processus séparé : « un processus asyncio » désigne l'orchestrateur,
pas la totalité des moteurs. Vérifier qu'il utilise bien le GPU avec `ollama ps`.

## Configurer l'AEC PipeWire

Le fichier `docs/pipewire-echo-cancel.conf` suit le module natif echo-cancel.
Installer le support WebRTC fourni par les paquets PipeWire de ta distribution.

```bash
mkdir -p ~/.config/pipewire/pipewire.conf.d
cp docs/pipewire-echo-cancel.conf ~/.config/pipewire/pipewire.conf.d/90-voiceagent-aec.conf
systemctl --user restart pipewire pipewire-pulse wireplumber
wpctl status
```

Cette opération coupe momentanément l'audio du bureau. Dans `wpctl status`, relever
l'identifiant du micro AEC et celui du sink AEC, puis :

```bash
wpctl set-default IDENTIFIANT_DU_MICRO_AEC
wpctl set-default IDENTIFIANT_DU_SINK_AEC
python -m voiceagent devices
```

PortAudio voit parfois seulement `pulse` ou `default`, pas les noms des nœuds
PipeWire. Dans ce cas, utiliser ces périphériques et contrôler le routage dans
`pavucontrol` ou `qpwgraph`. Les flux de capture/lecture physiques de l'AEC doivent
rejoindre le micro et les haut-parleurs physiques ; le flux de l'agent doit entrer
dans le sink AEC et lire la source AEC. Éviter toute boucle entre nœuds virtuels.

Si un périphérique est exposé directement, renseigner son index ou son nom dans
`audio.input_device` / `audio.output_device` du YAML. Après édition de la config
de base, relancer `prepare` pour produire une nouvelle configuration figée.

Pour retirer l'AEC, supprimer seulement le fichier ajouté ci-dessus et redémarrer
les services utilisateur. Ne pas effacer les autres configurations PipeWire.

## Préparer Piper

Piper sert de repli CPU ; le corpus synthétique utilise une autre voix.

```bash
mkdir -p models
python -m piper.download_voices --download-dir models fr_FR-siwis-medium fr_FR-upmc-medium
```

Les fichiers `.onnx` et `.onnx.json` doivent rester ensemble. Pour le repli, copier
`config.yaml` vers `config.cpu.yaml` et changer ces paramètres :

```yaml
stt:
  model: Systran/faster-whisper-small
  revision: main
  device: cpu
  compute_type: int8
  beam_size: 1
tts:
  engine: piper
  device: cpu
  piper_model: models/fr_FR-siwis-medium.onnx
```

Conserver les autres sections du fichier. Le LLM peut rester sur la RTX.
Pour libérer moins de VRAM en gardant Whisper sur GPU : remplacer seulement le
modèle par `Systran/faster-whisper-small`, garder `cuda` et `int8_float16`.

```bash
python -m voiceagent prepare --config config.cpu.yaml --output config.cpu.lock.yaml
python -m scripts.launch run --config config.cpu.lock.yaml
```

## Reproductibilité et installation hors ligne

`prepare` résout les révisions Hugging Face vers des SHA, télécharge les snapshots
et écrit leurs chemins dans `config.lock.yaml`. Il enregistre le digest Ollama,
vérifié avant le premier flux. Silero est embarqué dans sa version de paquet.
Piper est identifié par le SHA256 du fichier ONNX dans le manifeste de modèles.

Les chemins du lock sont propres à la machine ; exécuter `prepare` sur chaque PC.
Conserver le JSON associé, la configuration du run et `pip freeze` pour le rendu.
Après téléchargement et premier warmup réussi, les inférences locales ne requièrent
aucun service cloud. Tester en coupant Internet avant la démo ; une dépendance de
phonémisation peut encore télécharger une ressource au premier chargement.

Le résolveur de paquets a accepté les versions retenues. Cela ne garantit pas la
compatibilité de ton pilote, de ton serveur audio ou du matériel : `doctor`, puis
un tour réel, sont les contrôles d'installation à réaliser.
