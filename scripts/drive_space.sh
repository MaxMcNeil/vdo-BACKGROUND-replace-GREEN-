#!/usr/bin/env bash
# Vérifie qu'il reste assez de place sur Google Drive AVANT d'envoyer (ou de calculer) un gros fichier.
#   usage : bash scripts/drive_space.sh <destination rclone ou vide> <octets nécessaires> "<ce qui sera envoyé>"
# Code 0 : assez de place, OU place impossible à mesurer (jamais bloquant dans ce cas : l'envoi dira lui-même s'il échoue).
# Code 1 : pas assez de place → message clair (combien de libre, combien il faut, quoi faire).
# Le secret RCLONE_CONFIG est lu via RCLONE_CONFIG_CONTENT si rclone n'est pas encore configuré.
set -uo pipefail
DEST="${1:-}"; NEED="${2:-0}"; WHAT="${3:-le résultat}"
command -v rclone >/dev/null 2>&1 || { echo "Place sur Google Drive : non vérifiée (rclone absent)"; exit 0; }
if [ ! -s ~/.config/rclone/rclone.conf ] && [ -n "${RCLONE_CONFIG_CONTENT:-}" ]; then
  mkdir -p ~/.config/rclone && printf '%s\n' "$RCLONE_CONFIG_CONTENT" > ~/.config/rclone/rclone.conf
fi
if [[ "$DEST" == *:* ]]; then
  REMOTE="${DEST%%:*}"
else
  REMOTE="$(rclone listremotes --long 2>/dev/null | awk -F: '$2 ~ /drive/ {print $1; exit}')"
  [ -n "$REMOTE" ] || REMOTE="$(rclone listremotes 2>/dev/null | head -1 | tr -d ':')"
fi
[ -n "$REMOTE" ] || { echo "Place sur Google Drive : non vérifiée (aucun stockage configuré)"; exit 0; }
FREE="$(timeout 60 rclone about "$REMOTE:" --json 2>/dev/null | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); f=d.get("free")
    print(int(f) if f is not None else "")
except Exception: print("")' 2>/dev/null || true)"
if [ -z "$FREE" ]; then
  echo "Place sur Google Drive : non mesurable (stockage « $REMOTE ») — l'envoi le dira s'il manque de place"
  exit 0
fi
NEED=$(( ${NEED%.*} + 0 ))
echo "Place sur Google Drive « $REMOTE » : $((FREE / 1048576)) Mo libres, $((NEED / 1048576)) Mo nécessaires pour $WHAT"
if [ "$FREE" -lt "$NEED" ]; then
  echo "::error title=Google Drive plein::Il reste $((FREE / 1048576)) Mo sur Google Drive, il en faut environ $((NEED / 1048576)) pour $WHAT. Libère de la place (et VIDE LA CORBEILLE de Drive : elle compte), puis relance (« Re-run failed jobs » si les morceaux sont déjà traités)."
  exit 1
fi
exit 0
