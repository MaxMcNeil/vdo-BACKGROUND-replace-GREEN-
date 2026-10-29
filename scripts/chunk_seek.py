#!/usr/bin/env python3
"""Lecture d'un morceau de vidéo, image pour image identique à une lecture de la vidéo entière.

Les vidéos de téléphone ont souvent une piste image qui démarre quelques ms après le son (ou des images à intervalles
irréguliers) : un saut direct (-ss) décalait alors d'une image le début de chaque morceau -> au raccord, une image
sautée puis une image en double (micro-saccade toutes les N minutes).
Ici : temps d'origine conservés (-copyts), 1 s de pré-lecture, grille d'images ancrée sur 0 pour TOUS les morceaux,
puis découpe exacte au début du morceau.

Utilisé par beauty.py, edge_mask.py et le workflow des vidéos longues (même calcul partout = mêmes images).
Ligne de commande (pour bash) :  python3 scripts/chunk_seek.py <début s> <durée s> <fps ex. 30000/1001>
  -> ligne 1 : options d'entrée ffmpeg (séparées par des espaces) ; ligne 2 : début du filtre vidéo (finit par « , »)
Seule la bibliothèque standard est utilisée."""
import sys


def fps_value(fps_str):
    n, d = (str(fps_str).split("/") + ["1"])[:2]
    return float(n) / float(d)


def chunk_input(ss, ln, fps_str, fps=None):
    """Renvoie (options d'entrée ffmpeg, début du filtre vidéo). Le son n'est PAS concerné (lu à part, -ss direct)."""
    fps = fps or fps_value(fps_str)
    try:
        s = float(ss or 0)
    except ValueError:
        s = 0.0
    pre = max(0.0, s - 1.0)
    args = ["-copyts"] + (["-ss", "%.6f" % pre] if pre > 0 else [])
    if ln:
        args += ["-t", "%.6f" % (float(ln) + (s - pre) + 1.0)]
    vf = "fps=%s:start_time=0," % fps_str
    if s > 0:
        vf += "trim=start=%.6f," % max(0.0, s - 0.5 / fps)
    vf += "setpts=PTS-STARTPTS,"
    return args, vf


if __name__ == "__main__":
    a, v = chunk_input(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "", sys.argv[3])
    print(" ".join(a))
    print(v)
