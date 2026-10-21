import os
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from src.postprocess import post_process_and_validate
import base64

load_dotenv()

def generer_et_sauvegarder_image(concept, output_path, baseline=False, force_regenerate=False):
    if os.path.exists(output_path) and not force_regenerate:
        print(f"Image déjà existante dans le cache, génération évitée pour : {output_path}")
        return "cache"

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    if baseline:
        prompt = f"Schéma simple et direct représentant : {concept['titre']}. {concept['description']}"
    else:
        try:
            from src.config import STYLE_TEMPLATE_AVANCE
            prompt = f"{STYLE_TEMPLATE_AVANCE} Sujet : {concept['titre']}. Description détaillée : {concept['description']}"
        except ImportError:
            prompt = f"Schéma pédagogique professionnel sur : {concept['titre']}. Description : {concept['description']}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/jsboigeEPF/2026-MSMIN5IN52-GenAI",
        "X-Title": "GenAI-Project-C2"
    }

    payload = {
        "model": "qwen/qwen-image-3",
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024"
    }

    print(f"Tentative de génération d'image par IA via OpenRouter (Plan A) pour : {concept['titre']} (Mode baseline={baseline})...")

    succes_plan_a = False
    try:
        response = requests.post(f"{base_url}/images", headers=headers, json=payload, timeout=120, verify=False)
        
        if response.status_code == 200:
            data = response.json()
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
            
            # Post-traitement OCR et validation qualité
            concepts_attendus = [concept['titre'].split()[0], "définition", "exemple"]
            rapport_validation = post_process_and_validate(output_path, concepts_attendus)
            print("Rapport de post-traitement OCR :", rapport_validation)

            if rapport_validation["status"] == "WARNING":
                print("Attention, certains éléments textuels manquent ou sont mal lisibles sur l'image !")

            print(f"Succès ! Image Plan A générée et sauvegardée : {output_path}")
            succes_plan_a = True
            return "plan_a"
        else:
            print(f"Erreur API Plan A ({response.status_code}) : {response.text}")
    except Exception as e:
        print(f"Erreur technique Plan A détectée : {e}")

    # PLAN B : Si le Plan A échoue, on bascule sur le Plan B intelligent Groq + Pillow
    if not succes_plan_a:
        print("Activation du Plan B intelligent avec Groq...")
        groq_api_key = os.getenv("GROQ_API_KEY")
        definition = concept['description']
        points = [
            f"Caractéristiques principales : {concept['titre']}",
            f"Règles juridiques, techniques ou méthodologiques applicables",
            f"Application pratique et cas d'usage courant"
        ]
        
        if groq_api_key:
            try:
                url_groq = "https://api.groq.com/openai/v1/chat/completions"
                headers_groq = {
                    "Authorization": f"Bearer {groq_api_key}",
                    "Content-Type": "application/json"
                }
                payload_groq = {
                    "model": "openai/gpt-oss-20b",
                    "messages": [
                        {
                            "role": "system",
                            "content": "Tu es un expert pédagogique. Donne 3 points clés courts, techniques et précis sous forme de phrases distinctes séparées par un retour à la ligne, sans puces numérotées."
                        },
                        {
                            "role": "user",
                            "content": f"Concept : {concept['titre']}. Description : {concept['description']}"
                        }
                    ]
                }
                resp_groq = requests.post(url_groq, json=payload_groq, headers=headers_groq, timeout=10)
                if resp_groq.status_code == 200:
                    contenu = resp_groq.json()["choices"][0]["message"]["content"]
                    if contenu:
                        lignes_groq = [l.strip() for l in contenu.split('\n') if l.strip()]
                        if len(lignes_groq) >= 3:
                            points = lignes_groq[:3]
            except Exception as err:
                print(f"Info Plan B Groq : {err}")

        img = Image.new('RGB', (1000, 1000), color=(250, 250, 252))
        draw = ImageDraw.Draw(img)
        
        draw.rectangle([40, 40, 960, 140], fill=(30, 41, 59))
        try:
            font_title = ImageFont.truetype("arial.ttf", 24)
            font_subtitle = ImageFont.truetype("arial.ttf", 15)
            font_body = ImageFont.truetype("arial.ttf", 16)
            font_bold = ImageFont.truetype("arial.ttf", 17)
        except:
            font_title = font_subtitle = font_body = font_bold = ImageFont.load_default()

        draw.text((60, 65), "FICHE PEDAGOGIQUE (PLAN B)", fill=(148, 163, 184), font=font_subtitle)
        draw.text((60, 90), f"{concept['titre'][:55]}", fill=(255, 255, 255), font=font_title)
        
        draw.rectangle([40, 180, 960, 380], fill=(255, 255, 255), outline=(226, 232, 240), width=2)
        draw.rectangle([40, 180, 960, 230], fill=(241, 245, 249))
        draw.text((60, 195), "1. DEFINITION & OBJECTIFS", fill=(15, 23, 42), font=font_bold)
        
        def dessiner_texte_multi_lignes(texte, x, y, max_caracteres=85):
            mots = texte.split(' ')
            ligne_courante = ""
            current_y = y
            for mot in mots:
                if len(ligne_courante + " " + mot) < max_caracteres:
                    ligne_courante += " " + mot if ligne_courante else mot
                else:
                    draw.text((x, current_y), ligne_courante, fill=(51, 65, 85), font=font_body)
                    current_y += 28
                    ligne_courante = mot
            if ligne_courante:
                draw.text((x, current_y), ligne_courante, fill=(51, 65, 85), font=font_body)
            return current_y + 40

        dessiner_texte_multi_lignes(definition, 60, 250)

        draw.rectangle([40, 420, 960, 780], fill=(255, 255, 255), outline=(226, 232, 240), width=2)
        draw.rectangle([40, 420, 960, 470], fill=(241, 245, 249))
        draw.text((60, 435), "2. POINTS CLES & SPECIFICITES", fill=(15, 23, 42), font=font_bold)
        
        y_point = 495
        for i, pt in enumerate(points, 1):
            draw.ellipse([60, y_point+2, 72, y_point+14], fill=(59, 130, 246))
            mots_pt = pt.split(' ')
            ligne_pt = ""
            for mot in mots_pt:
                if len(ligne_pt + " " + mot) < 75:
                    ligne_pt += " " + mot if ligne_pt else mot
                else:
                    draw.text((95, y_point), ligne_pt, fill=(51, 65, 85), font=font_body)
                    y_point += 22
                    ligne_pt = mot
            if ligne_pt:
                draw.text((95, y_point), ligne_pt, fill=(51, 65, 85), font=font_body)
            y_point += 50

        draw.rectangle([40, 930, 960, 960], fill=(248, 250, 252))
        draw.text((60, 938), "Module GenAI - Projet Educatif Squadz", fill=(148, 163, 184), font=font_subtitle)

        img.save(output_path, "PNG")
        
        concepts_attendus = [concept['titre'].split()[0], "définition", "exemple"]
        rapport_validation = post_process_and_validate(output_path, concepts_attendus)
        print("Rapport de post-traitement OCR :", rapport_validation)

        if rapport_validation["status"] == "WARNING":
            print("Attention, certains concepts pédagogiques manquent ou sont mal lisibles sur l'image !")
        
        print(f"Schéma Plan B intelligent structuré généré et sauvegardé : {output_path}")
        return "plan_b"