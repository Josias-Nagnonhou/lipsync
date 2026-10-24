"""Construit LipSync.ipynb en y intégrant les trois fichiers de l'outil."""
import json
import os

import nbformat as nbf

HERE = os.path.dirname(os.path.abspath(__file__))
COMMIT = "a229c3948406bc2cf6eaf4873e662e70c6a04746"
files = {n: open(os.path.join(HERE, n)).read() for n in ("lipsync_core.py", "lipsync_runner.py", "lipsync_app.py")}

intro = """# 🎙️ LipSync — synchronisation labiale gratuite

**Fais parler une photo ou une vidéo avec ton propre audio, même pour 20 minutes.**

### Comment l'utiliser (3 clics)
1. Menu **Exécution → Modifier le type d'exécution** → choisis **GPU T4** → **Enregistrer**. *(à faire une seule fois)*
2. Clique sur le bouton **▶** de la cellule ci-dessous.
3. Autorise l'accès à Google Drive quand la fenêtre s'ouvre, puis attends 5 à 10 minutes la première fois.
   Un lien **https://….gradio.live** apparaît : **clique dessus**, ton outil s'ouvre.

### Bon à savoir
- Tes vidéos sont enregistrées dans **Google Drive → LipSync → projets**.
- Si Colab se coupe, relance la cellule et utilise l'onglet **« Reprendre un projet »** : les morceaux déjà faits ne sont pas refaits.
- Laisse cet onglet Colab ouvert pendant le traitement.
"""

setup = '''#@title ▶️ Lancer LipSync
SAUVEGARDER_SUR_DRIVE = True  #@param {type:"boolean"}

import os, subprocess, sys, json, textwrap

def etape(msg):
    print("\\n" + "=" * 60 + "\\n" + msg + "\\n" + "=" * 60, flush=True)

# 1. Carte graphique
try:
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout.strip()
except FileNotFoundError:
    gpu = ""
if not gpu:
    raise SystemExit("❌ Pas de carte graphique. Menu Exécution → Modifier le type d'exécution → GPU T4 → "
                     "Enregistrer, puis relance cette cellule. Si la T4 n'est pas disponible, réessaie plus tard.")
gpu_name, gpu_mem = [x.strip() for x in gpu.splitlines()[0].split(",")]
gpu_mem = int(gpu_mem)
etape(f"✅ Carte graphique : {gpu_name} ({gpu_mem // 1024} Go)")

# 2. Google Drive
BASE = "/content/LipSync"
if SAUVEGARDER_SUR_DRIVE:
    from google.colab import drive
    drive.mount("/content/drive")
    BASE = "/content/drive/MyDrive/LipSync"
os.makedirs(BASE, exist_ok=True)

# 3. Installation (une seule fois par session)
REPO = "/content/LatentSync"
MARK = "/content/.lipsync_ok"
if not os.path.exists(MARK):
    etape("⏳ Installation (5 à 10 minutes la première fois)…")
    if not os.path.exists(REPO):
        subprocess.run(["git", "clone", "-q", "https://github.com/bytedance/LatentSync.git", REPO], check=True)
        subprocess.run(["git", "-C", REPO, "checkout", "-q", "__COMMIT__"], check=True)
    pkgs = ["diffusers==0.32.2", "transformers==4.48.0", "decord==0.6.0", "einops", "omegaconf==2.3.0",
            "kornia==0.8.0", "insightface==0.7.3", "onnxruntime-gpu", "DeepCache==0.1.1", "ffmpeg-python",
            "imageio", "imageio-ffmpeg", "soundfile", "accelerate", "gradio"]
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise SystemExit("❌ L'installation a échoué. Fais une capture d'écran de ce message et envoie-la à Claude.")
    test = subprocess.run([sys.executable, "-c",
        "import decord, insightface, onnxruntime, diffusers, DeepCache, kornia, soundfile, ffmpeg, gradio"],
        capture_output=True, text=True, cwd=REPO)
    if test.returncode != 0:
        print(test.stderr[-3000:])
        raise SystemExit("❌ Un module ne se charge pas. Fais une capture d'écran et envoie-la à Claude.")
    open(MARK, "w").write("ok")
else:
    etape("✅ Déjà installé")

# 4. Modèle : 1.6 (plus net) si la carte a assez de mémoire, sinon 1.5 (T4 gratuite)
if gpu_mem >= 20000:
    HF_REPO, CONFIG, VERSION = "ByteDance/LatentSync-1.6", "configs/unet/stage2_512.yaml", "1.6"
else:
    HF_REPO, CONFIG, VERSION = "ByteDance/LatentSync-1.5", "configs/unet/stage2.yaml", "1.5"
ck = os.path.join(REPO, "checkpoints")
vfile = os.path.join(ck, "version.txt")
if not (os.path.exists(os.path.join(ck, "latentsync_unet.pt")) and os.path.exists(vfile)
        and open(vfile).read() == VERSION):
    etape(f"⏳ Téléchargement du modèle LatentSync {VERSION}…")
    from huggingface_hub import hf_hub_download
    for f in ("latentsync_unet.pt", "whisper/tiny.pt"):
        hf_hub_download(HF_REPO, f, local_dir=ck)
    open(vfile, "w").write(VERSION)
etape(f"✅ Modèle LatentSync {VERSION} prêt")

# 5. Fichiers de l'outil
APP = "/content/lipsync_outil"
os.makedirs(APP, exist_ok=True)
SOURCES = __SOURCES__
for name, code in SOURCES.items():
    open(os.path.join(APP, name), "w").write(code)

# 6. Lancement
os.environ.update({"LIPSYNC_REPO": REPO, "LIPSYNC_BASE": BASE, "LIPSYNC_CONFIG": CONFIG,
                   "LIPSYNC_WORK": "/content/lipsync_travail"})
sys.path.insert(0, APP)
for m in ("lipsync_app", "lipsync_core"):
    sys.modules.pop(m, None)
import lipsync_app
etape("🚀 Clique sur le lien « Running on public URL » ci-dessous (https://….gradio.live)")
lipsync_app.demo.queue().launch(share=True, debug=True, allowed_paths=[BASE], **lipsync_app._launch_kw)
'''.replace("__COMMIT__", COMMIT).replace("__SOURCES__", json.dumps(files, ensure_ascii=False))

nb = nbf.v4.new_notebook()
nb.cells = [nbf.v4.new_markdown_cell(intro), nbf.v4.new_code_cell(setup)]
nb.cells[1].metadata = {"cellView": "form"}
nb.metadata = {"accelerator": "GPU", "colab": {"name": "LipSync.ipynb", "provenance": [], "gpuType": "T4"},
               "kernelspec": {"name": "python3", "display_name": "Python 3"}}
out = os.path.join(HERE, "LipSync.ipynb")
nbf.write(nb, out)
print("écrit :", out, os.path.getsize(out), "octets")
