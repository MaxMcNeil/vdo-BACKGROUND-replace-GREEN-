# video_background_replace

Remplace automatiquement un fond vert (chroma key) par une image de fond, via GitHub Actions + FFmpeg.

Les fichiers vidéo (volumineux) passent par les **Releases GitHub** (jusqu'à 2 Go/fichier), pas par des commits Git classiques (limités à 100 Mo).

## Comment ça marche

### 1. Uploader ta vidéo en Release

Sur GitHub : onglet **Releases** → **Draft a new release**
- Choisis un tag (ex: `v1`)
- Attache ta vidéo (`video.mp4`) comme asset
- Publie la release

### 2. Ajouter l'image de fond

Dépose ton image dans `input/background.jpg` (les images restent légères, donc en Git normal), et push.

### 3. Lancer le traitement

Onglet **Actions** → workflow **Replace Green Screen Background** → **Run workflow**, puis renseigne :
- `release_tag` : le tag de la release contenant la vidéo (ex: `v1`)
- `video_asset` : le nom exact du fichier vidéo attaché à cette release (ex: `video.mp4`)
- `background_file` : nom du fichier dans `input/` (par défaut `background.jpg`)

### 4. Récupérer le résultat

Le workflow télécharge la vidéo depuis la Release, la traite avec FFmpeg, puis publie automatiquement une **nouvelle Release** nommée `result-<numéro de run>` contenant `result.mp4`. Le fichier est aussi disponible en artifact pendant 14 jours (onglet Actions > le run > Artifacts).

## Réglage chromakey — calibré pour ton tissu vert

```
chromakey=0x228C70:0.15:0.10
```

Cette couleur (`#228C70`) a été mesurée directement sur une photo de ton tissu vert — ce n'est pas un vert "studio" classique (plus clair/saturé comme `#00FF00`), donc ne reviens pas à une valeur générique trouvée en ligne.

- `0x228C70` = couleur exacte du tissu, à remesurer si l'éclairage change notablement au tournage
- `0.15` = **similarity** (tolérance sur la couleur). Trop bas → il reste du vert. Trop haut → ça mange dans le sujet.
- `0.10` = **blend** (douceur du dégradé sur les bords). Augmente légèrement (`0.12`-`0.15`) si les contours sont trop durs/dentelés.

Le paramètre `despill=mix=0.5` retire la teinte verte résiduelle qui "baverait" sur les cheveux/épaules après le chromakey.

**Astuce fiabilité :** la couleur perçue dépend de l'éclairage réel au tournage. Si le premier résultat garde des traces de vert ou mange trop dans le sujet, extrais une frame de ta vraie vidéo (`ffmpeg -i video.mp4 -frames:v 1 frame.png`) et remesure la couleur dominante du fond dessus plutôt que de garder cette valeur telle quelle.

## Conseils tournage (déjà bien partis avec 2m de distance)

- Éclaire le fond vert **séparément** du sujet, pour un vert bien uniforme sans ombre portée
- Évite les vêtements verts ou brillants (reflets)
- Shutter speed rapide si possible (réduit le flou de mouvement sur les bords, source n°1 des glitchs)

## Limites GitHub Actions (runners gratuits)

- Pas de GPU : traitement CPU uniquement, compter environ 1,5x à 2x la durée de la vidéo en temps de traitement
- Releases : jusqu'à 2 Go par asset, largement suffisant pour la plupart des vidéos courtes/moyennes
