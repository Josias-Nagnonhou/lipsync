"""Génère LipSync.ipynb : le carnet Colab qui allume le moteur."""
import os

import nbformat as nbf

ICI = os.path.dirname(os.path.abspath(__file__))
SITE = "https://lipsync-6cu9.onrender.com"
LATENTSYNC_COMMIT = "a229c3948406bc2cf6eaf4873e662e70c6a04746"

intro = f"""# Moteur LipSync

Cette page allume le moteur de ton site **[LipSync]({SITE})**.

1. La première fois seulement : menu **Exécution → Modifier le type d'exécution → GPU T4 → Enregistrer**.
2. Clique sur **▶** juste en dessous et autorise Google Drive.
3. Quand le bouton **« Ouvrir LipSync »** apparaît, clique dessus. Tout le reste se passe sur le site.

Laisse cet onglet ouvert pendant que tes vidéos se fabriquent.
"""

code = r'''#@title ▶ Allumer le moteur LipSync
SITE = "__SITE__"
import os, re, subprocess, sys, time
from IPython.display import HTML, display, clear_output

def info(msg):
    clear_output(wait=True)
    display(HTML(f"<div style='font:600 18px system-ui;padding:14px 0'>{msg}</div>"))

# Carte graphique
gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                     capture_output=True, text=True).stdout.strip() if os.path.exists("/usr/bin/nvidia-smi") else ""
if not gpu:
    info("❌ Pas de carte graphique. Menu <b>Exécution → Modifier le type d'exécution → GPU T4 → Enregistrer</b>, "
         "puis clique à nouveau sur ▶. Si la T4 n'est pas disponible, réessaie un peu plus tard.")
    raise SystemExit
gpu_name, gpu_mem = [x.strip() for x in gpu.splitlines()[0].split(",")]
gpu_mem = int(gpu_mem)

# Google Drive (tes vidéos y sont rangées)
from google.colab import drive
drive.mount("/content/drive")
BASE = "/content/drive/MyDrive/LipSync"
os.makedirs(BASE, exist_ok=True)

REPO, OUTIL = "/content/LatentSync", "/content/lipsync"
if not os.path.exists("/content/.lipsync_ok"):
    info("⏳ Installation du moteur… (5 à 10 minutes la première fois)")
    subprocess.run(["git", "clone", "-q", "https://github.com/bytedance/LatentSync.git", REPO], check=True)
    subprocess.run(["git", "-C", REPO, "checkout", "-q", "__COMMIT__"], check=True)
    pkgs = ["diffusers==0.32.2", "transformers==4.48.0", "decord==0.6.0", "einops", "omegaconf==2.3.0",
            "kornia==0.8.0", "insightface==0.7.3", "onnxruntime-gpu", "DeepCache==0.1.1", "ffmpeg-python",
            "imageio", "imageio-ffmpeg", "soundfile", "accelerate", "fastapi", "uvicorn"]
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-3000:]); info("❌ L'installation a échoué. Fais une capture d'écran et envoie-la à Claude."); raise SystemExit
    t = subprocess.run([sys.executable, "-c", "import decord, insightface, onnxruntime, diffusers, DeepCache, kornia, fastapi"],
                       capture_output=True, text=True, cwd=REPO)
    if t.returncode:
        print(t.stderr[-3000:]); info("❌ Un module ne se charge pas. Fais une capture d'écran et envoie-la à Claude."); raise SystemExit
    subprocess.run(["wget", "-q", "-O", "/usr/local/bin/cloudflared",
                    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"], check=True)
    os.chmod("/usr/local/bin/cloudflared", 0o755)
    open("/content/.lipsync_ok", "w").write("ok")

# Code du moteur (toujours la dernière version)
if os.path.exists(OUTIL):
    subprocess.run(["git", "-C", OUTIL, "pull", "-q"])
else:
    subprocess.run(["git", "clone", "-q", "https://github.com/Josias-Nagnonhou/lipsync.git", OUTIL], check=True)

# Modèle : 1.6 si la carte est assez grande, sinon 1.5 (T4 gratuite)
if gpu_mem >= 20000:
    HF, CONFIG, VERSION = "ByteDance/LatentSync-1.6", "configs/unet/stage2_512.yaml", "1.6"
else:
    HF, CONFIG, VERSION = "ByteDance/LatentSync-1.5", "configs/unet/stage2.yaml", "1.5"
ck = os.path.join(REPO, "checkpoints")
vf = os.path.join(ck, "version.txt")
if not (os.path.exists(os.path.join(ck, "latentsync_unet.pt")) and os.path.exists(vf) and open(vf).read() == VERSION):
    info(f"⏳ Téléchargement du modèle {VERSION}…")
    from huggingface_hub import hf_hub_download
    for f in ("latentsync_unet.pt", "whisper/tiny.pt"):
        hf_hub_download(HF, f, local_dir=ck)
    open(vf, "w").write(VERSION)

# Démarrage du moteur
info("⏳ Démarrage du moteur…")
subprocess.run("pkill -f 'uvicorn moteur:app'; pkill -f cloudflared", shell=True)
env = dict(os.environ, LIPSYNC_REPO=REPO, LIPSYNC_BASE=BASE, LIPSYNC_CONFIG=CONFIG, LIPSYNC_VERSION=VERSION,
           LIPSYNC_GPU=gpu_name.replace("Tesla ", ""), LIPSYNC_WORK="/content/lipsync_travail")
moteur = subprocess.Popen([sys.executable, "-m", "uvicorn", "moteur:app", "--host", "127.0.0.1", "--port", "8000"],
                          cwd=os.path.join(OUTIL, "moteur"), env=env,
                          stdout=open("/content/moteur.log", "w"), stderr=subprocess.STDOUT)
tunnel = subprocess.Popen(["cloudflared", "tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
adresse = None
debut = time.time()
for ligne in tunnel.stdout:
    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", ligne)
    if m:
        adresse = m.group(0); break
    if time.time() - debut > 90:
        break
if not adresse:
    info("❌ Le moteur n'a pas pu se connecter à internet. Clique à nouveau sur ▶."); raise SystemExit

import urllib.request
for _ in range(60):
    try:
        urllib.request.urlopen(adresse + "/api/etat", timeout=5); break
    except Exception:
        time.sleep(2)

lien = f"{SITE}/?moteur={adresse}"
clear_output(wait=True)
display(HTML(f"""
<div style="font-family:system-ui;padding:22px;border-radius:18px;background:#1c1626;color:#f3ede3;max-width:560px">
  <div style="font-size:15px;color:#5fcfae;margin-bottom:6px">● Moteur allumé ({gpu_name}, modèle {VERSION})</div>
  <div style="font-size:22px;font-weight:700;margin-bottom:16px">Ton site est prêt</div>
  <a href="{lien}" target="_blank" style="display:inline-block;background:#f2b33d;color:#2a1a00;padding:14px 26px;
     border-radius:12px;font-weight:700;font-size:18px;text-decoration:none">Ouvrir LipSync</a>
  <div style="font-size:13px;color:#a99fb9;margin-top:16px">Laisse cet onglet ouvert pendant le travail.
  Pour arrêter le moteur, clique sur ■ en haut de cette cellule.</div>
</div>"""))

# Garde le moteur en marche ; s'il s'arrête, on le dit clairement.
import threading
threading.Thread(target=lambda: [None for _ in tunnel.stdout], daemon=True).start()  # vide les messages du tunnel
try:
    while moteur.poll() is None and tunnel.poll() is None:
        time.sleep(5)
    print(open("/content/moteur.log").read()[-3000:])
    info("❌ Le moteur s'est arrêté. Clique à nouveau sur ▶. Tes vidéos commencées sont gardées.")
except KeyboardInterrupt:
    moteur.terminate(); tunnel.terminate()
    info("Moteur arrêté.")
'''.replace("__SITE__", SITE).replace("__COMMIT__", LATENTSYNC_COMMIT)

nb = nbf.v4.new_notebook()
nb.cells = [nbf.v4.new_markdown_cell(intro), nbf.v4.new_code_cell(code)]
nb.cells[1].metadata = {"cellView": "form"}
nb.metadata = {"accelerator": "GPU", "colab": {"name": "LipSync.ipynb", "provenance": [], "gpuType": "T4"},
               "kernelspec": {"name": "python3", "display_name": "Python 3"}}
nbf.write(nb, os.path.join(ICI, "..", "LipSync.ipynb"))
print("LipSync.ipynb mis à jour")
