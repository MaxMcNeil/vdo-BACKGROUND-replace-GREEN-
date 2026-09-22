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

Le workflow télécharge la vidéo depuis la Release, la traite avec FFmpeg, puis publie automatiquement une **nouvelle Release** nommée `result-<numéro de run>` contenant `result.mp4`. Le fichier est aussi disponible en artifact pendant 14 jours (onglet Actions > le run > Artifacts) si tu préfères le récupérer rapidement sans passer par les Releases.

## Réglages à ajuster selon ton tournage

Dans `.github/workflows/replace-background.yml`, ligne du filtre `chromakey` :

```
chromakey=0x00B140:0.12:0.08
```

- `0x00B140` = couleur du vert à supprimer. Si le keying est imparfait, prends une capture d'écran de ta vidéo, pipette la couleur exacte de ton tissu vert, et remplace cette valeur.
- `0.12` = **similarity** (tolérance sur la couleur). Trop bas → il reste du vert. Trop haut → ça mange dans le sujet.
- `0.08` = **blend** (douceur du dégradé sur les bords). Augmente légèrement (`0.1`-`0.15`) si les contours sont trop durs/dentelés.

Le paramètre `despill=mix=0.5` retire la teinte verte résiduelle qui "baverait" sur les cheveux/épaules après le chromakey.

## Conseils tournage (déjà bien partis avec 2m de distance)

- Éclaire le fond vert **séparément** du sujet, pour un vert bien uniforme sans ombre portée
- Évite les vêtements verts ou brillants (reflets)
- Shutter speed rapide si possible (réduit le flou de mouvement sur les bords, source n°1 des glitchs)

## Limites GitHub Actions (runners gratuits)

- Pas de GPU : traitement CPU uniquement, compter environ 1,5x à 2x la durée de la vidéo en temps de traitement
- Releases : jusqu'à 2 Go par asset, largement suffisant pour la plupart des vidéos courtes/moyennes
