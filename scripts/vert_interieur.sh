#!/usr/bin/env bash
# Partie « couleur » du filtre ffmpeg : retrait du vert sur la personne.
#   usage : bash scripts/vert_interieur.sh <doux|plein> <largeur> <hauteur>
# Entrées attendues dans le graphe : [c] (image) et [mat] (masque). Sortie : [fg] (personne détourée).
#   plein : table unmix.cube appliquée pixel par pixel partout (réglage d'origine).
#   doux  : bords (contour, zones semi-transparentes) = EXACTEMENT le réglage d'origine ;
#           intérieur (veste, chemise, barbe) = même correction, calculée sur une image adoucie puis ajoutée
#           à l'image d'origine : couleur moyenne identique, mais le grain de la caméra n'est plus amplifié.
MODE="${1:-doux}"; OW="$2"; OH="$3"
if [ "$MODE" = "plein" ] || [ -z "$OW" ] || [ -z "$OH" ]; then
  echo "[c]format=gbrp,lut3d=file=unmix.cube:interp=tetrahedral,format=rgba[cl];
            [cl][mat]alphamerge[fg];"
  exit 0
fi
echo "[mat]split=2[matA][matW];
            [c]format=gbrp,split=3[c1][c2][c3];
            [c1]lut3d=file=unmix.cube:interp=tetrahedral[full];
            [c2]scale=iw/2:ih/2:flags=area,gblur=sigma=1.5,split=2[b1][b2];
            [b1]lut3d=file=unmix.cube:interp=tetrahedral[bl];
            [bl][b2]blend=all_mode=grainextract,scale=$OW:$OH:flags=bilinear[dc];
            [c3][dc]blend=all_mode=grainmerge[inn];
            [matW]scale=iw/8:ih/8:flags=area,lut=y='255*gte(val,250)',erosion,gblur=sigma=1,scale=$OW:$OH:flags=bilinear,format=gbrp[wm];
            [full][inn][wm]maskedmerge,format=rgba[cl];
            [cl][matA]alphamerge[fg];"
