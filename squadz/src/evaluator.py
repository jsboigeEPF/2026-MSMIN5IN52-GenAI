import os
from PIL import Image
import pytesseract

def evaluer_lisibilite_image(image_path, mots_cles_attendus):
    if not os.path.exists(image_path):
        return {"score": 0, "erreur": "Fichier image introuvable"}

    img = Image.open(image_path)
    texte_extrait = pytesseract.image_to_string(img, lang="fra")

    # Calcul du taux de présence des mots-clés attendus
    mots_trouves = sum(1 for mot in mots_cles_attendus if mot.lower() in texte_extrait.lower())
    score = (mots_trouves / len(mots_cles_attendus)) * 100 if mots_cles_attendus else 0

    return {
        "texte_extrait": texte_extrait.strip(),
        "score_exactitude": score
    }