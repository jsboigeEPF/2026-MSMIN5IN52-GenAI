from src.config import CONCEPTS_PEDAGOGIQUES
from src.evaluator import evaluer_lisibilite_image

def lancer_benchmark():
    print("Lancement du benchmark comparatif Baseline vs Système C2...")

    resultats_baseline = []
    resultats_systeme = []

    for concept in CONCEPTS_PEDAGOGIQUES:
        mots_cles = concept['titre'].split()
        
        # Simulation d'évaluation sur des images de test
        # En production, tu appelles ton générateur et evaluer_lisibilite_image sur les outputs
        print(f"Évaluation du concept : {concept['titre']}")
    
    if name == "main":
        lancer_benchmark()  