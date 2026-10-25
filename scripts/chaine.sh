#!/usr/bin/env bash
# CHAÎNE fond vert → Montage LV final → Shorts (Push-overlay). Utilisé seulement si « enchainer » est coché.
#
#   chaine.sh verifier          → au DÉBUT du run : tout est-il prêt ? (Google Drive, jeton, dossier montage-joints)
#                                 Échoue tout de suite plutôt qu'après 30 min de traitement.
#   chaine.sh envoyer <vidéo>   → à la FIN du run (le résultat est déjà publié comme d'habitude) :
#                                 copie la vidéo sur Google Drive dans chaine/<id>/moi-<nom>.mp4 (privé),
#                                 puis lance Montage LV final avec un « bon de livraison ».
#                                 <vidéo> = fichier local, ou chemin rclone (gdrive:resultats/x.mp4 : copie côté Drive)
#
# Variables : CHAINE_FINDER (oui|non), RCLONE_CONFIG_CONTENT, CHAINE_GH_TOKEN (jeton GitHub App ou CHAINE_TOKEN),
#             CHAINE_JOINTS (dossier Drive des photos/vidéos, défaut montage-joints), GITHUB_* (fournies par Actions)
# Ménage : fichiers de chaine/ de plus de 2 jours supprimés définitivement (pas de corbeille) à chaque envoi.
set -euo pipefail
ACTION="${1:-}"
CIBLE_REPO="${CHAINE_MONTAGE_REPO:-MaxMcNeil/Montage-LV-final}"
JOINTS="${CHAINE_JOINTS:-montage-joints}"
FINDER="$(printf '%s' "${CHAINE_FINDER:-oui}" | tr 'A-Z' 'a-z')"
case "$FINDER" in non|no|false|0) FINDER=non;; *) FINDER=oui;; esac
SUM="${GITHUB_STEP_SUMMARY:-/dev/stdout}"
err() { echo "::error title=Chaîne::$*"; { echo "### ❌ Chaîne arrêtée"; echo "$*"; } >> "$SUM"; exit 1; }

rclone_pret() {
  if [ ! -s ~/.config/rclone/rclone.conf ]; then
    [ -n "${RCLONE_CONFIG_CONTENT:-}" ] || err "Google Drive non configuré : le secret RCLONE_CONFIG est vide. La chaîne passe par Google Drive (vidéos privées)."
    mkdir -p ~/.config/rclone && printf '%s\n' "$RCLONE_CONFIG_CONTENT" > ~/.config/rclone/rclone.conf
  fi
  command -v rclone >/dev/null || (curl -fsSL https://rclone.org/install.sh | sudo bash >/dev/null) || sudo apt-get install -y -qq rclone
  # même choix que get_source.sh : le premier stockage de type Google Drive
  REMOTE="$(rclone listremotes --long 2>/dev/null | awk -F: '$2 ~ /drive/ {print $1; exit}')"
  [ -n "$REMOTE" ] || REMOTE="$(rclone listremotes 2>/dev/null | head -1 | tr -d ':')"
  [ -n "$REMOTE" ] || err "Aucun stockage dans RCLONE_CONFIG."
  rclone about "$REMOTE:" >/dev/null 2>&1 || err "Google Drive injoignable avec RCLONE_CONFIG (jeton expiré ? refais « rclone config reconnect $REMOTE: » et mets à jour le secret)."
}

if [ "$ACTION" = "verifier" ]; then
  rclone_pret
  [ "${CHAINE_JETON_CONFIGURE:-}" = "true" ] \
    || err "Jeton de chaîne absent : ajoute la variable CHAINE_APP_ID + le secret CHAINE_APP_KEY (GitHub App, recommandé), ou le secret CHAINE_TOKEN. Voir README, section Chaîne."
  if [ "$FINDER" = "non" ]; then
    N=$(rclone lsf "$REMOTE:$JOINTS" --files-only -R 2>/dev/null | grep -ciE '\.(jpe?g|png|webp|gif|bmp|heic|heif|avif|mp4|mov|mkv|webm|avi|m4v|3gp|mts)$' || true)
    [ "${N:-0}" -gt 0 ] || err "Synchro Finder = non, mais le dossier Google Drive « $JOINTS » est vide (ou absent). Dépose tes photos / vidéos dedans, puis relance. Rien n'a été traité."
    echo "Dossier « $JOINTS » : $N photo(s)/vidéo(s) prêtes pour le montage."
  fi
  { echo "### 🔗 Chaîne activée"; echo "Fond vert → Montage LV final (synchro Finder : **$FINDER**) → Shorts."; } >> "$SUM"
  exit 0
fi

[ "$ACTION" = "envoyer" ] || err "usage : chaine.sh verifier | envoyer <vidéo>"
SRC="${2:-}"
[ -n "$SRC" ] || err "aucune vidéo à envoyer"
[ -n "${CHAINE_GH_TOKEN:-}" ] || err "Jeton de chaîne indisponible au moment de l'envoi (GitHub App ou CHAINE_TOKEN). Le résultat du fond vert est publié normalement ; relance la chaîne avec « Re-run failed jobs » après correction."
rclone_pret

ID="$(date -u +%Y%m%d-%H%M)-v${GITHUB_RUN_ID:-0}"
BASE="$(basename "${SRC#*:}")"; BASE="${BASE%.*}"
BASE="$(python3 -c 'import re,sys,unicodedata as u;t=u.normalize("NFKD",sys.argv[1]).encode("ascii","ignore").decode();t=re.sub(r"[^A-Za-z0-9._-]+","_",t).strip("_");print(re.sub(r"^moi[-_]*","",t,flags=re.I)[:50])' "$BASE")"
NOM="moi-${BASE:-video}.mp4"   # « moi… » : Montage LV final reconnaît ainsi TA vidéo
DST="$REMOTE:chaine/$ID/$NOM"

echo "Ménage : fichiers de chaine/ de plus de 2 jours"
rclone delete "$REMOTE:chaine" --min-age 2d --drive-use-trash=false -q 2>/dev/null || true
rclone rmdirs "$REMOTE:chaine" --leave-root -q 2>/dev/null || true

echo "Envoi : $SRC → $DST"
if [[ "$SRC" == *:* ]] && [ ! -e "$SRC" ]; then
  rclone copyto "$SRC" "$DST" --retries 5 || err "copie Drive impossible : $SRC"
else
  [ -s "$SRC" ] || err "vidéo introuvable : $SRC"
  rclone copyto "$SRC" "$DST" --retries 5 --drive-chunk-size 64M || err "envoi Drive impossible : $SRC"
  # contrôle : même taille des deux côtés
  L=$(stat -c %s "$SRC")
  R=$(rclone size "$DST" --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["bytes"])')
  [ "$L" = "$R" ] || err "taille différente après envoi ($L octets ici, $R sur Drive)"
fi
FID="$(rclone lsjson "$DST" --no-mimetype 2>/dev/null | python3 -c 'import sys,json;a=json.load(sys.stdin);print(a[0].get("ID","") if a else "")')"
[ -n "$FID" ] || err "identifiant Google Drive introuvable pour $DST"

ORIGINE="https://github.com/${GITHUB_REPOSITORY:-?}/actions/runs/${GITHUB_RUN_ID:-0}"
python3 - "$ID" "$FID" "$NOM" "$FINDER" "$JOINTS" "$ORIGINE" > bon-montage.json <<'PY'
import json, sys
i, fid, nom, finder, joints, origine = sys.argv[1:7]
print(json.dumps({"event_type": "chaine-montage", "client_payload": {
    "v": 1, "chaine_id": i, "video_id": fid, "video_nom": nom, "finder": finder,
    "joints": joints, "origine": origine}}, ensure_ascii=False))
PY
rclone copyto bon-montage.json "$REMOTE:chaine/$ID/bon-montage.json" -q || true   # trace (diagnostic)

OK=0
for i in 1 2 3 4; do
  if GH_TOKEN="$CHAINE_GH_TOKEN" gh api "repos/$CIBLE_REPO/dispatches" --input bon-montage.json >/dev/null 2>dispatch.err; then OK=1; break; fi
  echo "::warning::lancement de Montage LV final : essai $i échoué ($(tail -1 dispatch.err))"; sleep $((i * 10))
done
[ "$OK" = 1 ] || err "Montage LV final n'a pas pu être lancé : $(tail -2 dispatch.err | tr '\n' ' '). Le jeton a-t-il l'accès « Contents : read and write » sur montage-lv-final ? Le fond vert est publié ; corrige puis « Re-run failed jobs »."

{ echo "### 🔗 Chaîne lancée — \`$ID\`"
  echo "- Vidéo transmise (privée, Google Drive) : \`chaine/$ID/$NOM\`"
  echo "- Synchro Finder : **$FINDER**$([ "$FINDER" = non ] && echo " — photos/vidéos du dossier Drive « $JOINTS »")"
  echo "- Suite : [Montage LV final → Actions](https://github.com/$CIBLE_REPO/actions) puis Shorts dans Push-overlay"
  echo "- Résultats finaux : releases \`out-chaine-$ID\` (gardées 2 jours)"; } >> "$SUM"
echo "::notice title=Chaîne $ID::Montage LV final lancé (synchro Finder : $FINDER)"
