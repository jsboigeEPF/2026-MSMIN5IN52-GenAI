from src.config import CONCEPTS_PEDAGOGIQUES
from src.generator import generer_et_sauvegarder_image

premier_concept = CONCEPTS_PEDAGOGIQUES[0]
output_file = "data/outputs/system/test_concept_1.png"

print(f"Test de génération pour le concept : {premier_concept['titre']}")
generer_et_sauvegarder_image(premier_concept, output_file, baseline=False)