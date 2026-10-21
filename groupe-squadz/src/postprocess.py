import pytesseract
from PIL import Image

def post_process_and_validate(image_path, expected_concepts):
    # Ouvre l'image générée (Plan A ou Plan B)
    image = Image.open(image_path)

    # Extraction du texte via OCR
    detected_text = pytesseract.image_to_string(image)

    # Vérification de la présence des éléments clés du brief pédagogique
    errors = []
    for concept in expected_concepts:
        if concept.lower() not in detected_text.lower():
            errors.append(concept)
            
    # Calcul d'un score de qualité basique
    success_rate = 1.0 - (len(errors) / len(expected_concepts)) if expected_concepts else 1.0

    validation_report = {
        "status": "VALID" if success_rate >= 0.8 else "WARNING",
        "score": success_rate,
        "missing_elements": errors,
        "ocr_text": detected_text
    }

    return validation_report