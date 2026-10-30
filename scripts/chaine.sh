#!/usr/bin/env bash
# CHAÎNE fond vert → Montage LV final → Shorts (Push-overlay). Utilisé seulement si « enchainer » est coché.
#
#   chaine.sh verifier                 → au DÉBUT du run : Google Drive, jeton, dossier montage-joints.
#                                        Échoue tout de suite plutôt qu'après une heure de traitement.
#   chaine.sh deposer <vidéo> [drive]  → met la vidéo à disposition du montage sur Google Drive (privé) :
#        - si le résultat est DÉJÀ sur ton Drive ([drive] = gdrive:resultats/x.mp4, même taille) : réutilisé tel quel,
#          AUCUNE copie (ton espace Drive n'est pas consommé deux fois) ;
#        - sinon : envoi dans chaine/<id>/ après vérification de l'espace libre ;
#        - <vidéo> peut aussi être directement un chemin Drive (workflows « vidéos longues » / secours).
#        Écrit chaine_id, video_id, video_nom dans GITHUB_OUTPUT.
#   chaine.sh lancer                   → lance Montage LV final (variables CHAINE_ID, CHAINE_VIDEO_ID, CHAINE_VIDEO_NOM).
#                                        Job séparé : en cas d'échec, « Re-run failed jobs » ne refait QUE ce lancement.
#   chaine.sh envoyer <vidéo> [drive]  → deposer + lancer (workflow principal, un seul job).
#
# Variables : CHAINE_VOIX_STUDIO (non|oui, défaut non), CHAINE_SHORTS_SOUS_TITRES (oui|non), RCLONE_CONFIG_CONTENT, CHAINE_GH_TOKEN (jeton GitHub App ou CHAINE_TOKEN),
#             CHAINE_JOINTS (dossier Drive des photos/vidéos, défaut montage-joints), GITHUB_* (fournies par Actions)
# Ménage : fichiers de chaine/ de plus de 2 jours supprimés définitivement (pas de corbeille) à chaque dépôt.
set -euo pipefail
ACTION="${1:-}"
CIBLE_REPO="${CHAINE_MONTAGE_REPO:-MaxMcNeil/Montage-LV-final}"
JOINTS="${CHAINE_JOINTS:-montage-joints}"
FINDER=non   # 🔒 recherche automatique désactivée : tu fournis toi-même articles, vidéos, photos (montage-joints)
VOIX="$(printf '%s' "${CHAINE_VOIX_STUDIO:-non}" | tr 'A-Z' 'a-z')"
SHORTS_ST="$(printf '%s' "${CHAINE_SHORTS_SOUS_TITRES:-oui}" | tr 'A-Z' 'a-z')"; [ "$SHORTS_ST" = non ] || SHORTS_ST=oui
case "$VOIX" in oui|yes|true|1) VOIX=oui;; *) VOIX=non;; esac   # tout autre valeur = non (défaut sûr)
SUM="${GITHUB_STEP_SUMMARY:-/dev/stdout}"
err() { echo "::error title=Chaîne::$*"; { echo "### ❌ Chaîne arrêtée"; echo "$*"; } >> "$SUM"; exit 1; }
out() { echo "$1=$2" >> "${GITHUB_OUTPUT:-/dev/null}"; }

rclone_pret() {
  if [ ! -s ~/.config/rclone/rclone.conf ]; then
    [ -n "${RCLONE_CONFIG_CONTENT:-}" ] || err "Google Drive non configuré : le secret RCLONE_CONFIG est vide. La chaîne passe par Google Drive (vidéos privées)."
    mkdir -p ~/.config/rclone && printf '%s\n' "$RCLONE_CONFIG_CONTENT" > ~/.config/rclone/rclone.conf
  fi
  local i
  for i in 1 2 3; do
    command -v rclone >/dev/null && break
    (curl -fsSL --retry 3 https://rclone.org/install.sh | sudo bash >/dev/null 2>&1) || sudo apt-get install -y -qq rclone >/dev/null 2>&1 || sleep 5
  done
  command -v rclone >/dev/null || err "rclone n'a pas pu être installé (réseau) : relance le job (« Re-run failed jobs »)."
  # même choix que get_source.sh : le premier stockage de type Google Drive
  REMOTE="$(rclone listremotes --long 2>/dev/null | awk -F: '$2 ~ /drive/ {print $1; exit}')"
  [ -n "$REMOTE" ] || REMOTE="$(rclone listremotes 2>/dev/null | head -1 | tr -d ':')"
  [ -n "$REMOTE" ] || err "Aucun stockage dans RCLONE_CONFIG."
  rclone about "$REMOTE:" >/dev/null 2>&1 || err "Google Drive injoignable avec RCLONE_CONFIG (jeton expiré ? refais « rclone config reconnect $REMOTE: » et mets à jour le secret)."
}

drive_libre() {   # octets libres sur le Drive (vide si inconnu)
  rclone about "$REMOTE:" --json 2>/dev/null | python3 -c 'import sys,json
try: print(int(json.load(sys.stdin).get("free") or 0) or "")
except Exception: print("")' 2>/dev/null || true
}
taille_drive() {  # taille d'un fichier Drive (vide si absent)
  rclone lsjson "$1" --no-mimetype 2>/dev/null | python3 -c 'import sys,json
try:
    a=json.load(sys.stdin); print(a[0]["Size"] if a else "")
except Exception: print("")' 2>/dev/null || true
}
id_drive() {
  rclone lsjson "$1" --no-mimetype 2>/dev/null | python3 -c 'import sys,json
try:
    a=json.load(sys.stdin); print(a[0].get("ID","") if a else "")
except Exception: print("")' 2>/dev/null || true
}

deposer() {
  local SRC="${1:-}" EXIST="${2:-}" BASE NOM DST L R LIBRE FID=""
  [ -n "$SRC" ] || err "aucune vidéo à transmettre"
  rclone_pret
  CHAINE_ID="$(date -u +%Y%m%d-%H%M)-v${GITHUB_RUN_ID:-0}"

  echo "Ménage : fichiers de chaine/ de plus de 2 jours"
  rclone delete "$REMOTE:chaine" --min-age 2d --drive-use-trash=false -q 2>/dev/null || true
  rclone rmdirs "$REMOTE:chaine" --leave-root -q 2>/dev/null || true

  # 1) le résultat est-il déjà sur ton Drive ? (ex. fond vert de plus de 2 Go envoyé dans gdrive:resultats)
  if [ -z "$EXIST" ] && [[ "$SRC" == *:* ]] && [ ! -e "$SRC" ]; then EXIST="$SRC"; SRC=""; fi
  if [ -n "$EXIST" ]; then
    R="$(taille_drive "$EXIST")"
    if [ -n "$R" ] && { [ -z "$SRC" ] || [ "$R" = "$(stat -c %s "$SRC")" ]; }; then
      FID="$(id_drive "$EXIST")"
      CHAINE_VIDEO_NOM="$(basename "${EXIST#*:}")"
      [ -n "$FID" ] && echo "Déjà sur Google Drive : $EXIST ($((R / 1048576)) Mo) → réutilisé, aucune copie."
    elif [ -z "$SRC" ]; then
      err "vidéo introuvable sur Google Drive : $EXIST"
    fi
  fi

  # 2) sinon : envoi du fichier local dans chaine/<id>/
  if [ -z "$FID" ]; then
    [ -s "$SRC" ] || err "vidéo introuvable : $SRC"
    BASE="$(basename "$SRC")"; BASE="${BASE%.*}"
    BASE="$(python3 -c 'import re,sys,unicodedata as u;t=u.normalize("NFKD",sys.argv[1]).encode("ascii","ignore").decode();t=re.sub(r"[^A-Za-z0-9._-]+","_",t).strip("_");print(re.sub(r"^moi[-_]*","",t,flags=re.I)[:50])' "$BASE")"
    NOM="moi-${BASE:-video}.mp4"
    DST="$REMOTE:chaine/$CHAINE_ID/$NOM"
    L=$(stat -c %s "$SRC")
    LIBRE="$(drive_libre)"
    if [ -n "$LIBRE" ] && [ "$LIBRE" -lt $((L + 300000000)) ]; then
      err "Google Drive presque plein : $((LIBRE / 1048576)) Mo libres, il en faut $((L / 1048576 + 300)) pour transmettre la vidéo au montage. Le fond vert est publié normalement. Libère de la place (corbeille Drive comprise), puis « Re-run failed jobs »."
    fi
    echo "Envoi : $SRC ($((L / 1048576)) Mo) → $DST"
    rclone copyto "$SRC" "$DST" --retries 5 --low-level-retries 20 --drive-chunk-size 64M || err "envoi Google Drive impossible : $SRC"
    R="$(taille_drive "$DST")"
    [ "$L" = "$R" ] || err "taille différente après envoi ($L octets ici, ${R:-?} sur Drive)"
    FID="$(id_drive "$DST")"
    CHAINE_VIDEO_NOM="$NOM"
  fi
  [ -n "$FID" ] || err "identifiant Google Drive introuvable pour la vidéo"
  CHAINE_VIDEO_ID="$FID"
  out chaine_id "$CHAINE_ID"; out video_id "$CHAINE_VIDEO_ID"; out video_nom "$CHAINE_VIDEO_NOM"
  echo "Vidéo prête pour le montage : $CHAINE_VIDEO_NOM (Drive $CHAINE_VIDEO_ID)"
}

lancer() {
  [ -n "${CHAINE_ID:-}" ] && [ -n "${CHAINE_VIDEO_ID:-}" ] || err "bon de livraison incomplet (identifiant de chaîne ou de vidéo manquant)"
  [ -n "${CHAINE_GH_TOKEN:-}" ] || err "Jeton de chaîne indisponible (GitHub App : variable CHAINE_APP_ID + secret CHAINE_APP_KEY, ou secret CHAINE_TOKEN). Le fond vert est publié ; corrige puis « Re-run failed jobs » (seul ce lancement est refait)."
  local ORIGINE="https://github.com/${GITHUB_REPOSITORY:-?}/actions/runs/${GITHUB_RUN_ID:-0}" OK=0 i
  python3 - "$CHAINE_ID" "$CHAINE_VIDEO_ID" "${CHAINE_VIDEO_NOM:-video.mp4}" "$FINDER" "$JOINTS" "$ORIGINE" "$VOIX" "$SHORTS_ST" > bon-montage.json <<'PY'
import json, sys
i, fid, nom, finder, joints, origine, voix, shorts_st = sys.argv[1:9]
print(json.dumps({"event_type": "chaine-montage", "client_payload": {
    "v": 1, "chaine_id": i, "video_id": fid, "video_nom": nom, "finder": finder,
    "joints": joints, "origine": origine, "voix_studio": voix, "shorts_sous_titres": shorts_st}}, ensure_ascii=False))
PY
  cat bon-montage.json
  for i in 1 2 3 4 5; do
    if GH_TOKEN="$CHAINE_GH_TOKEN" gh api "repos/$CIBLE_REPO/dispatches" --input bon-montage.json >/dev/null 2>dispatch.err; then OK=1; break; fi
    echo "::warning::lancement de Montage LV final : essai $i échoué ($(tail -1 dispatch.err))"; sleep $((i * 15))
  done
  [ "$OK" = 1 ] || err "Montage LV final n'a pas pu être lancé : $(tail -2 dispatch.err | tr '\n' ' '). L'app « chaine-maxmcneil » a-t-elle « Contents : Read and write » et est-elle installée sur Montage-LV-final ? Corrige puis « Re-run failed jobs »."
  { echo "### 🔗 Chaîne lancée — \`$CHAINE_ID\`"
    echo "- Vidéo transmise (privée, Google Drive) : \`${CHAINE_VIDEO_NOM:-?}\`"
    echo "- 🔒 Recherche automatique désactivée : seuls TES fichiers du dossier Drive « $JOINTS » (photos, vidéos, captures d'articles) sont montés"
    echo "- Suite : [Montage LV final → Actions](https://github.com/$CIBLE_REPO/actions) puis Shorts dans Push-overlay"
    echo "- Résultats finaux : releases \`out-chaine-$CHAINE_ID\` (gardées 2 jours)"; } >> "$SUM"
  echo "::notice title=Chaîne $CHAINE_ID::Montage LV final lancé (pièces jointes : dossier « $JOINTS »)"
}

case "$ACTION" in
  verifier)
    rclone_pret
    [ "${CHAINE_JETON_CONFIGURE:-}" = "true" ] \
      || err "Jeton de chaîne absent : ajoute la variable CHAINE_APP_ID + le secret CHAINE_APP_KEY (GitHub App, recommandé), ou le secret CHAINE_TOKEN. Voir README, section Chaîne."
    # « montage-joints » est un PLUS : vide ou absent, la chaîne continue (jamais d'arrêt pour ça)
    N=$(rclone lsf "$REMOTE:$JOINTS" --files-only -R 2>/dev/null | grep -ciE '\.(jpe?g|png|webp|gif|bmp|heic|heif|avif|mp4|mov|mkv|webm|avi|m4v|3gp|mts)$' || true)
    echo "Dossier « $JOINTS » : ${N:-0} photo(s)/vidéo(s) en plus pour le montage$([ "${N:-0}" -gt 0 ] || echo " (vide : pas grave)")."
    LIBRE="$(drive_libre)"
    [ -n "$LIBRE" ] && echo "Google Drive : $((LIBRE / 1048576)) Mo libres."
    if [ -n "$LIBRE" ] && [ "$LIBRE" -lt 1500000000 ]; then
      echo "::warning title=Chaîne::Google Drive presque plein ($((LIBRE / 1048576)) Mo libres) : un résultat de plus de 2 Go ne pourra pas être transmis. Libère de la place si la vidéo est longue."
    fi
    { echo "### 🔗 Chaîne activée"; echo "Fond vert → Montage LV final (tes pièces jointes : dossier « $JOINTS ») → Shorts."
      [ -n "$LIBRE" ] && echo "Google Drive : $((LIBRE / 1048576)) Mo libres."; } >> "$SUM"
    ;;
  deposer) deposer "${2:-}" "${3:-}";;
  lancer)  lancer;;
  envoyer) deposer "${2:-}" "${3:-}"; lancer;;
  *) err "usage : chaine.sh verifier | deposer <vidéo> [drive] | lancer | envoyer <vidéo> [drive]";;
esac
