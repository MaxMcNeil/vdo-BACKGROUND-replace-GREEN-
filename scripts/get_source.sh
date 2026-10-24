#!/usr/bin/env bash
# Accès à la vidéo source pour le workflow Beauté.
#   get_source.sh resolve        -> choisit la source (lien, rclone ou Release) et la rend lisible ; écrit
#                                   SRC, SRC_MODE, OUT_BASENAME (et RELEASE_TAG / VIDEO_ASSET résolus) dans $GITHUB_ENV
#   get_source.sh open <mode>    -> dans un job de morceau : rend la même source lisible (SRC dans $GITHUB_ENV)
# Variables : SOURCE, RELEASE_TAG, VIDEO_ASSET, REPO, GH_TOKEN
set -euo pipefail
ACTION="${1:-resolve}"
MODE_IN="${2:-}"
mkdir -p input

is_video() { case "${1,,}" in *.mp4|*.mov|*.m4v|*.mkv|*.webm|*.avi|*.mts|*.3gp) return 0;; *) return 1;; esac; }

resolve_rclone_path() {   # corrige le nom du stockage (ex: « Grove: » tapé au lieu de « gdrive: ») et retrouve le fichier
  python3 - "$1" <<'PY'
import os, re, subprocess, sys, unicodedata
src = sys.argv[1].strip()
def run(*a):
    r = subprocess.run(["rclone", *a], capture_output=True, text=True)
    return r.returncode, r.stdout
def norm(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", t)
_, rem = run("listremotes", "--long")
remotes = [(l.split(":")[0], l.split(":", 1)[1].strip()) for l in rem.splitlines() if ":" in l]
names = [n for n, _ in remotes]
remote, path = (src.split(":", 1) + [""])[:2] if ":" in src else ("", src)
path = path.strip().strip("/")
if remote not in names:
    cand = [n for n in names if n.lower() == remote.lower()] or [n for n, t in remotes if t == "drive"] or names
    if not cand:
        sys.exit("aucun stockage rclone configuré")
    print("::notice::Stockage « %s: » inconnu, « %s: » utilisé à la place (stockages configurés : %s)"
          % (remote, cand[0], ", ".join(n + ":" for n in names)), file=sys.stderr)
    remote = cand[0]
full = "%s:%s" % (remote, path)
rc, out = run("lsf", full)
if rc == 0 and out.strip():
    print(full); sys.exit(0)
# fichier pas trouvé tel quel : recherche tolérante (majuscules, accents, espaces, extension)
d = os.path.dirname(path)
want = norm(os.path.splitext(os.path.basename(path))[0]) or norm(os.path.basename(path))
for depth in (["--max-depth", "1"], ["--max-depth", "4"]):
    rc, out = run("lsf", "%s:%s" % (remote, d), "--files-only", "-R", *depth)
    vids = [l for l in out.splitlines() if re.search(r"\.(mp4|mov|m4v|mkv|webm|avi|mts|3gp)$", l, re.I)]
    hit = [v for v in vids if norm(os.path.splitext(os.path.basename(v))[0]) == want]
    if not hit:
        hit = [v for v in vids if want and want in norm(os.path.basename(v))]
    if len(hit) >= 1:
        hit.sort(key=len)
        found = "%s:%s" % (remote, "/".join(x for x in (d, hit[0]) if x))
        print("::notice::Fichier trouvé : %s" % found, file=sys.stderr)
        print(found); sys.exit(0)
print(full)
PY
}

serve_rclone() {   # $1 = remote:chemin/fichier.ext
  local SOURCE="$1" REMOTE PATHPART DIR FILE ENC CODE
  SOURCE="$(resolve_rclone_path "$SOURCE")" || { echo "::error::rclone : $SOURCE"; exit 1; }
  RESOLVED_SOURCE="$SOURCE"
  REMOTE="${SOURCE%%:*}:"; PATHPART="${SOURCE#*:}"
  if [[ "$PATHPART" == */* ]]; then DIR="$REMOTE${PATHPART%/*}"; FILE="${PATHPART##*/}"; else DIR="$REMOTE"; FILE="$PATHPART"; fi
  if ! rclone lsf "$SOURCE" > source-check.txt 2> source-check.err || [ ! -s source-check.txt ]; then
    echo "::error::Fichier introuvable via rclone : $SOURCE"
    echo "--- Message rclone :"; tail -5 source-check.err || true
    echo "--- Ce que rclone voit dans $DIR :"; rclone lsf "$DIR" --max-depth 1 2>&1 | head -40 || true
    echo "Vérifie : le chemin exact (dossiers compris), les majuscules, l'extension, et que le fichier n'est pas seulement 'Partagé avec moi'."
    exit 1
  fi
  setsid nohup rclone serve http "$DIR" --addr 127.0.0.1:8081 --read-only > rclone-serve.log 2>&1 &
  ENC="$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$FILE")"
  SRC="http://127.0.0.1:8081/$ENC"
  CODE=000
  for i in $(seq 1 30); do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -r 0-0 "$SRC" || true)
    case "$CODE" in 200|206) break;; esac
    sleep 1
  done
  case "$CODE" in 200|206) ;; *) echo "::error::Serveur rclone : réponse HTTP $CODE pour $SRC"; cat rclone-serve.log; exit 1;; esac
  BASENAME="${FILE%.*}"
}

download_url() {   # $1 = lien https
  local URL="$1"
  case "$URL" in
    *drive.google.com*|*docs.google.com*)
      ID="$(printf '%s' "$URL" | sed -nE 's#.*(/d/|[?&]id=)([A-Za-z0-9_-]{10,}).*#\2#p')"
      OK=0
      # 1) avec ton compte (rclone) : fonctionne même si le fichier n'est pas partagé publiquement
      DRV="$(rclone listremotes --long 2>/dev/null | awk -F: '$2 ~ /drive/ {print $1; exit}' || true)"
      if [ -n "$ID" ] && [ -n "$DRV" ]; then
        mkdir -p input/dl
        if rclone backend copyid "$DRV:" "$ID" input/dl/ --retries 3 2>rclone-dl.err; then
          F="$(ls input/dl | head -1)"
          if [ -n "$F" ]; then mv "input/dl/$F" input/video.mp4; DRIVE_NAME="$F"; OK=1; echo "Téléchargé via rclone ($DRV:) : $F"; fi
        else
          echo "::warning::rclone n'a pas pu récupérer ce lien Drive :"; tail -3 rclone-dl.err || true
        fi
      fi
      # 2) sinon : téléchargement public (fichier partagé « Tous les utilisateurs disposant du lien »)
      if [ "$OK" != 1 ]; then
        python3 -m venv /tmp/gdown-venv && /tmp/gdown-venv/bin/pip install --quiet gdown
        if ! /tmp/gdown-venv/bin/gdown --fuzzy "$URL" -O input/video.mp4; then
          echo "::error::Téléchargement Google Drive impossible : configure le secret RCLONE_CONFIG (accès avec ton compte) ou partage le fichier en « Tous les utilisateurs disposant du lien »."
          exit 1
        fi
      fi;;
    *dropbox.com*)
      URL="${URL/dl=0/dl=1}"; case "$URL" in *dl=1*) ;; *\?*) URL="$URL&dl=1";; *) URL="$URL?dl=1";; esac
      curl -fL --retry 3 -o input/video.mp4 "$URL";;
    *)
      curl -fL --retry 3 -o input/video.mp4 "$URL";;
  esac
  test -s input/video.mp4 || { echo "::error::Vidéo vide ou introuvable : $URL"; exit 1; }
  if ! ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 input/video.mp4 | grep -q '[0-9]'; then
    echo "::error::Le lien ne donne pas une vidéo (page web ? fichier privé ?) : $URL"; head -c 300 input/video.mp4 | tr -cd '[:print:]\n' | head -5; exit 1
  fi
  SRC="input/video.mp4"
  BASENAME="$(DRIVE_NAME="${DRIVE_NAME:-}" python3 - "$URL" <<'PY'
import sys, urllib.parse, os, subprocess
u = sys.argv[1]
name = os.path.basename(urllib.parse.urlparse(u).path)
if os.environ.get("DRIVE_NAME"):
    name = os.environ["DRIVE_NAME"]
elif "drive.google.com" in u or "docs.google.com" in u or not name or "." not in name:
    name = "video"
print(os.path.splitext(urllib.parse.unquote(name))[0] or "video")
PY
)"
}

resolve_release() {
  local TAG="${RELEASE_TAG:-}" ASSET="${VIDEO_ASSET:-}"
  if [ -z "$TAG" ]; then
    # Rien de précisé : dernière Release (hors résultats) qui contient une vidéo
    TAG="$(gh api "repos/$REPO/releases?per_page=50" --jq '.[] | select((.tag_name|startswith("result-")|not) and (.tag_name|startswith("tmp")|not)) | select([.assets[].name | ascii_downcase | test("\\.(mp4|mov|m4v|mkv|webm|avi|mts|3gp)$")] | any) | .tag_name' | head -1)"
    [ -n "$TAG" ] || { echo "::error::Aucune vidéo trouvée : renseigne 'source' (lien ou rclone) ou 'release_tag'."; exit 1; }
    echo "::notice::Release utilisée automatiquement : $TAG"
  fi
  if [ -z "$ASSET" ]; then
    # La vidéo la plus récente de la Release
    ASSET="$(gh api "repos/$REPO/releases/tags/$TAG" --jq '[.assets[] | select(.name | ascii_downcase | test("\\.(mp4|mov|m4v|mkv|webm|avi|mts|3gp)$"))] | sort_by(.created_at) | last | .name // empty')"
    [ -n "$ASSET" ] || { echo "::error::Aucune vidéo dans la Release $TAG"; exit 1; }
    echo "::notice::Vidéo utilisée : $ASSET (Release $TAG)"
  fi
  RELEASE_TAG="$TAG"; VIDEO_ASSET="$ASSET"
}

download_release() {
  gh release download "$RELEASE_TAG" --repo "$REPO" --pattern "$VIDEO_ASSET" --output "input/video.mp4" --clobber \
    || { echo "::error::Vidéo introuvable : Release '$RELEASE_TAG', fichier '$VIDEO_ASSET' (vérifie le tag et le nom exact)"; exit 1; }
  SRC="input/video.mp4"
  BASENAME="${VIDEO_ASSET%.*}"
}

SOURCE="$(printf '%s' "${SOURCE:-}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
if [ "$ACTION" = "resolve" ]; then
  if [[ "$SOURCE" =~ ^https?:// ]]; then
    MODE=url; download_url "$SOURCE"
  elif [ -n "$SOURCE" ]; then
    case "$SOURCE" in *:*) ;; *) echo "::error::'source' : un lien (https://...) ou un chemin rclone de la forme stockage:dossier/fichier.mp4 (ex: gdrive:videos/ma_video.mp4)"; exit 1;; esac
    [ -s ~/.config/rclone/rclone.conf ] || { echo "::error::Chemin rclone donné, mais le secret RCLONE_CONFIG n'est pas configuré (voir README)."; exit 1; }
    MODE=rclone; serve_rclone "$SOURCE"
  else
    MODE=release; resolve_release; download_release
    { echo "RELEASE_TAG=$RELEASE_TAG"; echo "VIDEO_ASSET=$VIDEO_ASSET"; } >> "$GITHUB_ENV"
  fi
  { echo "SRC=$SRC"; echo "SRC_MODE=$MODE"; echo "OUT_BASENAME=$BASENAME"; } >> "$GITHUB_ENV"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    { echo "mode=$MODE"; echo "release_tag=${RELEASE_TAG:-}"; echo "video_asset=${VIDEO_ASSET:-}"; echo "source=${RESOLVED_SOURCE:-$SOURCE}"; } >> "$GITHUB_OUTPUT"
  fi
  echo "Source : $MODE ($SRC)"
else
  case "$MODE_IN" in
    rclone)  serve_rclone "$SOURCE";;
    release) download_release;;
    url)     test -s input/video.mp4 || { echo "::error::Vidéo source (lien) absente"; exit 1; }; SRC="input/video.mp4";;
    *) echo "::error::mode de source inconnu : $MODE_IN"; exit 1;;
  esac
  echo "SRC=$SRC" >> "$GITHUB_ENV"
fi
