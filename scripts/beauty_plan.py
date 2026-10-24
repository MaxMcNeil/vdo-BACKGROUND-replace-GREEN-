#!/usr/bin/env python3
"""Préparation du workflow Beauté : orientation, cadence, HDR, couleur du fond vert, découpage en morceaux.
Mêmes mesures que les autres workflows (mêmes formules), lecture par recherche directe dans la vidéo
(fonctionne aussi sur une vidéo servie par rclone, sans la télécharger entièrement).
Écrit les résultats dans $GITHUB_OUTPUT."""
import json, math, os, subprocess, statistics as st

SRC = os.environ["SRC"]


def probe_json(*args):
    return json.loads(subprocess.check_output(["ffprobe", "-v", "error", *args, "-of", "json", SRC]))


def to_rate(t):
    try:
        a, b = [float(x) for x in t.split("/")] if "/" in t else (float(t), 1.0)
        return a / b if b else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------- orientation, taille de sortie, cadence, HDR
j = probe_json("-select_streams", "v:0", "-show_entries",
               "stream=width,height,color_transfer,avg_frame_rate,r_frame_rate:stream_side_data=rotation")
s = j["streams"][0]
w, h = int(s["width"]), int(s["height"])
rot = 0
for sd in s.get("side_data_list", []):
    if "rotation" in sd:
        rot = int(round(float(sd["rotation"])))
if abs(rot) % 180 == 90:                  # vidéo tournée par métadonnée (téléphone) : on inverse
    w, h = h, w
vertical = h > w
if not vertical and abs(w / float(h) - 16 / 9.0) < 0.012:
    ow, oh = 1920, 1080
else:
    k = min(1.0, 1920.0 / max(w, h))
    ow, oh = int(round(w * k / 2.0)) * 2, int(round(h * k / 2.0)) * 2
hdr = s.get("color_transfer") in ("smpte2084", "arib-std-b67")
rate = to_rate(s.get("avg_frame_rate", ""))
if not 5.0 <= rate <= 120.0:
    alt = to_rate(s.get("r_frame_rate", ""))
    rate = alt if 5.0 <= alt <= 120.0 else 30.0
std = {"24000/1001": 23.976, "24": 24.0, "25": 25.0, "30000/1001": 29.97, "30": 30.0, "50": 50.0, "60000/1001": 59.94, "60": 60.0}
best = min(std.items(), key=lambda kv: abs(kv[1] - rate))
fps = best[0] if abs(best[1] - rate) / rate < 0.01 else "%.3f" % rate
fps_val = to_rate(fps)

dur = float(probe_json("-show_entries", "format=duration")["format"]["duration"])
has_audio = "true" if probe_json("-select_streams", "a", "-show_entries", "stream=index").get("streams") else "false"
print("Vidéo %dx%d -> %s, sortie %dx%d, %s i/s, %.1f s%s, son : %s" % (
    w, h, "VERTICALE" if vertical else "horizontale", ow, oh, fps, dur, ", HDR (conversion SDR)" if hdr else "",
    "oui" if has_audio == "true" else "non"))

# ---------------------------------------------------------------- couleur du fond vert (mêmes formules que les autres workflows)
TM = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
      "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,") if hdr else ""
W, H, N = 192, 108, 12
fs = W * H * 3
frames = []
for k in range(N):
    t = dur * (k + 0.5) / N
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", SRC, "-frames:v", "1",
                          "-vf", TM + "scale=%d:%d:flags=area,format=rgb24" % (W, H), "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    if len(raw) == fs:
        frames.append(raw)
if not frames:
    raise SystemExit("Impossible de lire des images de la vidéo (durée incohérente ou fichier corrompu)")


def exg(p):
    return p[1] - (p[0] + p[2]) / 2


def uvmag(r, g, b):
    u = -0.169 * r - 0.331 * g + 0.5 * b
    v = 0.5 * r - 0.419 * g - 0.081 * b
    return math.hypot(u, v)


override = os.environ.get("KEY_OVERRIDE", "").strip().lstrip("#").upper()
if len(override) == 6:
    R, G, B = [int(override[i:i + 2], 16) for i in (0, 2, 4)]
    print("Couleur imposée manuellement :", override)
else:
    px = []
    for f in frames:
        for y in range(H):
            for x in range(W):
                if y < H * 0.15 or ((x < W * 0.06 or x >= W * 0.94) and y < H * 0.60):
                    i = (y * W + x) * 3
                    px.append((f[i], f[i + 1], f[i + 2]))
    m = [st.median(p[i] for p in px) for i in range(3)]
    good = [p for p in px if abs(exg(p) - exg(m)) < 40 and p[1] > p[0] and p[1] > p[2]]
    if len(good) < 0.3 * len(px):
        raise SystemExit("Mesure du fond vert peu fiable : relance avec key_color renseigné")
    R, G, B = [round(st.median(p[i] for p in good)) for i in range(3)]

EK = max(G - (R + B) / 2, 20.0)
ref = uvmag(19, 157, 116)
SIM = min(max(0.10 * uvmag(R, G, B) / ref, 0.05), 0.16)
lum = (R + G + B) / 3
THR = min(max(100 * lum / 98.0, 70.0), 150.0)
hexk = "%02X%02X%02X" % (R, G, B)
print("Fond : #%s  EK=%.1f  tolérance=%.3f  seuil=%.1f" % (hexk, EK, SIM, THR))

# ---------------------------------------------------------------- découpage (traitement en parallèle)
preview = (os.environ.get("PREVIEW") or "").strip()
try:
    pv = float(preview) if preview else 0.0
except ValueError:
    pv = 0.0
if pv > 0:
    total = min(pv, dur)
    include = [{"idx": 0, "ss": 0.0, "len": round(total, 3)}]
    print("Test rapide : %.1f premières secondes" % total)
else:
    total = dur
    raw_cm = (os.environ.get("CHUNK_MINUTES") or "").strip()
    try:
        L = max(float(raw_cm.replace(",", ".")), 0.1) * 60 if raw_cm else None
    except ValueError:
        L = None
    if L is None:                         # automatique : ~16 morceaux de 1 à 4 minutes
        L = min(max(dur / 16.0, 60.0), 240.0)
        if dur <= L * 1.3:                # vidéo courte : un seul morceau
            L = dur + 1.0
    L = max(L, dur / 250.0)               # jamais plus de 256 morceaux (limite GitHub)
    L = max(1, round(L * fps_val)) / fps_val   # nombre entier d'images par morceau : pas de décalage son / image
    include, start, idx = [], 0.0, 0
    while start < dur - 0.05:
        ln = min(L, dur - start)
        include.append({"idx": idx, "ss": round(start, 6), "len": round(ln, 6)})
        start += L
        idx += 1
    print("Durée %.1f s -> %d morceau(x) de %.0f s max, traités en parallèle" % (dur, len(include), min(L, dur)))

base = os.environ.get("OUT_BASENAME") or "video"
out = {
    "matrix": json.dumps({"include": include}), "n_chunks": len(include),
    "key": "%d,%d,%d,%.2f,%.3f,%.1f,%s" % (R, G, B, EK, SIM, THR, hexk),
    "fps": fps, "has_audio": has_audio, "vertical": "true" if vertical else "false",
    "out_w": ow, "out_h": oh, "hdr": "true" if hdr else "false",
    "out_name": base + ("_beaute-test.mp4" if pv > 0 else "_beaute.mp4"),
}
with open(os.environ["GITHUB_OUTPUT"], "a") as o:
    for k, v in out.items():
        o.write("%s=%s\n" % (k, v))
