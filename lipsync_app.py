"""Interface web (Gradio) de l'outil de synchronisation labiale LipSync."""
import os
import re
import shutil
import subprocess
import sys
import time

import gradio as gr

import lipsync_core as core

REPO = os.environ.get("LIPSYNC_REPO", "/content/LatentSync")
BASE = os.environ.get("LIPSYNC_BASE", "/content/LipSync")
WORK = os.environ.get("LIPSYNC_WORK", "/content/lipsync_travail")
CONFIG = os.environ.get("LIPSYNC_CONFIG", "configs/unet/stage2.yaml")
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lipsync_runner.py")
PROJ_ROOT = os.path.join(BASE, "projets")
os.makedirs(PROJ_ROOT, exist_ok=True)

RES = {"480p (le plus rapide)": 480, "720p (recommandé)": 720, "1080p (lent, plus de mémoire)": 1080}
QUAL = {"Rapide (20 étapes)": 20, "Meilleure qualité (30 étapes)": 30}


def slug(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")
    return s or time.strftime("projet_%Y%m%d_%H%M")


def list_projects():
    if not os.path.isdir(PROJ_ROOT):
        return []
    return sorted(d for d in os.listdir(PROJ_ROOT) if os.path.exists(os.path.join(PROJ_ROOT, d, "manifest.json")))


def fmt_time(sec):
    sec = int(sec)
    h, m = divmod(sec // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {sec % 60:02d} s"


def _path(f):
    if f is None:
        return None
    return f if isinstance(f, str) else getattr(f, "name", None)


def process(name, face, audio, chunk_sec, res_label, qual_label, guidance, resume=False):
    lines = []

    def log(msg):
        lines.append(msg)

    def view(head):
        return f"### {head}\n\n" + "\n".join(f"- {l}" for l in lines[-8:])

    proj = slug(name)
    proj_dir = os.path.join(PROJ_ROOT, proj)
    in_dir = os.path.join(proj_dir, "fichiers_source")
    work_dir = os.path.join(WORK, proj)

    if resume:
        import json
        mpath = os.path.join(proj_dir, "manifest.json")
        if not os.path.exists(mpath):
            yield view("❌ Projet introuvable."), None
            return
        m = json.load(open(mpath))
        face_src, audio_src = m["face"], m["audio"]
        chunk_sec, max_h = m["chunk_sec"], m["max_h"]
        if not (os.path.exists(face_src) and os.path.exists(audio_src)):
            yield view("❌ Les fichiers source de ce projet ont disparu. Crée un nouveau projet."), None
            return
    else:
        face, audio = _path(face), _path(audio)
        if not face or not audio:
            yield view("❌ Ajoute un visage (photo ou vidéo) ET un audio."), None
            return
        os.makedirs(in_dir, exist_ok=True)
        face_src = os.path.join(in_dir, "visage" + os.path.splitext(face)[1].lower())
        audio_src = os.path.join(in_dir, "audio" + os.path.splitext(audio)[1].lower())
        for old in os.listdir(in_dir):
            os.remove(os.path.join(in_dir, old))
        shutil.copy(face, face_src)
        shutil.copy(audio, audio_src)
        max_h = RES[res_label]

    steps = QUAL.get(qual_label, 20)
    log(f"Projet : **{proj}**")
    yield view("⏳ Préparation…"), None
    try:
        manifest, jobs = core.prepare_project(proj_dir, work_dir, face_src, audio_src, chunk_sec, max_h, log=log)
    except Exception as e:
        log(f"Erreur pendant la préparation : {e}")
        yield view("❌ Le fichier n'a pas pu être lu. Vérifie qu'il s'agit bien d'une photo/vidéo et d'un audio."), None
        return
    yield view("⏳ Préparation terminée"), None

    if jobs:
        env = dict(os.environ)
        cmd = [sys.executable, "-u", RUNNER, os.path.join(work_dir, "jobs.json"),
               "--steps", str(steps), "--guidance", str(guidance), "--config", CONFIG]
        p = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        total_chunks = len(manifest["chunks"])
        already = total_chunks - len(jobs)
        durations, error, head = [], None, "⏳ Synchronisation en cours…"
        for raw in p.stdout:
            line = raw.strip()
            if line.startswith("STATUS "):
                log(line[7:])
            elif line.startswith("START "):
                _, k, n, idx = line.split()
                done = already + int(k) - 1
                eta = ""
                if durations:
                    avg = sum(durations) / len(durations)
                    eta = f" · temps restant estimé : **{fmt_time(avg * (int(n) - int(k) + 1))}**"
                head = f"⏳ Morceau {done + 1} / {total_chunks}{eta}"
            elif line.startswith("DONE "):
                durations.append(float(line.split()[3]))
                log(f"Morceau terminé en {fmt_time(durations[-1])} (sauvegardé).")
            elif line.startswith("ERROR FACE"):
                error = ("Aucun visage détecté dans un morceau. Utilise une photo ou une vidéo où le visage "
                         "est bien visible, de face, sans main devant la bouche.")
            elif line.startswith("ERROR OOM"):
                error = ("Mémoire de la carte graphique pleine. Relance en choisissant une résolution plus "
                         "basse ou des morceaux plus courts.")
            elif line.startswith("ERROR OTHER"):
                error = "Erreur du modèle : " + line[12:]
            elif line.startswith("TRACE "):
                print(line)  # détail technique visible dans Colab uniquement
            yield view(head), None
        p.wait()
        if error or p.returncode != 0:
            log(error or "Le traitement s'est arrêté (voir les détails dans Colab).")
            log("Les morceaux déjà finis sont gardés : utilise l'onglet « Reprendre » pour continuer.")
            yield view("❌ Arrêt"), None
            return

    final = os.path.join(proj_dir, f"{proj}_final.mp4")
    try:
        core.assemble(proj_dir, work_dir, manifest, final, log=log)
    except Exception as e:
        log(str(e))
        yield view("❌ Assemblage impossible"), None
        return
    shutil.rmtree(work_dir, ignore_errors=True)
    where = "dans ton Google Drive, dossier **LipSync/projets/" + proj + "**" if BASE.startswith("/content/drive") \
        else "dans Colab (pense à la télécharger avant de fermer)"
    log(f"Vidéo enregistrée {where}.")
    yield view("✅ Vidéo prête !"), final


def new_job(name, face, audio, chunk_sec, res_label, qual_label, guidance):
    yield from process(name, face, audio, chunk_sec, res_label, qual_label, guidance)


def resume_job(name, qual_label, guidance):
    if not name:
        yield "### ❌ Choisis un projet dans la liste.", None
        return
    yield from process(name, None, None, None, None, qual_label, guidance, resume=True)


CSS = """
.gradio-container {max-width: 880px !important; margin: auto}
#titre h1 {margin-bottom: 0}
"""

import inspect
THEME = gr.themes.Soft(primary_hue="orange")
_STYLE_IN_LAUNCH = "css" in inspect.signature(gr.Blocks.launch).parameters
_blocks_kw = {} if _STYLE_IN_LAUNCH else {"css": CSS, "theme": THEME}
_launch_kw = {"css": CSS, "theme": THEME} if _STYLE_IN_LAUNCH else {}

with gr.Blocks(title="LipSync", **_blocks_kw) as demo:
    gr.Markdown("# 🎙️ LipSync\nFais parler une photo ou une vidéo avec ton propre audio — gratuit, "
                "même pour des vidéos de 20 minutes.", elem_id="titre")
    with gr.Tab("Nouveau projet"):
        name = gr.Textbox(label="Nom du projet", placeholder="ex : pere_menas_episode_12")
        with gr.Row():
            face = gr.File(label="Visage : photo ou vidéo", file_types=["image", "video"])
            audio = gr.File(label="Ton audio (mp3, wav, m4a…)", file_types=["audio"])
        with gr.Accordion("Réglages (facultatif)", open=False):
            chunk = gr.Slider(15, 90, value=30, step=5, label="Taille des morceaux (secondes)",
                              info="30 s est le plus sûr sur Colab gratuit.")
            res = gr.Radio(list(RES), value="720p (recommandé)", label="Résolution")
            qual = gr.Radio(list(QUAL), value="Rapide (20 étapes)", label="Qualité")
            guid = gr.Slider(1.0, 3.0, value=1.5, step=0.1, label="Précision des lèvres",
                             info="Plus haut = lèvres plus marquées, mais risque de tremblement.")
        go = gr.Button("🎬 Lancer la synchronisation", variant="primary", size="lg")
        status = gr.Markdown()
        result = gr.File(label="Vidéo finale (clique pour télécharger)")
        go.click(new_job, [name, face, audio, chunk, res, qual, guid], [status, result])

    with gr.Tab("Reprendre un projet"):
        gr.Markdown("Si Colab s'est coupé, relance l'outil puis choisis ton projet : "
                    "les morceaux déjà faits ne sont pas refaits.")
        with gr.Row():
            proj = gr.Dropdown(list_projects(), label="Projet", scale=4)
            refresh = gr.Button("🔄", scale=1)
        qual2 = gr.Radio(list(QUAL), value="Rapide (20 étapes)", label="Qualité")
        guid2 = gr.Slider(1.0, 3.0, value=1.5, step=0.1, label="Précision des lèvres")
        go2 = gr.Button("▶️ Reprendre", variant="primary", size="lg")
        status2 = gr.Markdown()
        result2 = gr.File(label="Vidéo finale")
        refresh.click(lambda: gr.Dropdown(choices=list_projects()), None, proj)
        go2.click(resume_job, [proj, qual2, guid2], [status2, result2])

    gr.Markdown("<small>Utilise des visages générés par IA, le tien, ou ceux de personnes qui ont donné leur accord.</small>")

if __name__ == "__main__":
    demo.queue().launch(share=os.environ.get("LIPSYNC_SHARE", "1") == "1", debug=True,
                        allowed_paths=[BASE], server_port=int(os.environ.get("PORT", "7860")), **_launch_kw)
