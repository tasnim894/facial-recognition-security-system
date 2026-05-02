import os
import numpy as np
from deepface import DeepFace

KNOWN_FACES_DIR = "database"
SEUIL = 0.70
TOP_K = 3

def meilleur_score(distances, top_k=TOP_K):
    top = sorted(distances)[:top_k]
    return sum(top) / len(top)

def reconnaitre_visage(image_path: str) -> str:
    print(f"[RECOG] Debut reconnaissance: {image_path}")

    if not os.path.exists(image_path):
        print(f"[RECOG] Fichier introuvable: {image_path}")
        return "Inconnu"

    taille = os.path.getsize(image_path)
    if taille < 1000:
        print(f"[RECOG] Photo trop petite ({taille} octets)")
        return "Inconnu"

    if not os.path.exists(KNOWN_FACES_DIR):
        print(f"[RECOG] Dossier database introuvable")
        return "Inconnu"

    employes = [d for d in os.listdir(KNOWN_FACES_DIR)
                if os.path.isdir(os.path.join(KNOWN_FACES_DIR, d))]

    if not employes:
        print(f"[RECOG] Aucun employe dans database/")
        return "Inconnu"

    distances_par_personne = {}

    for personne in employes:
        dossier_personne = os.path.join(KNOWN_FACES_DIR, personne)
        photos = [f for f in os.listdir(dossier_personne)
                  if f.lower().endswith((".jpg", ".jpeg", ".png"))]

        if not photos:
            continue

        distances = []
        for fichier in photos:
            chemin_ref = os.path.join(dossier_personne, fichier)
            try:
                result = DeepFace.verify(
                    img1_path=image_path,
                    img2_path=chemin_ref,
                    model_name="VGG-Face",
                    detector_backend="opencv",
                    enforce_detection=False
                )
                distance = result.get("distance", 1.0)
                distances.append(distance)
            except Exception as e:
                print(f"[RECOG] Erreur comparaison {fichier}: {e}")

        if distances:
            distances_par_personne[personne] = distances

    if not distances_par_personne:
        return "Inconnu"

    scores = {p: meilleur_score(d) for p, d in distances_par_personne.items()}
    meilleur_nom = min(scores, key=scores.get)
    meilleur_score_val = scores[meilleur_nom]

    if meilleur_score_val > SEUIL:
        return "Inconnu"

    return meilleur_nom
