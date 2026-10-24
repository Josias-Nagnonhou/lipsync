"""Charge LatentSync une seule fois puis traite tous les morceaux de jobs.json.

Usage : python lipsync_runner.py JOBS.json --steps 20 --guidance 1.5 --config ... --ckpt ...
Mettre LIPSYNC_FAKE=1 pour tester sans GPU (copie la vidéo au lieu d'utiliser le modèle).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback

FPS = 25


def say(msg):
    print(msg, flush=True)


def finalize(raw, out, frames):
    """Force exactement `frames` images pour que l'assemblage reste synchro."""
    tmp_local = raw + ".exact.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
           "-vf", f"fps={FPS},tpad=stop_mode=clone:stop=100", "-frames:v", str(frames),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", tmp_local]
    subprocess.run(cmd, check=True)
    tmp_out = out + ".part.mp4"
    shutil.copy(tmp_local, tmp_out)
    os.replace(tmp_out, out)  # le morceau n'est « fini » qu'une fois entièrement copié
    os.remove(tmp_local)


def load_pipeline(config_path, ckpt_path, deepcache):
    import torch
    from omegaconf import OmegaConf
    from diffusers import AutoencoderKL, DDIMScheduler
    from latentsync.models.unet import UNet3DConditionModel
    from latentsync.pipelines.lipsync_pipeline import LipsyncPipeline
    from latentsync.whisper.audio2feature import Audio2Feature

    config = OmegaConf.load(config_path)
    # La T4 gratuite (capacité 7.5) gère le float16 : moins de mémoire, plus rapide.
    dtype = torch.float16 if torch.cuda.get_device_capability()[0] >= 7 else torch.float32
    scheduler = DDIMScheduler.from_pretrained("configs")
    whisper = "checkpoints/whisper/small.pt" if config.model.cross_attention_dim == 768 else "checkpoints/whisper/tiny.pt"
    audio_encoder = Audio2Feature(model_path=whisper, device="cuda", num_frames=config.data.num_frames,
                                  audio_feat_length=config.data.audio_feat_length)
    vae = AutoencoderKL.from_pretrained("stabilityai/sd-vae-ft-mse", torch_dtype=dtype)
    vae.config.scaling_factor = 0.18215
    vae.config.shift_factor = 0
    unet, _ = UNet3DConditionModel.from_pretrained(OmegaConf.to_container(config.model), ckpt_path, device="cpu")
    unet = unet.to(dtype=dtype)
    pipe = LipsyncPipeline(vae=vae, audio_encoder=audio_encoder, unet=unet, scheduler=scheduler).to("cuda")
    if deepcache:
        from DeepCache import DeepCacheSDHelper
        helper = DeepCacheSDHelper(pipe=pipe)
        helper.set_params(cache_interval=3, cache_branch_id=0)
        helper.enable()
    return pipe, config, dtype


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs")
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--guidance", type=float, default=1.5)
    ap.add_argument("--config", default="configs/unet/stage2.yaml")
    ap.add_argument("--ckpt", default="checkpoints/latentsync_unet.pt")
    ap.add_argument("--seed", type=int, default=1247)
    ap.add_argument("--no-deepcache", action="store_true")
    args = ap.parse_args()

    jobs = json.load(open(args.jobs))
    todo = [j for j in jobs if not os.path.exists(j["out"])]
    total = len(todo)
    if not total:
        say("ALLDONE")
        return
    fake = os.environ.get("LIPSYNC_FAKE") == "1"

    say("STATUS Chargement du modèle (1 à 2 minutes)…")
    if not fake:
        pipe, config, dtype = load_pipeline(args.config, args.ckpt, not args.no_deepcache)
        from accelerate.utils import set_seed

    for k, job in enumerate(todo, 1):
        say(f"START {k} {total} {job['idx']}")
        t0 = time.time()
        raw = job["video"] + ".raw.mp4"
        try:
            if fake:
                # imite le modèle : sortie avec 2 images de trop
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", job["video"], "-i", job["audio"],
                                "-vf", "tpad=stop_mode=clone:stop=2", "-c:v", "libx264", "-preset", "veryfast",
                                "-c:a", "aac", raw], check=True)
            else:
                if args.seed != -1:
                    set_seed(args.seed)
                pipe(video_path=job["video"], audio_path=job["audio"], video_out_path=raw,
                     num_frames=config.data.num_frames, num_inference_steps=args.steps,
                     guidance_scale=args.guidance, weight_dtype=dtype,
                     width=config.data.resolution, height=config.data.resolution,
                     mask_image_path=config.data.mask_image_path, temp_dir="temp")
            finalize(raw, job["out"], job["frames"])
            os.remove(raw)
            for p in (job["video"], job["audio"]):
                if os.path.exists(p):
                    os.remove(p)
        except Exception as e:
            msg = str(e)
            say("TRACE " + traceback.format_exc().replace("\n", " | "))
            if "face" in msg.lower():
                say(f"ERROR FACE {job['idx']}")
            elif "out of memory" in msg.lower():
                say("ERROR OOM")
            else:
                say("ERROR OTHER " + msg[:300].replace("\n", " "))
            sys.exit(1)
        say(f"DONE {k} {total} {time.time() - t0:.1f}")
    say("ALLDONE")


if __name__ == "__main__":
    main()
