from src.config import CONCEPTS_PEDAGOGIQUES
from src.evaluator import evaluer_lisibilite_image

premier_concept = CONCEPTS_PEDAGOGIQUES[0]
output_file = f"data/outputs/system/test_concept_1.png"

mots_cles = ["Arbre", "Binaire", "Recherche", "Racine", "Nœud"]

print(f"Évaluation de l'image pour le concept : {premier_concept['titre']}")
resultat = evaluer_lisibilite_image(output_file, mots_cles)

print("Résultat de l'évaluation :")
print(resultat)