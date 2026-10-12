import os
import requests
from io import BytesIO
from PIL import Image, ImageDraw
from dotenv import load_dotenv
import base64

load_dotenv()

def generer_et_sauvegarder_image(concept, output_path, baseline=False):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL", "[https://openrouter.ai/api/v1](https://openrouter.ai/api/v1)")

    if baseline:
        prompt = f"Dessine un schéma simple représentant : {concept['titre']}"
    else:
        from src.config import STYLE_TEMPLATE_AVANCE
        prompt = f"{STYLE_TEMPLATE_AVANCE} Sujet : {concept['titre']}. Description détaillée : {concept['description']}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "[https://github.com/jsboigeEPF/2026-MSMIN5IN52-GenAI](https://github.com/jsboigeEPF/2026-MSMIN5IN52-GenAI)",
        "X-Title": "GenAI-Project-C2"
    }

    payload = {
        "model": "qwen/qwen-image-3",
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024"
    }

    print(f"Tentative de génération d'image par IA via OpenRouter pour : {concept['titre']} (Mode baseline={baseline})...")

    try:
        response = requests.post(f"{base_url}/images", headers=headers, json=payload, timeout=120, verify=False)
        
        if response.status_code == 200:
            data = response.json()
            print("DEBUG - Réponse brute OpenRouter :", data)
            
            if 'data' in data and len(data['data']) > 0:
                item = data['data'][0]
                if 'url' in item:
                    image_url = item['url']
                    img_data = requests.get(image_url, timeout=30).content
                    img = Image.open(BytesIO(img_data))
                elif 'b64_json' in item:
                    img_data = base64.b64decode(item['b64_json'])
                    img = Image.open(BytesIO(img_data))
                else:
                    raise KeyError("Format d'image non reconnu dans la réponse OpenRouter.")
            else:
                raise KeyError("Données de réponse vides.")
                
            img.save(output_path, "PNG")
            print(f"Succès ! Image générée par l'IA sauvegardée : {output_path}")
            return True
        else:
            print(f"Erreur API ({response.status_code}) : {response.text}")
            return generer_image_plan_b_graphique(concept, output_path)
            
    except Exception as e:
        print(f"Erreur technique détectée : {e}")
        print("Activation du Plan B graphique de secours...")
        return generer_image_plan_b_graphique(concept, output_path)

def generer_image_plan_b_graphique(concept, output_path):
    img = Image.new('RGB', (1024, 1024), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([362, 150, 662, 270], outline=(30, 144, 255), width=4, fill=(230, 240, 255))
    d.text((385, 195), f"PRINCIPE : {concept['titre']}", fill=(0, 0, 0))
    d.rectangle([150, 450, 420, 570], outline=(46, 139, 87), width=3, fill=(230, 255, 230))
    d.text((170, 500), "Composant / État A", fill=(0, 0, 0))
    d.rectangle([604, 450, 874, 570], outline=(178, 34, 34), width=3, fill=(255, 230, 230))
    d.text((624, 500), "Composant / État B", fill=(0, 0, 0))
    d.line([(512, 270), (285, 450)], fill=(100, 100, 100), width=3)
    d.line([(512, 270), (739, 450)], fill=(100, 100, 100), width=3)
    d.text((50, 900), f"Description technique : {concept['description']}", fill=(80, 80, 80))
    d.text((50, 940), "[MODE PLAN B ACTIF - SÉCURISÉ POUR SOUTENANCE]", fill=(200, 0, 0))
    img.save(output_path, "PNG")
    print(f"Schéma Plan B graphique généré et sauvegardé : {output_path}")
    return True