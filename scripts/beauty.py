#!/usr/bin/env python3
"""Fond vert remplacé + beauté (peau plus claire, yeux recolorés), image par image.

Chaîne :
  ffmpeg (lecture)  : même chromakey / même masque que les autres workflows -> images RGBA
  ce script         : dé-mélange du vert (même formule que unmix.cube), peau, yeux, nettoyage des bords
  ffmpeg (écriture) : incrustation sur l'image de fond, encodage x264 (mêmes réglages que les autres workflows)

Peau : modèle de segmentation MediaPipe « selfie multiclass » (classes peau du visage / peau du corps,
séparées des vêtements, des cheveux et du fond), affiné par la couleur réelle de TA peau, mesurée sur la vidéo.
Yeux : MediaPipe FaceLandmarker (iris + paupières), positions lissées dans le temps ; seul l'iris visible,
plus sombre que le blanc de l'oeil, est recoloré. Pupille, reflets et anneau sombre de l'iris conservés.
"""
import argparse, math, os, subprocess, sys, time
import numpy as np
import cv2

# ---------------------------------------------------------------- utilitaires

try:                                    # une seule version du calcul, partagée avec edge_mask.py et le workflow long
    from chunk_seek import chunk_input  # noqa: E402
except ImportError:                     # repli : copie identique
    def chunk_input(ss, ln, fps_str, fps):
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

def smoothstep(x, a, b):
    t = np.clip((x - a) / float(b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- dé-mélange du vert (identique à mklut.py des autres workflows)
def build_unmix_table(K, EK, THR):
    """Table complète des 256^3 couleurs, même formule que unmix.cube.
    Indexée par l'entier 32 bits d'un pixel RGBA (r | g<<8 | b<<16) ; renvoie le pixel corrigé dans le même format."""
    tab = np.empty((256, 256, 256), np.uint32)                           # index [b, g, r]
    b, g = np.meshgrid(np.arange(256, dtype=np.float32), np.arange(256, dtype=np.float32), indexing="ij")
    for r0 in range(256):
        r = np.full_like(g, r0)
        a = 1.0 - np.maximum(g - (r + b) / 2.0, 0.0) / EK
        a = np.clip(a, 0.4, 1.0)
        bright = (r + g + b) / 3.0 > THR
        rr = np.where(bright, np.clip((r - (1 - a) * K[0]) / a, 0, 255), r)
        gg = np.where(bright, np.clip((g - (1 - a) * K[1]) / a, 0, 255), g)
        bb = np.where(bright, np.clip((b - (1 - a) * K[2]) / a, 0, 255), b)
        gg = np.minimum(gg, (rr + bb) / 2.0)
        bb = np.minimum(bb, rr)
        tab[:, :, r0] = (np.round(rr).astype(np.uint32) | (np.round(gg).astype(np.uint32) << 8)
                         | (np.round(bb).astype(np.uint32) << 16))
    return tab.reshape(-1)


def unmix_smooth(rgb, K, EK, THR):
    """Retrait du vert « lissé » (intérieur de la personne : veste, chemise, barbe).
    Même formule que le retrait habituel (unmix.cube), mais les quantités de correction sont calculées sur une image
    légèrement floutée : la couleur MOYENNE est la même qu'avant (aucun liseré, aucune différence de teinte avec les
    bords), mais le grain de la caméra n'est plus amplifié ni « haché » pixel par pixel.
    rgb : image HxWx3 uint8. Renvoie une image uint8."""
    H, W = rgb.shape[:2]
    hw, hh = max(2, W // 2), max(2, H // 2)
    sm = cv2.GaussianBlur(cv2.resize(rgb, (hw, hh), interpolation=cv2.INTER_AREA).astype(np.float32), (0, 0), 1.5)
    ex = np.maximum(sm[..., 1] - (sm[..., 0] + sm[..., 2]) * 0.5, 0.0)
    a = np.clip(1.0 - ex / np.float32(EK), 0.4, 1.0)
    wb = smoothstep(sm.mean(axis=2), THR - 8.0, THR + 8.0)            # seuil de luminosité adouci (plus de bascule)
    a = (1.0 - (1.0 - a) * wb).astype(np.float32)
    inv = cv2.resize(1.0 / a, (W, H), interpolation=cv2.INTER_LINEAR)             # 1/a
    off = cv2.resize((1.0 - a) / a, (W, H), interpolation=cv2.INTER_LINEAR)        # (1-a)/a
    inv3, off1 = cv2.merge([inv, inv, inv]), off
    c = cv2.multiply(rgb.astype(np.float32), inv3)
    c -= cv2.merge([off1 * np.float32(K[0]), off1 * np.float32(K[1]), off1 * np.float32(K[2])])
    # plafonds du vert (et du cyan) : corrections calculées sur l'image lissée, appliquées en douceur
    cs = cv2.GaussianBlur(cv2.resize(c, (hw, hh), interpolation=cv2.INTER_AREA), (0, 0), 1.5)
    dg = np.maximum(cs[..., 1] - (cs[..., 0] + cs[..., 2]) * 0.5, 0.0)
    db = np.maximum(cs[..., 2] - cs[..., 0], 0.0)
    z = np.zeros((H, W), np.float32)
    c -= cv2.merge([z, cv2.resize(dg, (W, H), interpolation=cv2.INTER_LINEAR),
                    cv2.resize(db, (W, H), interpolation=cv2.INTER_LINEAR)])
    return cv2.convertScaleAbs(np.maximum(c, 0.0))                     # arrondi + bornage 0..255 -> uint8


def edge_weight(alpha, W, H):
    """1 = bord de la découpe (contour, cheveux, zones semi-transparentes) -> retrait du vert habituel, plein ;
    0 = intérieur de la personne -> retrait lissé. Transition progressive sur ~1,2 % de la taille de l'image."""
    band = max(6.0, 0.012 * max(W, H))
    inner = (alpha >= 250).astype(np.uint8)
    dist = cv2.distanceTransform(inner, cv2.DIST_L2, 3)
    return 1.0 - smoothstep(dist, 0.5 * band, band)


def apply_unmix_split(tab, rgba, K, EK, THR, W, H):
    """Bords : EXACTEMENT la correction habituelle, pixel par pixel. Intérieur : même correction, lissée.
    Seule la zone où tu te trouves est calculée (le reste est transparent)."""
    full = apply_unmix(tab, rgba)
    alpha = rgba[..., 3]
    rows = np.flatnonzero((alpha >= 250).any(axis=1))
    if rows.size == 0:
        return full
    cols = np.flatnonzero((alpha[rows[0]:rows[-1] + 1] >= 250).any(axis=0))
    m = 8
    y0, y1 = max(0, rows[0] - m), min(H, rows[-1] + 1 + m)
    x0, x1 = max(0, cols[0] - m), min(W, cols[-1] + 1 + m)
    e = edge_weight(alpha[y0:y1, x0:x1], W, H).astype(np.float32)
    if not (e < 0.998).any():
        return full
    smooth = unmix_smooth(np.ascontiguousarray(rgba[y0:y1, x0:x1, :3]), K, EK, THR)
    fz = np.ascontiguousarray(full[y0:y1, x0:x1, :3])
    full[y0:y1, x0:x1, :3] = cv2.blendLinear(fz, smooth, e, (1.0 - e).astype(np.float32))
    return full


def apply_unmix(tab, rgba):
    """rgba : image HxWx4 uint8 contiguë. Renvoie une nouvelle image RGBA (alpha conservé)."""
    v = rgba.view(np.uint32)[..., 0]
    out = tab[v & 0x00FFFFFF] | (v & 0xFF000000)
    return out.view(np.uint8).reshape(rgba.shape)


# ---------------------------------------------------------------- lissage temporel (filtre « One Euro »)
class OneEuro:
    def __init__(self, fps, mincutoff=1.2, beta=0.15, dcutoff=1.0):
        self.fps, self.mc, self.beta, self.dc = float(fps), mincutoff, beta, dcutoff
        self.x = None
        self.dx = None

    def _alpha(self, cutoff):
        te = 1.0 / self.fps
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / te)

    def reset(self):
        self.x = None

    def __call__(self, x):
        if self.x is None:
            self.x, self.dx = x.copy(), np.zeros_like(x)
            return x
        dx = (x - self.x) * self.fps
        ad = self._alpha(self.dc)
        self.dx = ad * dx + (1 - ad) * self.dx
        cutoff = self.mc + self.beta * np.abs(self.dx)
        te = 1.0 / self.fps
        tau = 1.0 / (2 * np.pi * cutoff)
        a = 1.0 / (1.0 + tau / te)
        self.x = a * x + (1 - a) * self.x
        return self.x


# ---------------------------------------------------------------- géométrie du visage (indices MediaPipe, 478 points)
RIGHT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]   # œil droit du sujet
LEFT_EYE = [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466]
RIGHT_IRIS = [468, 469, 470, 471, 472]
LEFT_IRIS = [473, 474, 475, 476, 477]
RIGHT_EAR = ((33, 133), [(160, 144), (159, 145), (158, 153)])
LEFT_EAR = ((362, 263), [(387, 373), (386, 374), (385, 380)])
LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
RIGHT_BROW = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
LEFT_BROW = [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]
EYE_SET = sorted(set(RIGHT_EYE + LEFT_EYE + RIGHT_IRIS + LEFT_IRIS))


def eye_open_ratio(lm, spec):
    (a, b), pairs = spec
    w = float(np.linalg.norm(lm[a] - lm[b]))
    if w < 1e-3:
        return 0.0, 0.0
    return float(np.mean([np.linalg.norm(lm[u] - lm[v]) for u, v in pairs]) / w), w


# ---------------------------------------------------------------- couleurs des yeux (Lab : clarté relative au blanc de l'oeil, a, b)
EYE_COLORS = {
    "bleu_clair":   (0.72, -4.0, -13.0),
    "bleu_intense": (0.60, -2.0, -26.0),
    "gris_bleu":    (0.70, -2.0, -8.0),
    "vert":         (0.62, -14.0, 9.0),
}


EYE_SMOOTH = 0.25    # part de la nouvelle mesure prise à chaque image (lissage ~0,15 s à 30 i/s)
EYE_FADE = 0.6      # extinction plus rapide (clignement, mouvement brusque)


# ---------------------------------------------------------------- traitement d'une image
class Beauty:
    def __init__(self, W, H, fps, skin, eyes, eye_color, clean_edges, models):
        self.W, self.H, self.fps = W, H, fps
        self.skin_s = skin            # 0..1.5
        self.eye_s = eyes             # 0..1
        self.eye_col = EYE_COLORS.get(eye_color, EYE_COLORS["gris_bleu"])
        self.clean = clean_edges
        k = 640.0 / max(W, H)
        self.sw, self.sh = int(round(W * k / 2) * 2), int(round(H * k / 2) * 2)
        self.seg = self.lmk = None
        self.skin_ab = None           # moyenne / écart de la couleur de peau (a, b)
        self.lm_prev, self.lm_hold = None, 0
        self.euro = OneEuro(fps, mincutoff=1.0, beta=0.08)
        self.iris_ratio = []          # rayon de l'iris / largeur de l'oeil (stable pour une personne)
        self.ear_hist = []            # ouverture habituelle de TES yeux (référence pour clignements / regard baissé)
        self.prev_c = [None, None]    # position précédente des iris (flou de mouvement)
        # lissage dans le temps (anti-scintillement) : force de l'effet et luminosités de référence, par oeil
        self.eye_w = [None, None]     # None = pas encore vu : la 1re image prend directement la bonne force
                                      # (pas de fondu au début de chaque morceau ni aux raccords)
        self.eye_ref = [None, None]   # luminosité du blanc de l'oeil, lissée
        self.person_hist = []
        self.cloth = None
        self.smooth_key = None        # (K, EK, THR) du retrait lissé à l'intérieur (None = retrait plein partout, comme avant)
        self.last_conf = None
        self.stats = {"frames": 0, "faces": 0, "seg": 0}
        self._init_models(models)

    def _init_models(self, models):
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mpp
            from mediapipe.tasks.python import vision
            self.mp = mp
        except Exception as e:
            log("::warning::MediaPipe indisponible (%s) : la vidéo sera produite sans retouche." % str(e)[:200])
            return
        segp = os.path.join(models, "selfie_multiclass_256x256.tflite")
        if (self.skin_s > 0 or self.clean) and os.path.exists(segp):
            try:
                o = vision.ImageSegmenterOptions(base_options=mpp.BaseOptions(model_asset_path=segp),
                                                 running_mode=vision.RunningMode.VIDEO,
                                                 output_confidence_masks=True, output_category_mask=False)
                self.seg = vision.ImageSegmenter.create_from_options(o)
            except Exception as e:
                log("::warning::Segmentation (peau) indisponible : %s" % str(e)[:200])
        lmp = os.path.join(models, "face_landmarker.task")
        if (self.eye_s > 0 or self.skin_s > 0) and os.path.exists(lmp):
            try:
                o = vision.FaceLandmarkerOptions(base_options=mpp.BaseOptions(model_asset_path=lmp),
                                                 running_mode=vision.RunningMode.VIDEO, num_faces=2,
                                                 min_face_detection_confidence=0.4, min_face_presence_confidence=0.4,
                                                 min_tracking_confidence=0.4)
                self.lmk = vision.FaceLandmarker.create_from_options(o)
            except Exception as e:
                log("::warning::Détection du visage indisponible : %s" % str(e)[:200])

    # ------------------------------------------------ détections
    def detect(self, rgba, small, ts, n):
        conf = self.last_conf if (n % 2 == 1) else None                 # segmentation une image sur deux (le modèle est lent)
        if self.seg is not None and conf is None:
            try:
                r = self.seg.segment_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=small), ts)
                cm = r.confidence_masks
                if cm and len(cm) >= 5:
                    conf = [np.squeeze(m.numpy_view()).astype(np.float32) for m in cm[:5]]
                    self.stats["seg"] += 1
                    self.last_conf = conf
            except Exception:
                conf = None
        lm = None
        if self.lmk is not None:
            try:
                r = self.lmk.detect_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGBA, data=rgba), ts)
                best, area = None, -1.0
                for face in (r.face_landmarks or []):
                    if len(face) < 478:
                        continue
                    pts = np.array([[p.x * self.W, p.y * self.H] for p in face], np.float32)
                    ar = float(np.ptp(pts[:, 0]) * np.ptp(pts[:, 1]))
                    if ar > area:
                        best, area = pts, ar
                lm = best
            except Exception:
                lm = None
        return conf, lm

    # ------------------------------------------------ peau
    def update_skin_model(self, lab_s, face_skin):
        sel = face_skin > 0.85
        if sel.sum() < 200:
            return
        a, b = lab_s[..., 1][sel], lab_s[..., 2][sel]
        chroma = np.hypot(a, b)
        keep = chroma > np.percentile(chroma, 35)                     # écarte barbe / sourcils (peu colorés)
        a, b, L = a[keep], b[keep], lab_s[..., 0][sel][keep]
        if a.size < 100:
            return
        est = np.array([np.median(a), np.median(b),
                        max(1.4826 * np.median(np.abs(a - np.median(a))), 2.5),
                        max(1.4826 * np.median(np.abs(b - np.median(b))), 2.5),
                        np.median(L)], np.float32)
        self.skin_ab = est if self.skin_ab is None else 0.95 * self.skin_ab + 0.05 * est

    def skin_mask_small(self, conf, lab_s, lm):
        face_skin, body_skin = conf[3], conf[2]
        self.update_skin_model(lab_s, face_skin)
        m = np.clip(face_skin + body_skin, 0.0, 1.0)
        m = smoothstep(m, 0.25, 0.75)
        if self.skin_ab is not None:
            ma, mb, sa, sb, _ = self.skin_ab
            d = np.sqrt(((lab_s[..., 1] - ma) / sa) ** 2 + ((lab_s[..., 2] - mb) / sb) ** 2)
            m = m * (1.0 - smoothstep(d, 2.6, 4.6))
        m = m * smoothstep(lab_s[..., 0], 14.0, 30.0)                     # pas sur les zones très sombres
        if lm is not None:                                               # ni lèvres, ni yeux, ni sourcils
            k = self.sw / float(self.W)
            ex = np.zeros((self.sh, self.sw), np.uint8)
            for poly in (LIPS, RIGHT_EYE, LEFT_EYE, RIGHT_BROW, LEFT_BROW):
                cv2.fillPoly(ex, [np.round(lm[poly] * k).astype(np.int32)], 255, cv2.LINE_AA)
            ex = cv2.GaussianBlur(ex.astype(np.float32) / 255.0, (0, 0), 1.2)
            m = m * (1.0 - np.clip(ex * 1.4, 0, 1))
        return m.astype(np.float32)

    def whiten(self, out, lab_s, small_rgb, mask_s):
        """Peau : plus claire, moins jaune/rouge. Le changement de couleur est calculé en basse résolution
        puis ajouté à l'image : pores, rides, barbe naissante et netteté restent ceux d'origine."""
        if float(mask_s.max()) < 0.02:
            return
        L, A, B = lab_s[..., 0], lab_s[..., 1], lab_s[..., 2]
        s = self.skin_s
        base = cv2.GaussianBlur(L, (0, 0), 1.0)
        L2 = np.clip(L + (100.0 - base) * min(0.62 * s, 0.85), 0, 100)    # éclaircit le teint
        A2 = A * (1.0 - min(0.48 * s, 0.75))                            # moins rouge
        B2 = B * (1.0 - min(0.58 * s, 0.85))                            # moins jaune / hâlé
        new = cv2.cvtColor(cv2.merge([L2, A2, B2]), cv2.COLOR_Lab2RGB)
        delta = (new - small_rgb) * mask_s[..., None] * 255.0
        ys, xs = np.where(mask_s > 0.01)
        sx0, sx1 = max(0, xs.min() - 2), min(self.sw, xs.max() + 3)
        sy0, sy1 = max(0, ys.min() - 2), min(self.sh, ys.max() + 3)
        kx, ky = self.W / float(self.sw), self.H / float(self.sh)
        x0, x1 = int(round(sx0 * kx)), min(self.W, int(round(sx1 * kx)))
        y0, y1 = int(round(sy0 * ky)), min(self.H, int(round(sy1 * ky)))
        full = cv2.resize(np.ascontiguousarray(delta[sy0:sy1, sx0:sx1]), (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR)
        roi = out[y0:y1, x0:x1, :3]
        roi[...] = np.clip(roi.astype(np.float32) + full + 0.5, 0, 255).astype(np.uint8)

    # ------------------------------------------------ yeux
    def eyes(self, out, lm, weight):
        specs = ((RIGHT_EYE, RIGHT_IRIS, RIGHT_EAR), (LEFT_EYE, LEFT_IRIS, LEFT_EAR))
        info = []
        for eye_idx, iris_idx, spec in specs:
            ear, width = eye_open_ratio(lm, spec)
            r = float(np.mean(np.linalg.norm(lm[iris_idx[1:]] - lm[iris_idx[0]], axis=1)))
            info.append((ear, width, r))
        # rapport rayon d'iris / largeur d'oeil : constant pour une personne -> rayon stable, sans « gonflement »
        for ear, width, r in info:
            if width > 8 and len(self.ear_hist) < 3000:
                self.ear_hist.append(ear)
        ear_open = max(float(np.percentile(self.ear_hist, 75)), 0.16) if len(self.ear_hist) >= 20 else 0.26
        for ear, width, r in info:
            if ear > 0.8 * ear_open and width > 8 and 0.12 < r / width < 0.35 and len(self.iris_ratio) < 400:
                self.iris_ratio.append(r / width)
        ratio = float(np.median(self.iris_ratio)) if len(self.iris_ratio) >= 5 else None
        # de profil : l'oeil le plus éloigné est éteint progressivement
        wts = [1.0, 1.0]
        big = 0 if info[0][1] >= info[1][1] else 1
        far = 1 - big
        rel = info[far][1] / max(info[big][1], 1e-6)
        wts[far] = float(smoothstep(np.array(rel), 0.55, 0.75))
        for k, (eye_idx, iris_idx, spec) in enumerate(specs):
            ear, width, r = info[k]
            if ratio is not None:
                r = 0.35 * r + 0.65 * ratio * width
            fade = float(smoothstep(np.array(ear / ear_open), 0.50, 0.80))   # clignement, oeil plissé, regard baissé
            c = lm[iris_idx[0]]
            if self.prev_c[k] is not None and r > 0:                    # mouvement rapide (image floue) : on s'efface
                speed = float(np.linalg.norm(c - self.prev_c[k])) / r
                fade *= float(1.0 - smoothstep(np.array(speed), 0.5, 1.2))
            self.prev_c[k] = c.copy()
            w = weight * wts[k] * fade * self.eye_s if width > 6 else 0.0
            # la force suit la cible en douceur : apparition progressive (~0,15 s), disparition plus rapide
            # (clignement, mouvement brusque) -> plus d'allumage / extinction sec d'une image à l'autre
            if self.eye_w[k] is None:
                self.eye_w[k] = w
            else:
                rate = EYE_SMOOTH if w > self.eye_w[k] else EYE_FADE
                self.eye_w[k] += rate * (w - self.eye_w[k])
            if self.eye_w[k] > 0.01 and width > 6:
                self.one_eye(out, lm[eye_idx], lm[iris_idx[0]], r, self.eye_w[k], k)

    def one_eye(self, out, lid, c, r, strength, eye_k=None):
        H, W = self.H, self.W
        if not (np.isfinite(r) and 1.5 < r < 0.1 * max(W, H)):
            return
        x0 = int(max(0, math.floor(min(lid[:, 0].min(), c[0] - r) - 3)))
        x1 = int(min(W, math.ceil(max(lid[:, 0].max(), c[0] + r) + 3)))
        y0 = int(max(0, math.floor(min(lid[:, 1].min(), c[1] - r) - 3)))
        y1 = int(min(H, math.ceil(max(lid[:, 1].max(), c[1] + r) + 3)))
        if x1 - x0 < 6 or y1 - y0 < 4:
            return
        h, w = y1 - y0, x1 - x0
        SS = 4
        # ouverture de la paupière (légèrement rentrée : cils et ombre ne sont jamais colorés)
        big = np.zeros((h * SS, w * SS), np.uint8)
        cv2.fillPoly(big, [np.round((lid - [x0, y0]) * SS).astype(np.int32)], 255, cv2.LINE_AA)
        k = max(1, int(round(SS * 0.9)))
        big = cv2.erode(big, np.ones((k, k), np.uint8))
        lidm = cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        d = np.hypot(xx + 0.5 - c[0], yy + 0.5 - c[1]) / r
        roi = out[y0:y1, x0:x1, :3].astype(np.float32) / 255.0
        lab = cv2.cvtColor(roi, cv2.COLOR_RGB2Lab)
        L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]
        # blanc de l'oeil : référence de luminosité (sinon valeur par défaut)
        scl = (lidm > 0.6) & (d > 1.15)
        Lw = float(np.percentile(L[scl], 70)) if scl.sum() >= 6 else 0.0
        Lw = min(max(Lw, 55.0), 92.0) if Lw > 0 else 70.0
        disk = 1.0 - smoothstep(d, 0.86, 1.0)
        pupil = 1.0 - smoothstep(d, 0.30, 0.44)
        dark = 1.0 - smoothstep(L, Lw * 0.80, Lw * 0.95)                # l'iris est plus sombre que le blanc de l'oeil
        black = smoothstep(L, 6.0, 14.0)                                # pupille / cils très noirs : intacts
        m = disk * lidm * (1.0 - pupil) * dark * black
        if m.max() < 0.05:
            return
        m = cv2.GaussianBlur(m, (0, 0), max(0.45, r * 0.06)) * strength
        sel = m > 0.3 * m.max()
        Lb = float(np.average(L[sel], weights=m[sel])) if sel.any() else float(L.mean())
        Lw_s = Lw
        if eye_k is not None:                   # blanc de l'oeil lissé dans le temps : teinte stable, sans scintillement
            ref = self.eye_ref[eye_k]
            if ref is None or abs(Lw - ref) > 25.0:
                ref = Lw                        # premier passage ou vrai changement de lumière : pas de traînée
            else:
                ref = ref + EYE_SMOOTH * (Lw - ref)
            self.eye_ref[eye_k] = ref
            Lw_s = ref
        lt, at, bt = self.eye_col
        Lt = lt * Lw_s
        prof = Lt + 7.0 * (1.0 - smoothstep(d, 0.40, 0.70)) - 0.30 * Lt * smoothstep(d, 0.70, 1.0)   # clair au centre, anneau plus sombre
        L2 = np.clip((L - Lb) * 1.35 + prof, 4.0, Lw_s * 0.92)        # texture de l'iris d'origine conservée (accentuée)
        edge = 1.0 - 0.35 * smoothstep(d, 0.7, 1.0)
        A2 = A * 0.25 + at * edge
        B2 = B * 0.25 + bt * edge
        new = cv2.cvtColor(cv2.merge([L2.astype(np.float32), A2.astype(np.float32), B2.astype(np.float32)]), cv2.COLOR_Lab2RGB)
        res = roi + (new - roi) * m[..., None]
        out[y0:y1, x0:x1, :3] = np.clip(res * 255.0 + 0.5, 0, 255).astype(np.uint8)

    # ------------------------------------------------ nettoyage des bords (mur, plafond... visibles hors du tissu vert)
    def set_cloth(self, mask):
        """mask : zone du tissu vert (0..1). En dehors, seule la personne est gardée."""
        self.cloth = mask

    def person_mask(self, conf, small):
        p = 1.0 - conf[0]
        guide = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        try:
            p = cv2.ximgproc.guidedFilter(guide, p.astype(np.float32), 3, 2e-3)   # contour collé au vrai bord (bras sur le mur)
        except Exception:
            pass
        p = smoothstep(p, 0.25, 0.55).astype(np.float32)
        self.person_hist.append(p)
        if len(self.person_hist) > 2:
            self.person_hist.pop(0)
        p = np.maximum.reduce(self.person_hist)
        p = cv2.dilate(p, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        return cv2.GaussianBlur(p, (0, 0), 0.8)

    # ------------------------------------------------ image complète
    def process(self, rgba, n, unmix_tab):
        """rgba : image d'entrée (HxWx4, alpha = masque du chromakey). Renvoie l'image RGBA finale."""
        self.stats["frames"] += 1
        ts = int(round(n * 1000.0 / self.fps))
        small4 = cv2.resize(rgba, (self.sw, self.sh), interpolation=cv2.INTER_AREA)
        small = np.ascontiguousarray(small4[..., :3])
        conf, lm = self.detect(rgba, small, ts, n) if (self.seg or self.lmk) else (None, None)

        # suivi du visage : lissage, et maintien bref si une image n'est pas détectée
        lw = 1.0
        if lm is not None:
            self.stats["faces"] += 1
            if self.lm_prev is not None and np.median(np.linalg.norm(lm - self.lm_prev, axis=1)) > 0.08 * max(np.ptp(lm[:, 0]), 1):
                self.euro.reset()                                       # changement brusque (autre plan) : pas de traînée
            lm = self.euro(lm)
            self.lm_prev, self.lm_hold = lm.copy(), 0
        elif self.lm_prev is not None and self.lm_hold < 3:
            self.lm_hold += 1
            lm, lw = self.lm_prev, 1.0 - 0.3 * self.lm_hold
        else:
            self.euro.reset()

        out = None
        if self.smooth_key is not None:                                 # bords : correction habituelle ; intérieur : lissée
            try:
                out = apply_unmix_split(unmix_tab, rgba, *self.smooth_key, self.W, self.H)
            except Exception as e:                                      # jamais bloquant : retour au réglage habituel
                if not self.stats.get("split_err"):
                    log("::warning::Retrait lissé du vert en échec (%s) : retrait plein utilisé." % str(e)[:150])
                self.stats["split_err"] = self.stats.get("split_err", 0) + 1
        if out is None:
            out = apply_unmix(unmix_tab, rgba)                          # même correction du vert que les autres workflows
        if conf is not None and self.skin_s > 0:
            small_out = cv2.resize(out, (self.sw, self.sh), interpolation=cv2.INTER_AREA)[..., :3].astype(np.float32) / 255.0
            lab_s = cv2.cvtColor(small_out, cv2.COLOR_RGB2Lab)
            ms = self.skin_mask_small(conf, lab_s, lm)
            guide = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
            try:
                ms = cv2.ximgproc.guidedFilter(guide, ms, 4, 1e-3)       # bords de la peau collés aux vrais contours
            except Exception:
                ms = cv2.GaussianBlur(ms, (0, 0), 1.5)
            self.whiten(out, lab_s, small_out, np.clip(ms, 0, 1))
        if lm is not None and self.eye_s > 0:
            self.eyes(out, lm, lw)
        if conf is not None and self.clean and self.cloth is not None:
            p = cv2.resize(self.person_mask(conf, small), (self.W, self.H), interpolation=cv2.INTER_LINEAR)
            keep = np.maximum(self.cloth, np.clip(p, 0, 1))
            out[..., 3] = (out[..., 3].astype(np.float32) * keep + 0.5).astype(np.uint8)
        return out


# ---------------------------------------------------------------- zone du tissu vert (mesurée une fois par vidéo)
def analyze_cloth(src, W, H, TM, EK, n=16, seg_model=None):
    """Masque (0..1, taille W x H) : 1 = zone traitée normalement, 0 = « mur » à remplacer par le fond.
    Est considéré comme mur UNIQUEMENT ce qui est à la fois :
      - jamais vert sur les n images,
      - immobile (même couleur d'une image à l'autre),
      - jamais occupé par toi (silhouette détectée sur les n images, avec une large marge),
      - relié au bord de l'image.
    Tout le reste (toi, tes vêtements, le tissu) garde exactement le traitement habituel. None = rien à nettoyer."""
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src]).decode().strip())
    w, h = 384, int(round(384 * H / float(W)))
    frames = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", src, "-frames:v", "1",
                              "-vf", TM + "scale=%d:%d:flags=area,format=rgb24" % (w, h), "-f", "rawvideo", "-"],
                             capture_output=True).stdout
        if len(raw) == w * h * 3:
            frames.append(np.frombuffer(raw, np.uint8).reshape(h, w, 3))
    if len(frames) < 3:
        return None
    green = np.zeros((h, w), bool)
    for f in frames:
        f32 = f.astype(np.float32)
        green |= (f32[..., 1] - (f32[..., 0] + f32[..., 2]) / 2.0) > 0.35 * EK
    if green.mean() < 0.25:                                             # tissu non reconnu : pas de nettoyage
        return None
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32) for f in frames])
    still = gray.std(axis=0) < 9.0
    person = np.zeros((h, w), bool)
    if seg_model and os.path.exists(seg_model):
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mpp
            from mediapipe.tasks.python import vision
            seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
                base_options=mpp.BaseOptions(model_asset_path=seg_model), running_mode=vision.RunningMode.IMAGE,
                output_confidence_masks=True, output_category_mask=False))
            for f in frames:
                r = seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(f)))
                person |= (1.0 - np.squeeze(r.confidence_masks[0].numpy_view())) > 0.15
        except Exception as e:
            log("::warning::Silhouette non détectée pour la mesure des bords (%s) : pas de nettoyage." % str(e)[:120])
            return None
    else:
        return None
    m = int(0.06 * w) * 2 + 1                                           # large marge autour de toi
    person = cv2.dilate(person.astype(np.uint8), np.ones((m, m), np.uint8)) > 0
    greenish = cv2.dilate(green.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    wall = (~greenish) & still & (~person)
    wall = cv2.morphologyEx(wall.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(wall, 8)
    keep_wall = np.zeros((h, w), np.uint8)
    for i in range(1, nlab):
        x, y, bw, bh, area = stats[i]
        touches = x == 0 or y == 0 or x + bw >= w or y + bh >= h
        if touches and area > 0.002 * w * h:
            keep_wall[lab == i] = 255
    if keep_wall.max() == 0:
        return None
    # Lisière tissu / mur : la bande de transition (mélange clair-vert, pli, ombre du bord du tissu) n'est ni assez
    # verte pour le chromakey, ni classée « mur » → liseré clair visible. On étend le mur de quelques pixels VERS le
    # tissu (qui y serait de toute façon transparent) : jamais sur toi (large marge), seulement près du tissu.
    b = max(5, int(round(0.035 * w))) | 1
    near_cloth = cv2.dilate(green.astype(np.uint8), np.ones((b, b), np.uint8)) > 0
    grow = cv2.dilate(keep_wall, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (b, b))) > 0
    keep_wall[grow & near_cloth & (~person)] = 255
    zone = 255 - keep_wall
    zone = cv2.resize(zone, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
    return cv2.GaussianBlur(zone, (0, 0), 2.0)


# ---------------------------------------------------------------- programme principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--ss", default="0")
    ap.add_argument("--len", default="")
    ap.add_argument("--w", type=int, required=True)
    ap.add_argument("--h", type=int, required=True)
    ap.add_argument("--fps", required=True)
    ap.add_argument("--hdr", default="false")
    ap.add_argument("--bg", required=True)
    ap.add_argument("--key", required=True, help="R,G,B,EK,SIM,THR,HEX")
    ap.add_argument("--skin", default="60")
    ap.add_argument("--eyes", default="30")
    ap.add_argument("--eye-color", default="gris_bleu")
    ap.add_argument("--clean-edges", default="true")
    ap.add_argument("--vert-interieur", default="doux", help="doux = retrait habituel sur les bords, lissé à l'intérieur ; "
                    "plein = partout (ancien comportement)")
    ap.add_argument("--models", default=".")
    ap.add_argument("--out", required=True)
    ap.add_argument("--audio-out", default="")
    ap.add_argument("--preset", default="slow")
    ap.add_argument("--debug-frames", default="", help="dossier : enregistre quelques images avant/après")
    a = ap.parse_args()

    def num(txt, default):
        try:
            return float(str(txt).strip().replace(",", ".") or default)
        except ValueError:
            log("::warning::Valeur '%s' non comprise : %s utilisé." % (txt, default))
            return float(default)
    a.skin, a.eyes = num(a.skin, 60), num(a.eyes, 30)
    R, G, B, EK, SIM, THR, HEX = a.key.split(",")
    K = (float(R), float(G), float(B))
    W, H = a.w, a.h
    f_num, f_den = (a.fps.split("/") + ["1"])[:2]
    fps = float(f_num) / float(f_den)
    TM = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
          "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,") if a.hdr == "true" else ""

    cloth = None
    if a.clean_edges == "true":
        try:
            cloth = analyze_cloth(a.src, W, H, TM, float(EK), seg_model=os.path.join(a.models, "selfie_multiclass_256x256.tflite"))
        except Exception as e:
            log("::warning::Zone du tissu non mesurée (%s) : pas de nettoyage des bords." % str(e)[:150])
        if cloth is not None and a.debug_frames:
            os.makedirs(a.debug_frames, exist_ok=True)
            cv2.imwrite(os.path.join(a.debug_frames, "zone_tissu.png"), (cloth * 255).astype(np.uint8))

    t0 = time.time()
    tab = build_unmix_table(K, float(EK), float(THR))
    log("Table de dé-mélange prête (%.1f s)" % (time.time() - t0))

    seek = ["-ss", a.ss] if a.ss not in ("", "0", "0.0") else []
    dur = ["-t", a.len] if a.len else []
    in_args, vf0 = chunk_input(a.ss, a.len, a.fps, fps)
    fc_in = ("[0:v]" + vf0 + "%sscale=%d:%d,setsar=1,fps=%s,split=2[a][c];"
             "[a]format=yuva420p,chromakey=0x%s:%s:0.06,alphaextract,erosion,erosion,erosion,boxblur=1:1[mat];"
             "[c]format=rgba[cl];[cl][mat]alphamerge,format=rgba[o]") % (TM, W, H, a.fps, HEX, SIM)
    cmd_in = ["ffmpeg", "-v", "error", *in_args, "-i", a.src, "-filter_complex", fc_in, "-map", "[o]",
              "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    fc_out = ("[1:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1[bg];"
              "[0:v]setsar=1[fg];[bg][fg]overlay=shortest=1:format=auto[out]") % (W, H, W, H)
    cmd_out = ["ffmpeg", "-v", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % (W, H), "-framerate", a.fps, "-i", "-",
               "-framerate", a.fps, "-loop", "1", "-i", a.bg,
               "-filter_complex", fc_out, "-map", "[out]",
               "-c:v", "libx264", "-preset", a.preset, "-crf", "17", "-pix_fmt", "yuv420p", "-r", a.fps, "-fps_mode", "cfr",
               a.out]
    if a.audio_out:
        has_audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                    "-of", "csv=p=0", a.src], capture_output=True, text=True).stdout.strip()
        if has_audio:
            subprocess.run(["ffmpeg", "-v", "error", "-y", *seek, *dur, "-i", a.src, "-map", "0:a:0", "-vn",
                            "-c:a", "pcm_s16le", *dur, a.audio_out], check=True)

    bt = Beauty(W, H, fps, max(0.0, min(a.skin, 150.0)) / 100.0, max(0.0, min(a.eyes, 100.0)) / 100.0,
                a.eye_color, a.clean_edges == "true", a.models)
    bt.set_cloth(cloth)
    if str(a.vert_interieur).strip().lower() != "plein":
        bt.smooth_key = (K, float(EK), float(THR))
        log("Retrait du vert : habituel sur les bords, lissé à l'intérieur (veste, chemise, barbe : même couleur, sans grain)")
    else:
        log("Retrait du vert : plein partout (ancien réglage)")
    log("Nettoyage des bords hors du tissu vert : %s" % ("oui" if bt.cloth is not None else "non (tissu sur toute l'image)"))
    pin = subprocess.Popen(cmd_in, stdout=subprocess.PIPE)
    pout = subprocess.Popen(cmd_out, stdin=subprocess.PIPE)
    fsize = W * H * 4
    n = 0
    n_max = int(math.floor(float(a.len) * fps + 0.5)) if a.len else None    # nombre exact d'images du morceau
    dbg = a.debug_frames
    if dbg:
        os.makedirs(dbg, exist_ok=True)
    t1 = time.time()
    try:
        while True:
            if n_max is not None and n >= n_max:
                break
            buf = pin.stdout.read(fsize)
            if len(buf) < fsize:
                break
            fr = np.frombuffer(buf, np.uint8).reshape(H, W, 4)
            try:
                res = bt.process(fr, n, tab)
            except Exception as e:                                      # une image difficile ne bloque jamais la vidéo
                if n < 3 or n % 500 == 0:
                    log("::warning::Image %d non retouchée (%s)" % (n, str(e)[:150]))
                res = apply_unmix(tab, fr)
            pout.stdin.write(np.ascontiguousarray(res).tobytes())
            if dbg and n % max(1, int(round(fps * 2))) == 0:
                cv2.imwrite(os.path.join(dbg, "f%05d_avant.jpg" % n), cv2.cvtColor(fr[..., :3], cv2.COLOR_RGB2BGR))
                cv2.imwrite(os.path.join(dbg, "f%05d_apres.jpg" % n), cv2.cvtColor(res[..., :3], cv2.COLOR_RGB2BGR))
            n += 1
            if n % 300 == 0:
                el = time.time() - t1
                log("  %d images (%.1f i/s)" % (n, n / max(el, 1e-6)))
    finally:
        pout.stdin.close()
        rc_out = pout.wait()
        pin.stdout.close()
        rc_in = pin.wait()
    st = bt.stats
    log("Beauté : %d images en %.0f s | visage détecté sur %d (%.0f %%) | segmentation peau sur %d (%.0f %%)" % (
        n, time.time() - t1, st["faces"], 100.0 * st["faces"] / max(n, 1), st["seg"], 100.0 * st["seg"] / max((n + 1) // 2, 1)))
    if n > 20 and a.eyes > 0 and st["faces"] < 0.2 * n:
        log("::warning::Visage détecté sur moins de 20 %% des images : les yeux restent naturels sur le reste.")
    if rc_out != 0 or n == 0:
        log("::error::Encodage vidéo en échec (code %s, %d images)" % (rc_out, n))
        sys.stdout.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)                                                         # évite un message inoffensif de MediaPipe à la fermeture


if __name__ == "__main__":
    main()
