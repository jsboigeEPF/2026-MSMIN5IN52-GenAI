from src.config import CONCEPTS_PEDAGOGIQUES
from src.generator import generer_et_sauvegarder_image
from src.evaluator import evaluer_lisibilite_image
import time

def lancer_evaluation_globale():
    print("=== DEBUT DE LA CAMPAGNE D'EVALUATION GLOBALE (10 CONCEPTS) ===")

    resultats_globaux = []

    for concept in CONCEPTS_PEDAGOGIQUES:
        print(f"\n--- Traitement du concept {concept['id']} : {concept['titre']} ---")
        
        # 1. Génération de la Baseline
        path_baseline = f"data/outputs/baseline/concept_{concept['id']}.png"
        print("Génération de la baseline...")
        generer_et_sauvegarder_image(concept, path_baseline, baseline=True)
        time.sleep(3)
        
        # Définition des mots-clés pour l'évaluation OCR
        mots_cles = [mot for mot in concept['titre'].split() if len(mot) > 3]
        
        eval_baseline = evaluer_lisibilite_image(path_baseline, mots_cles)
        print(f"Score baseline : {eval_baseline.get('score_exactitude', 0)}%")
        
        # 2. Génération avec le Système Avancé
        path_system = f"data/outputs/system/concept_{concept['id']}.png"
        print("Génération avec le système avancé...")
        generer_et_sauvegarder_image(concept, path_system, baseline=False)
        time.sleep(3)
        
        eval_system = evaluer_lisibilite_image(path_system, mots_cles)
        print(f"Score système : {eval_system.get('score_exactitude', 0)}%")
        
        resultats_globaux.append({
            "id": concept['id'],
            "titre": concept['titre'],
            "score_baseline": eval_baseline.get('score_exactitude', 0),
            "score_system": eval_system.get('score_exactitude', 0)
        })

    print("\n=== FIN DE LA CAMPAGNE D'EVALUATION ===")
    print("Résumé des scores finaux :")
    for res in resultats_globaux:
        print(f"Concept {res['id']} ({res['titre']}) -> Baseline: {res['score_baseline']}% | Système: {res['score_system']}%")


if __name__ == "__main__":
    lancer_evaluation_globale()