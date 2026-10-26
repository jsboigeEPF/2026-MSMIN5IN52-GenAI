"""Build the delivery report from the included validation traces."""

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
NAVY = colors.HexColor("#12243a")
TEAL = colors.HexColor("#087f8c")
GRAY = colors.HexColor("#526174")
PALE = colors.HexColor("#eef5f8")


def build():
    font_root = Path("/usr/share/fonts/truetype/dejavu")
    pdfmetrics.registerFont(TTFont("DejaVu", str(font_root / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuBold", str(font_root / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVuBold")
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "TextFR", fontName="DejaVu", fontSize=9.4, leading=14, textColor=NAVY, spaceAfter=9
        )
    )
    styles.add(
        ParagraphStyle(
            "TitleFR",
            fontName="DejaVuBold",
            fontSize=29,
            leading=34,
            textColor=NAVY,
            spaceAfter=18,
        )
    )
    styles.add(
        ParagraphStyle(
            "HeadingFR",
            fontName="DejaVuBold",
            fontSize=17,
            leading=22,
            textColor=NAVY,
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            "SmallFR", fontName="DejaVu", fontSize=8, leading=11, textColor=GRAY, spaceAfter=8
        )
    )
    styles.add(
        ParagraphStyle(
            "CellFR",
            fontName="DejaVu",
            fontSize=8.2,
            leading=12,
            textColor=NAVY,
            alignment=TA_LEFT,
        )
    )
    story = []

    def p(text, style="TextFR"):
        return Paragraph(text, styles[style])

    def add(text, style="TextFR"):
        story.append(p(text, style))

    def table(rows, widths):
        cells = [[p(str(cell), "CellFR") for cell in row] for row in rows]
        result = Table(cells, colWidths=widths, hAlign="LEFT")
        result.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), PALE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#dce4ec")),
                ]
            )
        )
        story.append(result)
        story.append(Spacer(1, 12))

    summary_path = ROOT / "runs/validation-simulated/summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = summary["groups"]["simulated/local"]
    response = metrics["response_ms"]

    add("D1 / RAPPORT TECHNIQUE / 29 SEPTEMBRE 2026", "SmallFR")
    story.append(Spacer(1, 12))
    add("Un agent vocal local,<br/>interruptible et mesurable", "TitleFR")
    add("Python · Français · RTX 3070 Ti visée · Pipeline STT / LLM / TTS", "SmallFR")
    story.append(Spacer(1, 14))
    add("1. Périmètre et état de la livraison", "HeadingFR")
    add(
        "Le projet réalise un assistant vocal en terminal, avec capture continue, détection "
        "de fin de parole, génération de texte en flux, synthèse par phrase et lecture "
        "interruptible. Le code sépare la logique de conversation, les moteurs et les mesures "
        "pour rendre les erreurs et les décisions de concurrence testables."
    )
    table(
        [
            ["Livré et vérifié ici", "À mesurer sur le poste cible"],
            [
                "52 tests réussis ; contrôle Ruff propre ; quatre essais CLI ; "
                "JSONL, CSV, statistiques et graphiques générés.",
                "Qualité de transcription, latence réelle, interruption acoustique, "
                "annulation d'écho, VRAM et stabilité sur 30 minutes.",
            ],
            [
                "Sources modulaires, configurations, dépendances figées, scripts de corpus, "
                "protocole E1-E6 et scénario vidéo.",
                "20 prises de voix humaine, 20 phrases Piper, 10 signaux d'interruption, "
                "bruit de pièce et vidéo live de deux minutes.",
            ],
        ],
        [253, 253],
    )
    add(
        "Les exécutions fournies utilisent des doublures déterministes, "
        "sans GPU ni périphérique "
        "audio. Elles valident la circulation des données et les calculs. Elles ne constituent "
        "pas une expérience de performance sur la RTX. Aucun résultat réel E1-E6 n'est inventé."
    )
    add(
        "L'objectif reste p50 ≤ 1,2 s et p95 ≤ 2,0 s entre la dernière trame de parole "
        "et le premier audio. Le coût d'API de cette livraison est nul. "
        "La comparaison Realtime "
        "et le routage automatique sont des extensions P2 non implémentées."
    )
    add(
        "Pièces de preuve : docs/test-results.txt ; runs/validation-simulated ; "
        "runs/validation-replay ; runs/validation-interruptions ; runs/validation-text.",
        "SmallFR",
    )
    story.append(PageBreak())

    add("2. Architecture et interruption", "HeadingFR")
    add(
        "Un orchestrateur asyncio possède chaque tour et ses tâches. Ollama reste un serveur "
        "local séparé. Les moteurs natifs tournent hors de la boucle événementielle."
    )
    table(
        [
            ["Étage", "Implémentation", "Décision de conception"],
            [
                "Entrée / VAD",
                "PortAudio, 16 kHz, 512 échantillons ; Silero ONNX CPU",
                "File bornée, pré-roll, seuil normal 0,5 et seuil d'interruption 0,7.",
            ],
            [
                "Transcription",
                "faster-whisper, français, int8_float16",
                "Segment terminé, un exécuteur dédié ; repli small/CPU configurable.",
            ],
            [
                "Réponse",
                "Ollama Qwen2.5 3B Q4_K_M",
                "NDJSON, historique de six tours, file de quatre phrases, segmentation bornée.",
            ],
            [
                "Voix / lecture",
                "Kokoro Siwis, ou Piper CPU ; sortie 24 kHz",
                "File de trois unités audio, callbacks de 20 ms, estimation de l'heure DAC.",
            ],
            [
                "Traces",
                "Bus unique, perf_counter_ns, JSONL",
                "Écriture hors boucle, configuration et versions enregistrées avec la session.",
            ],
        ],
        [87, 187, 232],
    )
    add(
        "<b>Interruption.</b> La parole candidate doit durer dix trames, soit 320 ms. "
        "La lecture est invalidée et abortée en premier ; les tâches LLM/TTS/lecture sont "
        "ensuite annulées. Le flux HTTP est fermé. Les trames déjà enregistrées servent "
        "au nouveau tour, sans perdre le début de la demande."
    )
    add(
        "<b>Annulation native.</b> Une inférence CUDA déjà lancée ne peut pas être tuée par "
        "Task.cancel(). Son résultat est abandonné. Un exécuteur à un seul thread par moteur "
        "empêche une nouvelle inférence du même moteur de chevaucher l'ancienne. Le prochain "
        "tour peut attendre ce calcul restant."
    )
    add(
        "<b>Historique.</b> Seules les unités TTS entièrement jouées sont conservées. Une "
        "phrase coupée est omise et le tour porte [interrompu]. Cette méthode ne prétend pas "
        "connaître l'alignement exact entre mots français et échantillons."
    )
    add(
        "<b>Écho.</b> Le module PipeWire WebRTC doit être routé "
        "vers le micro et les haut-parleurs. "
        "Une porte d'énergie compare le micro à une estimation de l'écho (-10 dB par défaut, "
        "marge 6 dB). Ce réglage doit être calibré. Le mode half-duplex est disponible, "
        "avec interruption désactivée."
    )
    story.append(PageBreak())

    add("3. Mesures de validation logicielle", "HeadingFR")
    add(
        "Les valeurs ci-dessous proviennent de la session de simulation incluse. Les moteurs "
        "renvoient des textes fixes et des sinusoïdes. Ces mesures vérifient les horodatages "
        "et le rapport ; elles ne prouvent pas l'atteinte des objectifs matériels."
    )
    stage_rows = [["Étape", "n", "p50 simulé", "p95 simulé"]]
    labels = {
        "endpoint_ms": "Fin de tour",
        "stt_ms": "STT simulé",
        "llm_first_token_ms": "Premier fragment LLM",
        "sentence_ms": "Première phrase",
        "tts_ms": "TTS simulé",
        "playback_ms": "Démarrage simulé",
        "response_ms": "Réponse, bout en bout",
    }
    for key, label in labels.items():
        value = metrics[key]
        stage_rows.append(
            [label, value["n"], f"{value['p50']:.1f} ms", f"{value['p95']:.1f} ms"]
        )
    table(stage_rows, [214, 46, 123, 123])
    add(
        f"Réponse : n = {response['n']}, IC95 du p50 par bootstrap "
        f"[{response['p50_ci_low']:.1f} ; {response['p50_ci_high']:.1f}] ms. "
        "Avec moins de 20 tours, le p95 est peu fiable. Le nombre de phrases mesurées "
        "peut dépasser celui des tours vocaux, car la session contient aussi du texte.",
        "SmallFR",
    )
    chart = ROOT / "runs/validation-simulated/latency-simulated.png"
    story.append(Image(str(chart), width=430, height=242))
    add(
        "Le budget nominal du PRD additionne 500 + 250 + 200 + 250 + 150 + 30 = 1 380 ms. "
        "Il dépasse déjà la cible de 1 200 ms. Il faudra donc réduire un ou plusieurs postes. "
        "Les médianes par étage ne s'additionnent pas nécessairement à la médiane totale.",
        "SmallFR",
    )
    story.append(PageBreak())

    add("4. Protocole expérimental et résultats disponibles", "HeadingFR")
    table(
        [
            ["Expérience", "Méthode fournie", "État réel"],
            [
                "E1 - latence",
                "40 WAV, même VAD et pipeline, warmup exclu, horloge monotone.",
                "Non mesurée sur cible.",
            ],
            [
                "E2 - WER",
                "Blanc, rose, bruit réel ; propre, 20, 10, 5, 0 dB ; référence FR.",
                "Calcul testé ; corpus à enregistrer.",
            ],
            [
                "E3 - interruption",
                "Injection à 2 s, écho -10 dB / 30 ms, réponse fixe.",
                "Un cas technique simulé accepté.",
            ],
            [
                "E4 - faux départ",
                "Toux, euh et bruits courts annotés.",
                "Un cas technique simulé rejeté.",
            ],
            [
                "E5 - AEC",
                "20 réponses avec AEC, puis 20 sans ; même volume et placement.",
                "À réaliser dans la pièce.",
            ],
            [
                "E6 - live",
                "20 interruptions humaines, annotation et contrôle acoustique.",
                "À réaliser sur le PC.",
            ],
        ],
        [105, 260, 141],
    )
    add(
        "<b>Statistiques.</b> Le rapport calcule n, p50/p95, min/max pour les petits "
        "échantillons, IC95 du p50 par bootstrap (1 000 tirages) et intervalles de Wilson "
        "pour les proportions. Un WER est agrégé par nombre de mots, pas par moyenne des "
        "pourcentages des phrases. Les cas non transcrits comptent comme des suppressions."
    )
    add(
        "<b>Interprétation des fixtures.</b> Le cas E3 simulé donne 1/1 accepté, mais son "
        "intervalle de Wilson à 95 % est environ [20,7 % ; 100 %]. E4 donne 0/1 faux "
        "départ, avec borne haute proche de 79,3 %. Ces minuscules effectifs démontrent "
        "le chemin de calcul, sans étayer une performance de 90 %."
    )
    add(
        "<b>Conditions distinctes.</b> Il y a 40 × (1 + 4 × 3) = 520 tours distincts. "
        "Les 600 tours du produit brut répètent le propre trois fois. Le script permet "
        "cette grille complète, mais ces répétitions ne sont pas indépendantes."
    )
    add(
        "<b>Analyse d'erreurs à mener.</b> Examiner séparément les endpoints manqués, "
        "les hallucinations sur bruit, les mots rares, les coupures trop précoces et "
        "les auto-interruptions par écho. Annoter la vérité terrain avant de calculer "
        "un taux. Ne jamais déduire une fausse interruption de la seule sortie du VAD."
    )
    add(
        "VRAM cible : ≤ 7 Go, non mesurée ici. nvidia-smi couvre la carte entière à 1 Hz ; "
        "le pic PyTorch ne couvre pas Ollama ni CTranslate2. Les pics brefs et l'arrêt "
        "acoustique exigent des observations complémentaires.",
        "SmallFR",
    )
    story.append(PageBreak())

    add("5. Reproduction, limites et validation finale", "HeadingFR")
    add(
        "L'installation complète et les replis sont détaillés dans README.md et "
        "docs/INSTALLATION.md. Python 3.12 est isolé dans un environnement uv. Le fichier "
        "de dépendances complet a été résolu ; l'installation CUDA doit être confirmée sur "
        "la machine cible."
    )
    table(
        [
            ["Ordre", "Action"],
            ["1", "Installer uv, Ollama, PortAudio et espeak-ng ; vérifier nvidia-smi."],
            [
                "2",
                "Créer l'environnement Python 3.12 et installer requirements-local.lock.txt.",
            ],
            ["3", "Démarrer Ollama et télécharger qwen2.5:3b-instruct-q4_K_M."],
            ["4", "Lancer prepare : snapshots Hugging Face et digest Ollama figés."],
            ["5", "Essayer run --text, puis configurer PipeWire et passer au mode vocal."],
        ],
        [55, 451],
    )
    add(
        "<b>Avant le rendu.</b> Faire cinq tours vocaux sans clavier ; vérifier que la "
        "lecture précède la fin du LLM ; tester l'interruption et un bruit court ; mesurer "
        "la VRAM ; produire le corpus ; lancer E1-E6 ; générer les rapports de ces sessions. "
        "La vidéo de deux minutes doit montrer le poste réel. Le storyboard est fourni, "
        "mais aucune vidéo matérielle ne peut être certifiée depuis ce conteneur."
    )
    add(
        "<b>Limites qui restent visibles.</b> STT par segment et non incrémental ; mémoire "
        "à la phrase ; validation d'interruption à 320 ms minimum ; arrêt hôte distinct de "
        "l'arrêt du son dans la pièce ; porte d'énergie non calibrée ; objectifs de vitesse "
        "et de robustesse non validés. La route cloud P2 et le routage automatique sont "
        "explicitement absents. Les sources sont prêtes pour GitHub, sans publication externe."
    )
    add(
        "<b>Critère de réussite final.</b> Les tests logiciels sont nécessaires "
        "mais insuffisants. "
        "Le dossier devient une preuve expérimentale lorsqu'il contient les WAV autorisés, "
        "les traces réelles, les annotations, les chiffres calculés et une démo reproduite "
        "sur le matériel annoncé."
    )
    add("Références techniques", "HeadingFR")
    add(
        "faster-whisper : github.com/SYSTRAN/faster-whisper<br/>"
        "Kokoro : github.com/hexgrad/kokoro<br/>"
        "Silero : github.com/snakers4/silero-vad<br/>"
        "Ollama : docs.ollama.com/api/chat<br/>"
        "Audio : python-sounddevice.readthedocs.io/en/latest/api/streams.html<br/>"
        "AEC : docs.pipewire.org/page_module_echo_cancel.html<br/>"
        "Piper : github.com/OHF-Voice/piper1-gpl",
        "SmallFR",
    )
    add(
        "Consultées le 29 septembre 2026. Liens complets et interfaces inspectées dans "
        "docs/SOURCES.md. Cahier des charges : document fourni avec la demande.",
        "SmallFR",
    )

    def furniture(canvas, document):
        canvas.setStrokeColor(TEAL)
        canvas.setLineWidth(2)
        canvas.line(44, 800, 551, 800)
        canvas.setFont("DejaVu", 8)
        canvas.setFillColor(GRAY)
        canvas.drawString(44, 813, "VOICEAGENT FR / RAPPORT DE LIVRAISON")
        canvas.drawString(44, 26, "Validation logicielle - mesures matérielles à réaliser")
        canvas.drawRightString(551, 26, f"{document.page} / 5")

    output = ROOT / "docs/rapport-technique.pdf"
    document = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        rightMargin=44,
        leftMargin=44,
        topMargin=62,
        bottomMargin=48,
        title="VoiceAgent FR - Rapport technique",
        author="Projet VoiceAgent FR",
    )
    document.build(story, onFirstPage=furniture, onLaterPages=furniture)
    print(output)


if __name__ == "__main__":
    build()
