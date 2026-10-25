#!/usr/bin/env python3
"""Nettoyage des bords pour les workflows standard (court et long).

Produit une vidéo de masque en niveaux de gris (une image par image de la composition) :
  - 255 partout où se trouve le tissu vert  -> le masque du chromakey y reste EXACTEMENT celui d'avant
  - en dehors du tissu (mur, plafond...)     -> seule ta silhouette (bras, mains) est gardée
La composition multiplie son masque par celui-ci. Si le tissu remplit toute l'image, ou si quoi que ce soit
échoue, aucun masque n'est produit et la vidéo est traitée exactement comme avant.

Sortie : code 0 + fichier --out si un masque est utile ; code 3 si inutile (tissu sur toute l'image).
"""
import argparse, os, subprocess, sys, time
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from beauty import analyze_cloth, smoothstep, log  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--ss", default="")
    ap.add_argument("--len", default="")
    ap.add_argument("--w", type=int, required=True)
    ap.add_argument("--h", type=int, required=True)
    ap.add_argument("--fps", required=True)
    ap.add_argument("--hdr", default="false")
    ap.add_argument("--ek", type=float, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    W, H = a.w, a.h
    TM = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
          "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,") if a.hdr == "true" else ""

    zone = analyze_cloth(a.src, W, H, TM, a.ek, seg_model=a.model)
    if zone is None:
        log("Tissu vert sur toute l'image : rien à nettoyer, traitement identique à avant.")
        sys.exit(3)
    zone8 = np.clip(zone * 255.0 + 0.5, 0, 255).astype(np.uint8)
    zone8[zone > 0.999] = 255
    log("Zone du tissu mesurée : %.0f %% de l'image, le reste (mur...) sera remplacé par le fond." % (100.0 * (zone8 == 255).mean()))

    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision
    seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
        base_options=mpp.BaseOptions(model_asset_path=a.model), running_mode=vision.RunningMode.VIDEO,
        output_confidence_masks=True, output_category_mask=False))

    k = 640.0 / max(W, H)
    sw, sh = int(round(W * k / 2) * 2), int(round(H * k / 2) * 2)
    f_num, f_den = (a.fps.split("/") + ["1"])[:2]
    fps = float(f_num) / float(f_den)
    seek = ["-ss", a.ss] if a.ss not in ("", "0", "0.0") else []
    dur = ["-t", a.len] if a.len else []
    # Mêmes filtres que la composition (même nombre d'images), mais lu directement en petite taille
    cmd_in = ["ffmpeg", "-v", "error", *seek, *dur, "-i", a.src, "-vf",
              "%sscale=%d:%d,setsar=1,fps=%s,scale=%d:%d:flags=area,format=rgb24" % (TM, W, H, a.fps, sw, sh),
              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    cmd_out = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "%dx%d" % (W, H),
               "-framerate", a.fps, "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", a.out]
    pin = subprocess.Popen(cmd_in, stdout=subprocess.PIPE)
    pout = subprocess.Popen(cmd_out, stdin=subprocess.PIPE)
    n_max = int(np.floor(float(a.len) * fps + 0.5)) if a.len else None
    fsz = sw * sh * 3
    n, last, hist, t0 = 0, None, [], time.time()
    outside = zone8 < 255
    try:
        while n_max is None or n < n_max:
            buf = pin.stdout.read(fsz)
            if len(buf) < fsz:
                break
            small = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
            if n % 2 == 0 or last is None:                              # silhouette une image sur deux
                try:
                    r = seg.segment_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(small)),
                                              int(round(n * 1000.0 / fps)))
                    p = 1.0 - np.squeeze(r.confidence_masks[0].numpy_view()).astype(np.float32)
                    g = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
                    try:
                        p = cv2.ximgproc.guidedFilter(g, p, 3, 2e-3)
                    except Exception:
                        pass
                    last = smoothstep(p, 0.25, 0.55).astype(np.float32)
                except Exception:
                    last = np.ones((sh, sw), np.float32)                # en cas de doute : on garde tout
            hist.append(last)
            if len(hist) > 2:
                hist.pop(0)
            p = np.maximum.reduce(hist)
            p = cv2.dilate(p, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
            p = cv2.GaussianBlur(p, (0, 0), 0.8)
            pf = cv2.resize(p, (W, H), interpolation=cv2.INTER_LINEAR)
            keep = zone8.copy()                                         # 255 sur le tissu : masque inchangé
            keep[outside] = np.maximum(zone8[outside], np.clip(pf[outside] * 255.0 + 0.5, 0, 255).astype(np.uint8))
            pout.stdin.write(keep.tobytes())
            n += 1
            if n % 1800 == 0:
                log("  %d images (%.1f i/s)" % (n, n / max(time.time() - t0, 1e-6)))
    finally:
        pout.stdin.close()
        rc = pout.wait()
        pin.stdout.close()
        pin.wait()
    log("Masque des bords : %d images en %.0f s" % (n, time.time() - t0))
    sys.stdout.flush()
    os._exit(0 if (rc == 0 and n > 0) else 1)


if __name__ == "__main__":
    main()
