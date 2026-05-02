import os
import numpy as np
from deepface import DeepFace

KNOWN_FACES_DIR = "database"
SEUIL = 0.70      # Seuil augmenté pour plus de souplesse
TOP_K = 3

def meilleur_score(distances, top_k=TOP_K):
    top = sorted(distances)[:top_k]
    return sum(top) / len(top)

def reconnaitre_visage(image_path: str) -> str:
    print(f"[RECOG] ══════════════════════════════════")
    print(f"[RECOG] Début reconnaissance: {image_path}")

    # Vérification fichier source
    if not os.path.exists(image_path):
        print(f"[RECOG] ❌ Fichier introuvable: {image_path}")
        return "Inconnu"

    taille = os.path.getsize(image_path)
    print(f"[RECOG] Taille photo reçue: {taille} octets")

    if taille < 1000:
        print(f"[RECOG] ❌ Photo trop petite ({taille} octets) — qualité insuffisante")
        return "Inconnu"

    # Vérification dossier database
    if not os.path.exists(KNOWN_FACES_DIR):
        print(f"[RECOG] ❌ Dossier database introuvable !")
        return "Inconnu"

    # Liste des employés disponibles
    employes = [d for d in os.listdir(KNOWN_FACES_DIR)
                if os.path.isdir(os.path.join(KNOWN_FACES_DIR, d))]

    if not employes:
        print(f"[RECOG] ❌ Aucun employé dans database/ !")
        return "Inconnu"

    print(f"[RECOG] Employés disponibles: {employes}")

    distances_par_personne = {}

    for personne in employes:
        dossier_personne = os.path.join(KNOWN_FACES_DIR, personne)
        photos = [f for f in os.listdir(dossier_personne)
                  if f.lower().endswith((".jpg", ".jpeg", ".png"))]

        print(f"[RECOG] {personne}: {len(photos)} photo(s)")

        if not photos:
            print(f"[RECOG] ⚠️ Aucune photo pour {personne}")
            continue

        distances = []

        for fichier in photos:
            chemin_ref = os.path.join(dossier_personne, fichier)
            taille_ref = os.path.getsize(chemin_ref)
            print(f"[RECOG] Comparaison avec {personne}/{fichier} ({taille_ref} octets)")

            try:
                result = DeepFace.verify(
                    img1_path   = image_path,
                    img2_path   = chemin_ref,
                    model_name  = "VGG-Face",
                    detector_backend = "opencv",
                    enforce_detection = False
                )
                distance = result.get("distance", 1.0)
                verified = result.get("verified", False)
                print(f"[RECOG] → distance={distance:.4f} verified={verified}")
                distances.append(distance)

            except Exception as e:
                print(f"[RECOG] ❌ Erreur comparaison {fichier}: {e}")

        if distances:
            distances_par_personne[personne] = distances
            print(f"[RECOG] {personne} distances: {[round(d,4) for d in distances]}")

    if not distances_par_personne:
        print("[RECOG] ❌ Aucune comparaison possible → Inconnu")
        return "Inconnu"

    # Calcul scores
    scores = {}
    for personne, distances in distances_par_personne.items():
        scores[personne] = meilleur_score(distances)
        print(f"[RECOG] Score {personne}: {scores[personne]:.4f}")

    meilleur_nom = min(scores, key=scores.get)
    meilleur_score_val = scores[meilleur_nom]

    print(f"[RECOG] Meilleur: {meilleur_nom} score={meilleur_score_val:.4f} seuil={SEUIL}")

    if meilleur_score_val > SEUIL:
        print(f"[RECOG] ❌ Score {meilleur_score_val:.4f} > seuil {SEUIL} → Inconnu")
        return "Inconnu"

    print(f"[RECOG] ✅ Résultat final: {meilleur_nom}")
    print(f"[RECOG] ══════════════════════════════════")
    return meilleur_nom
