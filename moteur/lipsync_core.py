"""Préparation, découpage et assemblage des vidéos pour la synchro labiale.

Aucune dépendance au GPU ici : uniquement ffmpeg. Le modèle tourne dans
lipsync_runner.py.
"""
import json
import math
import os
import shutil
import subprocess

FPS = 25
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
BOOMERANG_MAX_SEC = 30  # au-delà, simple boucle (l'inversion prendrait trop de mémoire)


def run(cmd):
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg a échoué :\n" + " ".join(cmd) + "\n" + res.stderr[-1500:])
    return res


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", path]).stdout.strip()
    return float(out)


def frame_count(path):
    out = run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
               "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", path]).stdout.strip()
    return int(out)


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXT


def _scale(max_h):
    return f"scale=-2:'min({max_h},trunc(ih/2)*2)'"


ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]


def make_base(face_path, out_path, max_h, audio_sec):
    """Vidéo de base à 25 i/s, redimensionnée. Boomerang (aller-retour) si courte."""
    tmp = out_path + ".fwd.mp4"
    if is_image(face_path):
        run(["ffmpeg", "-y", "-loop", "1", "-i", face_path, "-t", "4", "-r", str(FPS),
             "-vf", _scale(max_h), *ENC, "-an", out_path])
        return out_path
    run(["ffmpeg", "-y", "-i", face_path, "-vf", f"fps={FPS}," + _scale(max_h), *ENC, "-an", tmp])
    src_sec = duration(tmp)
    if src_sec >= audio_sec or src_sec > BOOMERANG_MAX_SEC:
        shutil.move(tmp, out_path)
        return out_path
    # aller + retour pour que la boucle ne « saute » pas
    run(["ffmpeg", "-y", "-i", tmp, "-filter_complex",
         "[0:v]split[a][b];[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];[a][r]concat=n=2:v=1[v]",
         "-map", "[v]", *ENC, out_path])
    os.remove(tmp)
    return out_path


def plan_chunks(total_frames, chunk_sec):
    size = max(FPS * 5, int(round(chunk_sec * FPS)))
    chunks, start, i = [], 0, 0
    while start < total_frames:
        n = min(size, total_frames - start)
        chunks.append({"idx": i, "start": start, "frames": n})
        start += n
        i += 1
    return chunks


def prepare_project(proj_dir, work_dir, face_path, audio_path, chunk_sec, max_h, log=print):
    """Crée la frise vidéo complète et les morceaux à traiter. Renvoie le manifeste."""
    os.makedirs(work_dir, exist_ok=True)
    done_dir = os.path.join(proj_dir, "morceaux_finis")
    os.makedirs(done_dir, exist_ok=True)

    audio_sec = duration(audio_path)
    total_frames = int(math.ceil(audio_sec * FPS))
    chunks = plan_chunks(total_frames, chunk_sec)

    manifest_path = os.path.join(proj_dir, "manifest.json")
    if os.path.exists(manifest_path):
        old = json.load(open(manifest_path))
        if old.get("chunks") == chunks and old.get("max_h") == max_h:
            log("Projet existant retrouvé : les morceaux déjà terminés seront gardés.")
        else:
            log("Réglages différents de la dernière fois : on recommence les morceaux.")
            shutil.rmtree(done_dir)
            os.makedirs(done_dir)

    todo = [c for c in chunks if not os.path.exists(os.path.join(done_dir, f"morceau_{c['idx']:04d}.mp4"))]
    log(f"Audio : {audio_sec/60:.1f} min · {len(chunks)} morceaux · {len(chunks)-len(todo)} déjà faits.")

    jobs = []
    if todo:
        log("Préparation de la vidéo de base…")
        base = make_base(face_path, os.path.join(work_dir, "base.mp4"), max_h, audio_sec)
        timeline = os.path.join(work_dir, "frise.mp4")
        if not (os.path.exists(timeline) and frame_count(timeline) == total_frames):
            log("Création de la frise vidéo à la longueur de l'audio…")
            run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", base, "-frames:v", str(total_frames),
                 "-r", str(FPS), *ENC, "-an", timeline])
        seg_dir = os.path.join(work_dir, "segments")
        os.makedirs(seg_dir, exist_ok=True)
        for c in todo:
            t0, dur = c["start"] / FPS, c["frames"] / FPS
            v = os.path.join(seg_dir, f"v_{c['idx']:04d}.mp4")
            a = os.path.join(seg_dir, f"a_{c['idx']:04d}.wav")
            run(["ffmpeg", "-y", "-ss", f"{t0:.4f}", "-i", timeline, "-frames:v", str(c["frames"]),
                 "-r", str(FPS), *ENC, "-an", v])
            run(["ffmpeg", "-y", "-ss", f"{t0:.4f}", "-i", audio_path, "-af", "apad",
                 "-t", f"{dur:.4f}", "-ar", "16000", "-ac", "1", a])
            jobs.append({"idx": c["idx"], "frames": c["frames"], "video": v, "audio": a,
                         "out": os.path.join(done_dir, f"morceau_{c['idx']:04d}.mp4")})

    manifest = {"chunks": chunks, "max_h": max_h, "chunk_sec": chunk_sec,
                "audio": audio_path, "face": face_path, "total_frames": total_frames}
    json.dump(manifest, open(manifest_path, "w"), indent=1)
    json.dump(jobs, open(os.path.join(work_dir, "jobs.json"), "w"), indent=1)
    return manifest, jobs


def assemble(proj_dir, work_dir, manifest, out_path, log=print):
    done_dir = os.path.join(proj_dir, "morceaux_finis")
    parts = [os.path.join(done_dir, f"morceau_{c['idx']:04d}.mp4") for c in manifest["chunks"]]
    missing = [p for p in parts if not os.path.exists(p)]
    if missing:
        raise RuntimeError(f"{len(missing)} morceaux manquent encore.")
    lst = os.path.join(work_dir, "liste.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write("file '" + p.replace("'", "'\\''") + "'\n")
    video_only = os.path.join(work_dir, "video_seule.mp4")
    log("Assemblage des morceaux…")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video_only])
    log("Ajout de l'audio original en pleine qualité…")
    tmp = out_path + ".tmp.mp4"
    run(["ffmpeg", "-y", "-i", video_only, "-i", manifest["audio"], "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp])
    shutil.move(tmp, out_path)
    return out_path
