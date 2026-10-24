"""Moteur LipSync : API qui tourne dans Colab, à côté du modèle.

Le site (hébergé sur Render) envoie les fichiers ici, suit la progression et
télécharge la vidéo finale. Tout le travail lourd se fait en arrière-plan.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import lipsync_core as core

REPO = os.environ.get("LIPSYNC_REPO", "/content/LatentSync")
BASE = os.environ.get("LIPSYNC_BASE", "/content/LipSync")
WORK = os.environ.get("LIPSYNC_WORK", "/content/lipsync_travail")
CONFIG = os.environ.get("LIPSYNC_CONFIG", "configs/unet/stage2.yaml")
MODEL_VERSION = os.environ.get("LIPSYNC_VERSION", "1.5")
GPU_NAME = os.environ.get("LIPSYNC_GPU", "GPU")
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lipsync_runner.py")
PROJ_ROOT = os.path.join(BASE, "projets")
UPLOADS = os.path.join(WORK, "_envois")
os.makedirs(PROJ_ROOT, exist_ok=True)
os.makedirs(UPLOADS, exist_ok=True)

RES = {"480": 480, "720": 720, "1080": 1080}
STEPS = {"rapide": 20, "qualite": 30}

app = FastAPI(title="Moteur LipSync")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"])

lock = threading.Lock()
work_q = queue.Queue()
state = {}  # nom -> statut en mémoire


def slug(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (name or "").strip()).strip("-")
    return (s or time.strftime("video-%Y%m%d-%H%M"))[:60]


def proj_dir(nom):
    return os.path.join(PROJ_ROOT, nom)


def save_status(nom, **upd):
    with lock:
        st = state.setdefault(nom, {})
        st.update(upd)
        st["maj"] = time.time()
        snap = dict(st)
    path = os.path.join(proj_dir(nom), "statut.json")
    try:
        with open(path + ".tmp", "w") as f:
            json.dump(snap, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def load_status(nom):
    with lock:
        if nom in state:
            return dict(state[nom])
    path = os.path.join(proj_dir(nom), "statut.json")
    if os.path.exists(path):
        st = json.load(open(path))
        # un travail interrompu par une coupure de Colab n'est plus en cours
        if st.get("etat") in ("attente", "preparation", "synchro", "assemblage"):
            st["etat"] = "interrompu"
        with lock:
            state[nom] = st
        return dict(st)
    return None


# ---------------------------------------------------------------- travail ---
def worker():
    while True:
        nom = work_q.get()
        try:
            run_project(nom)
        except Exception as e:  # filet de sécurité
            save_status(nom, etat="erreur", message=f"Erreur inattendue : {e}")
        finally:
            work_q.task_done()


def run_project(nom):
    st = load_status(nom)
    d = proj_dir(nom)
    m = json.load(open(os.path.join(d, "reglages.json")))
    work_dir = os.path.join(WORK, nom)
    save_status(nom, etat="preparation", message="Préparation de la vidéo", erreur=None)
    try:
        manifest, jobs = core.prepare_project(d, work_dir, m["visage"], m["audio"], m["morceau"],
                                              m["resolution"], log=lambda s: None)
    except Exception as e:
        print("préparation :", e, flush=True)
        save_status(nom, etat="erreur", message="Le fichier n'a pas pu être lu. Vérifie la photo/vidéo et l'audio.")
        return
    total = len(manifest["chunks"])
    deja = total - len(jobs)
    save_status(nom, total=total, faits=deja, duree=manifest["total_frames"] / core.FPS)

    if jobs:
        save_status(nom, etat="synchro", message="Chargement du modèle")
        cmd = [sys.executable, "-u", RUNNER, os.path.join(work_dir, "jobs.json"),
               "--steps", str(m["etapes"]), "--guidance", str(m["precision"]), "--config", CONFIG]
        p = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        durees, erreur = [], None
        for raw in p.stdout:
            line = raw.strip()
            if line.startswith("START "):
                _, k, n, _ = line.split()
                restant = None
                if durees:
                    restant = sum(durees) / len(durees) * (int(n) - int(k) + 1)
                save_status(nom, message=f"Synchronisation du morceau {deja + int(k)} sur {total}", restant=restant)
            elif line.startswith("DONE "):
                durees.append(float(line.split()[3]))
                save_status(nom, faits=deja + int(line.split()[1]), sec_par_morceau=sum(durees) / len(durees))
            elif line.startswith("ERROR FACE"):
                erreur = ("Aucun visage trouvé dans un passage. Utilise une photo ou une vidéo où le visage "
                          "est bien de face, visible, sans main devant la bouche.")
            elif line.startswith("ERROR OOM"):
                erreur = "Mémoire de la carte graphique pleine. Recommence avec une résolution plus basse."
            elif line.startswith("ERROR OTHER"):
                erreur = "Le modèle s'est arrêté : " + line[12:]
            else:
                print(line, flush=True)
        p.wait()
        if erreur or p.returncode != 0:
            save_status(nom, etat="erreur", restant=None,
                        message=erreur or "Le modèle s'est arrêté. Les passages finis sont gardés : clique sur Reprendre.")
            return

    save_status(nom, etat="assemblage", message="Assemblage de la vidéo finale", restant=None, faits=total)
    final = os.path.join(d, f"{nom}.mp4")
    try:
        core.assemble(d, work_dir, manifest, final, log=lambda s: None)
    except Exception as e:
        print("assemblage :", e, flush=True)
        save_status(nom, etat="erreur", message="L'assemblage a échoué. Clique sur Reprendre.")
        return
    shutil.rmtree(work_dir, ignore_errors=True)
    save_status(nom, etat="termine", message="Vidéo prête", termine_le=time.time(),
                taille=os.path.getsize(final))


threading.Thread(target=worker, daemon=True).start()


# -------------------------------------------------------------------- API ---
@app.get("/api/etat")
def etat():
    return {"ok": True, "gpu": GPU_NAME, "modele": MODEL_VERSION, "drive": BASE.startswith("/content/drive"),
            "file": work_q.qsize()}


class Init(BaseModel):
    nom: str
    taille: int


@app.post("/api/envoi")
def envoi_init(i: Init):
    uid = uuid.uuid4().hex
    ext = os.path.splitext(i.nom)[1].lower()[:6]
    os.makedirs(os.path.join(UPLOADS, uid), exist_ok=True)
    json.dump({"ext": ext, "taille": i.taille}, open(os.path.join(UPLOADS, uid, "info.json"), "w"))
    return {"id": uid}


@app.put("/api/envoi/{uid}/{index}")
async def envoi_morceau(uid: str, index: int, request: Request):
    d = os.path.join(UPLOADS, re.sub(r"[^a-f0-9]", "", uid))
    if not os.path.isdir(d):
        raise HTTPException(404, "Envoi inconnu")
    with open(os.path.join(d, f"{index:05d}.part"), "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return {"ok": True}


@app.post("/api/envoi/{uid}/fin")
def envoi_fin(uid: str):
    d = os.path.join(UPLOADS, re.sub(r"[^a-f0-9]", "", uid))
    info = json.load(open(os.path.join(d, "info.json")))
    out = os.path.join(UPLOADS, uid + info["ext"])
    with open(out, "wb") as o:
        for p in sorted(x for x in os.listdir(d) if x.endswith(".part")):
            with open(os.path.join(d, p), "rb") as f:
                shutil.copyfileobj(f, o)
    shutil.rmtree(d)
    if os.path.getsize(out) != info["taille"]:
        os.remove(out)
        raise HTTPException(400, "Le fichier est arrivé incomplet. Réessaie.")
    return {"fichier": uid + info["ext"]}


class Nouveau(BaseModel):
    titre: str
    visage: str
    audio: str
    morceau: int = 30
    resolution: str = "720"
    qualite: str = "rapide"
    precision: float = 1.5
    onde: list[float] = []


def _uploaded(name):
    p = os.path.join(UPLOADS, os.path.basename(name))
    if not os.path.exists(p):
        raise HTTPException(400, "Fichier introuvable, renvoie-le.")
    return p


@app.post("/api/projets")
def creer(n: Nouveau):
    nom = slug(n.titre)
    base_nom, i = nom, 2
    while os.path.exists(proj_dir(nom)):
        nom, i = f"{base_nom}-{i}", i + 1
    d = proj_dir(nom)
    src = os.path.join(d, "fichiers_source")
    os.makedirs(src)
    v, a = _uploaded(n.visage), _uploaded(n.audio)
    visage = os.path.join(src, "visage" + os.path.splitext(v)[1])
    audio = os.path.join(src, "audio" + os.path.splitext(a)[1])
    shutil.move(v, visage)
    shutil.move(a, audio)
    # miniature pour le site
    try:
        core.run(["ffmpeg", "-y", "-i", visage, "-frames:v", "1", "-vf", "scale=320:-2", os.path.join(d, "miniature.jpg")])
    except Exception:
        pass
    reglages = {"titre": n.titre.strip() or nom, "visage": visage, "audio": audio,
                "morceau": max(15, min(90, n.morceau)), "resolution": RES.get(n.resolution, 720),
                "etapes": STEPS.get(n.qualite, 20), "precision": max(1.0, min(3.0, n.precision)),
                "cree_le": time.time(), "onde": [round(float(x), 3) for x in n.onde[:400]]}
    json.dump(reglages, open(os.path.join(d, "reglages.json"), "w"))
    save_status(nom, etat="attente", message="En file d'attente", faits=0, total=None, titre=reglages["titre"],
                cree_le=reglages["cree_le"])
    work_q.put(nom)
    return {"nom": nom}


_ondes = {}


def _onde(nom):
    if nom not in _ondes:
        try:
            _ondes[nom] = json.load(open(os.path.join(proj_dir(nom), "reglages.json"))).get("onde", [])
        except Exception:
            _ondes[nom] = []
    return _ondes[nom]


def _resume(nom):
    st = load_status(nom) or {}
    return {
        "onde": _onde(nom),
        "nom": nom,
        "titre": st.get("titre", nom),
        "etat": st.get("etat", "inconnu"),
        "message": st.get("message", ""),
        "faits": st.get("faits", 0),
        "total": st.get("total"),
        "restant": st.get("restant"),
        "duree": st.get("duree"),
        "cree_le": st.get("cree_le"),
        "taille": st.get("taille"),
        "miniature": os.path.exists(os.path.join(proj_dir(nom), "miniature.jpg")),
    }


@app.get("/api/projets")
def liste():
    noms = [n for n in os.listdir(PROJ_ROOT) if os.path.exists(os.path.join(proj_dir(n), "reglages.json"))]
    items = [_resume(n) for n in noms]
    items.sort(key=lambda x: x.get("cree_le") or 0, reverse=True)
    return items


@app.get("/api/projets/{nom}")
def detail(nom: str):
    if not os.path.exists(os.path.join(proj_dir(nom), "reglages.json")):
        raise HTTPException(404, "Vidéo introuvable")
    return _resume(nom)


@app.post("/api/projets/{nom}/reprendre")
def reprendre(nom: str):
    st = load_status(nom)
    if not st:
        raise HTTPException(404, "Vidéo introuvable")
    if st.get("etat") in ("attente", "preparation", "synchro", "assemblage"):
        return {"ok": True}
    save_status(nom, etat="attente", message="En file d'attente")
    work_q.put(nom)
    return {"ok": True}


@app.get("/api/projets/{nom}/miniature")
def miniature(nom: str):
    p = os.path.join(proj_dir(os.path.basename(nom)), "miniature.jpg")
    if not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/api/projets/{nom}/video")
def video(nom: str, telecharger: int = 0):
    nom = os.path.basename(nom)
    p = os.path.join(proj_dir(nom), f"{nom}.mp4")
    if not os.path.exists(p):
        raise HTTPException(404, "La vidéo n'est pas encore prête")
    st = load_status(nom) or {}
    fname = slug(st.get("titre", nom)) + ".mp4"
    return FileResponse(p, media_type="video/mp4", filename=fname if telecharger else None,
                        content_disposition_type="attachment" if telecharger else "inline")


def reprise_au_demarrage():
    """Les vidéos coupées par une fermeture de Colab restent marquées « interrompu »."""
    for n in os.listdir(PROJ_ROOT):
        if os.path.exists(os.path.join(proj_dir(n), "reglages.json")):
            load_status(n)


reprise_au_demarrage()
