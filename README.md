# video_background_replace

Remplace automatiquement un fond vert (chroma key) par une image de fond, via GitHub Actions + FFmpeg.

- Vidéos **horizontales et verticales** (détection automatique, fond dédié à chaque orientation)
- **Mesure automatique** de la couleur du tissu vert : plus de réglage à la main
- Cadence d'images d'origine conservée (30 i/s reste 30 i/s), vidéos **HDR** converties automatiquement en SDR
- **Vidéos longues** : découpage en morceaux traités en parallèle, résultat envoyé sur ton stockage externe

Les vidéos (volumineuses) passent par les **Releases GitHub** (jusqu'à environ 2 Go par fichier), pas par des commits Git classiques (limités à 100 Mo).

## Fichiers du dépôt

| Fichier | Rôle |
|---|---|
| `.github/workflows/replace-background.yml` | Workflow principal (vidéos courtes, et aiguillage automatique) |
| `.github/workflows/replace-background-long.yml` | Workflow des vidéos longues (lancé automatiquement, ou à la main) |
| `.github/workflows/replace-background-assemble.yml` | Secours : recolle des morceaux déjà traités si l'étape finale a échoué |
| `.github/workflows/replace-background-beauty.yml` | Même traitement + yeux bleus (workflow séparé, optionnel ; la peau n'est pas retouchée) |
| `scripts/` | Programmes du workflow Beauté (accès à la vidéo, mesure, retouche image par image) |
| `models/` | Modèles MediaPipe de détection du visage et de la silhouette (licence Apache 2.0) |
| `input/background.jpg` | Image de fond pour les vidéos **horizontales** |
| `input/background-vertical.jpg` | Image de fond pour les vidéos **verticales** |

Garde **les deux images** dans `input/`, même si tu ne filmes qu'en horizontal : le workflow principal vérifie la présence du fond horizontal au démarrage.

## Utilisation (vidéo courte, 15 min ou moins)

### 1. Mettre la vidéo en Release

Sur GitHub : onglet **Releases** → **Draft a new release**
- Choisis un tag (ex: `v1`)
- Attache ta vidéo (ex: `video.mp4`) comme asset
- Publie la release

### 2. Lancer le traitement

Onglet **Actions** → **Replace Green Screen Background** → **Run workflow**, puis :

| Champ | Rôle |
|---|---|
| `release_tag` | Tag de la release qui contient la vidéo (ex: `v1`) |
| `video_asset` | Nom exact du fichier attaché à cette release (ex: `video.mp4`) |
| `background_file` | Fond horizontal dans `input/` (défaut : `background.jpg`) |
| `background_file_vertical` | Fond vertical dans `input/` (défaut : `background-vertical.jpg`) |
| `key_color` | Optionnel : couleur du fond vert en hex (ex: `139D74`). Vide = mesure automatique |
| `force_single` | Optionnel : `true` pour forcer le traitement normal même si la vidéo est longue |

### 3. Récupérer le résultat

Le workflow publie une **nouvelle Release** nommée `result-<numéro de run>` contenant `result.mp4`. Le fichier est aussi disponible en artifact pendant 14 jours (onglet Actions → le run → Artifacts).

## Horizontal ou vertical

Le workflow lit les dimensions de la vidéo (rotation des téléphones comprise) :
- **Horizontale 16:9** : sortie 1920×1080 avec `background.jpg`
- **Autre format horizontal ou carré** (4:3, 1:1, etc.) : la sortie garde les proportions de la vidéo (ex: 1440×1080), avec `background.jpg`. Plus d'étirement.
- **Verticale** : sortie à la taille de la vidéo (ex: 1080×1920) avec `background-vertical.jpg`.

Dans tous les cas, l'image de fond est **recadrée au centre** pour remplir l'écran, sans jamais l'étirer : pour la verticale, utilise une image de fond **au format portrait**.

## Cadence et HDR

- **Cadence d'images** : le résultat garde celle de la vidéo d'origine (24, 25, 30, 50, 60 i/s, ou les variantes 29,97 / 23,976). Une cadence variable (téléphone) est ramenée à une cadence constante.
- **HDR** (HLG ou HDR10, par exemple filmé avec « HDR vidéo » activé) : le workflow le détecte et convertit la vidéo en SDR avant le traitement. Sans cette conversion, la couleur du fond serait mal mesurée et le sujet pourrait disparaître. La conversion a été testée sur une vidéo HLG simulée, pas sur un vrai fichier de téléphone : si le rendu d'une vraie vidéo HDR ne te convient pas, filme en SDR.

## Nettoyage des bords (mur autour du tissu vert)

Si le tissu vert ne remplit pas toute l'image (mur blanc visible sur les côtés, plafond...), ces zones sont aussi remplacées par le fond, dans tous les workflows (`clean_edges`, activé par défaut) :
- seul ce qui est **à la fois** jamais vert, immobile, jamais occupé par toi (silhouette repérée sur 16 images, avec une large marge) et relié au bord de l'image est considéré comme « mur » ; **partout ailleurs (toi, tes vêtements, le tissu), le résultat est strictement identique à avant** ;
- en dehors, seule ta silhouette est gardée (un bras tendu au-dessus du mur reste visible), repérée par un modèle de segmentation ;
- si le tissu remplit l'image, ou en cas de problème, rien ne change : la vidéo est traitée exactement comme avant. Mets `clean_edges` à non pour le désactiver.

## Réglages automatiques

Avant le traitement, le workflow mesure ton fond vert :
1. Il prend 12 images réparties dans la vidéo.
2. Il mesure la couleur médiane du fond sur les **bords** (bandeau du haut et côtés dans la partie haute de l'image), en ignorant ce qui n'est pas vert (mains, objets).
3. Il en déduit la couleur de clé, la tolérance du chromakey et le seuil de reconstruction des couleurs.

Pour que ça marche bien :
- Le tissu vert doit couvrir le haut et les côtés du cadre, **sans que tu touches les bords** pendant la vidéo.
- Pas d'ombres fortes sur le fond.
- Si la mesure n'est pas fiable (le workflow s'arrête avec le message « Mesure du fond vert peu fiable »), relance avec `key_color` renseigné.

Le traitement lui-même (chromakey serré, masque érodé et adouci, reconstruction de la couleur des bords flous, comme les doigts en mouvement) est identique pour les vidéos courtes et longues.

## Vidéos longues (plus de 15 minutes)

Si la vidéo dépasse **15 minutes**, le workflow principal lance automatiquement **Replace Green Screen Background (vidéos longues)** :
1. La couleur du fond est mesurée une seule fois pour toute la vidéo.
2. La vidéo est découpée en morceaux traités **en parallèle** : par défaut, environ 12 morceaux de 3 à 10 minutes selon la durée de la vidéo (réglable avec `chunk_minutes`).
3. Les morceaux sont recollés avec le son, puis envoyés sur ton stockage externe.
4. Un seul fichier est produit ; le lien (si ton stockage en fournit un) est affiché dans le résumé du run.

### Configuration du stockage externe (une seule fois)

1. Sur un ordinateur, installe [rclone](https://rclone.org/downloads/) et lance `rclone config` pour créer un stockage (Google Drive, Dropbox, etc.). Le fichier `rclone.conf` est alors créé (`rclone config file` donne son emplacement).
2. Sur GitHub : **Settings** → **Secrets and variables** → **Actions**
   - Onglet **Secrets** → **New repository secret** : nom `RCLONE_CONFIG`, valeur = le contenu complet de `rclone.conf`
   - Onglet **Variables** → **New repository variable** : nom `RCLONE_DEST`, valeur = dossier de destination (ex: `gdrive:resultats`)

Ne partage jamais le contenu de `rclone.conf` : il donne accès à ton stockage.

Sans cette configuration, une vidéo longue est traitée normalement par le workflow principal, avec un avertissement : le résultat risque de dépasser les limites de GitHub (voir plus bas).

### Si l'étape finale a échoué : workflow de secours

Les morceaux déjà traités restent sur ton stockage, dans le dossier `.tmp-run-<numéro du run>`. Inutile de tout retraiter : lance **Recoller des morceaux déjà traités (secours)** (`replace-background-assemble.yml`) avec :

| Champ | Rôle |
|---|---|
| `run_id` | Le numéro du run (celui du dossier `.tmp-run-...`) |
| `chunks` | Nombre de morceaux (1 s'il n'y a que `chunk_0`) |
| `has_audio` | `true` s'il y a des fichiers `.wav` |
| `out_name` | Nom du fichier final (ex: `ma_video_fond-remplace.mp4`) |

### Lancer à la main (vidéos de plus de 2 Go)

Une vidéo de plus de 2 Go ne peut pas passer par une Release. Dépose-la sur ton stockage, puis lance **Replace Green Screen Background (vidéos longues)** avec :

| Champ | Rôle |
|---|---|
| `source` | Chemin rclone de la vidéo, de la forme `nom_du_stockage:dossier/fichier.mp4` (ex: `gdrive:videos/long.mp4`, ou `gdrive:long.mp4` si le fichier est à la racine du Drive) |
| `dest` | Dossier de destination (vide = variable `RCLONE_DEST`) |
| `background_file` / `background_file_vertical` | Fonds, comme ci-dessus |
| `key_color` | Optionnel, comme ci-dessus |
| `chunk_minutes` | Durée de chaque morceau en minutes. Vide = automatique (défaut) |

Si la vidéo est dans une Release, renseigne `release_tag` et `video_asset` à la place de `source`.

### Google Drive : créer ton propre client_id

Avec le `client_id` partagé de rclone, rclone affiche l'avertissement « This remote uses rclone's shared Google Drive client_id, which is being retired and will stop working during 2026 ». Pour éviter une coupure, crée ton propre client_id en suivant https://rclone.org/drive/#making-your-own-client-id, puis refais `rclone config` et remplace le secret `RCLONE_CONFIG`.

## Beauté : yeux bleus (workflow séparé)

**Replace Green Screen Background + Beauté (yeux)** fait le même remplacement de fond que le workflow principal, puis retouche **uniquement toi** (le fond n'est jamais modifié). Les autres workflows ne sont pas touchés.

### D'où vient la vidéo

| Champ | Rôle |
|---|---|
| `source` | Un lien **Google Drive** (fichier partagé « Tous les utilisateurs disposant du lien »), **Dropbox** ou direct (`https://...`), ou un chemin **rclone** (ex: `gdrive:videos/ma_video.mp4`, voir « stockage externe »). Vide = Release |
| `release_tag` | Release contenant la vidéo. **Vide = la dernière Release qui contient une vidéo** |
| `video_asset` | Nom du fichier dans la Release. **Vide = la vidéo la plus récente de la Release** |

Tout vide : le workflow prend tout seul la dernière vidéo mise en Release.

### Réglages

| Champ | Rôle |
|---|---|
| `eye_strength` | Yeux : `0` = désactivé (yeux naturels), `30` = discret (défaut), `80` = marqué, `100` = maximum |
| `eye_color` | `gris_bleu` (défaut, discret), `bleu_clair`, `bleu_intense` ou `vert` |
| `vert_interieur` | `doux` (défaut) : retrait du vert plein sur les bords seulement, veste / chemise / barbe sans grain amplifié ; `plein` : partout, ancien réglage |
| `clean_edges` | Remplace aussi le mur / plafond visibles autour du tissu vert (défaut : oui) |
| `preview_seconds` | Test rapide : seulement les N premières secondes (ex: `15`) |
| `background_file`, `background_file_vertical`, `key_color` | Comme le workflow principal |
| `dest` | Dossier rclone où envoyer aussi le résultat (ex: `gdrive:resultats`) |
| `chunk_minutes` | Durée des morceaux traités en parallèle. Vide = automatique |

### Résultat

- Une Release `result-<numéro>` avec la vidéo (`<nom>_beaute.mp4`) **et `avant-apres.jpg`** : 3 images, l'original à gauche, le résultat à droite, pour juger d'un coup d'œil depuis le téléphone.
- Aussi en artifact pendant 14 jours. Si la source vient de rclone, si `dest` est rempli, ou si le résultat dépasse 2 Go, il est aussi envoyé sur ton stockage externe.

### Comment c'est fait

- **Yeux** : l'iris est suivi image par image (MediaPipe FaceLandmarker), positions lissées dans le temps. Seul l'iris **visible et plus sombre que le blanc de l'œil** est recoloré, avec un centre plus clair et un anneau extérieur plus sombre, comme un vrai œil clair. Pupille, reflets, cils et paupières restent intacts ; l'effet s'efface pendant un clignement, quand le regard est baissé, quand la tête tourne vite (image floue) et pour l'œil caché de profil.
- **Bords** : la zone du tissu vert est mesurée ; en dehors (mur, plafond), seule ta silhouette est gardée.
- **Toute durée** : la vidéo est découpée en morceaux traités en parallèle puis recollée (nombre exact d'images par morceau : pas de décalage du son).
- **Premier essai** : lance avec `preview_seconds` à `15` et regarde `avant-apres.jpg`. Ajuste `eye_strength` si besoin.
- **Dans le log** : l'étape « Fond remplacé + yeux » affiche `visage détecté sur X (Y %)`. Si Y est proche de 0, les yeux ne sont pas retouchés.

## 🔗 Chaîne : fond vert → Montage LV final → Shorts

Coche **enchainer** au lancement (*Replace Green Screen Background* ou *… + Beauté (yeux)*) : quand le fond vert
est terminé (et publié comme d'habitude), la vidéo part toute seule dans **Montage LV final**, puis le montage part
dans **Video to Shorts** (Push-overlay). Sans la case cochée, **rien ne change**.

| Choix au lancement | Ce qui se passe |
|---|---|
| `chaine_finder: oui` (défaut) | Vid-o-presse-finder **actif** : le montage cherche les sources de chaque affirmation de ta vidéo (vidéos, presse, Telegram, Reddit) et reprend le dernier lot du Finder |
| `chaine_finder: non` | **aucune** recherche Finder. Le dossier Drive `montage-joints` reste un plus dans les deux cas : pris s'il contient des fichiers, jamais bloquant s'il est vide |
| `chaine_voix_studio: oui` | Montage LV final applique la **voix studio chaude** (profondeur, clarté, volume régulier ; repli automatique sur ta voix normale au moindre doute). **non** par défaut |

**Où arrive quoi**
- Fond vert : comme d'habitude (release `result-…` ou ton Drive).
- Vidéos intermédiaires : Google Drive, dossier `chaine/<id>/` — **privé**, supprimé définitivement après **2 jours**.
- Montage : release **`out-chaine-<id>`** dans montage-lv-final (+ PLAN.md) ; Shorts : release **`out-chaine-<id>`**
  dans push-overlay. Ces releases sont **supprimées après 2 jours** : télécharge avant. Tes résultats manuels
  (`out-montage`, `out-shorts`, dossiers Drive « derniers ») ne sont **jamais** touchés par la chaîne.
- `montage-joints` : après un montage réussi, seuls les fichiers réellement utilisés sont retirés (corbeille Drive,
  récupérables 30 jours) ; un fichier ajouté pendant le montage reste pour la vidéo suivante.

**Robustesse**
- Tout est vérifié **avant** le traitement (Drive, jeton, dossier `montage-joints`) : un oubli coûte 1 minute, pas 1 heure.
- Chaque étape transmet un « bon de livraison » précis (identifiant Drive de la vidéo) : aucune étape ne devine
  « la dernière vidéo », deux chaînes lancées à la suite ne se mélangent pas.
- Une étape en échec s'arrête net avec un message clair dans son dépôt (onglet Actions) ; après correction :
  **Re-run jobs** sur ce run — la vidéo l'attend 2 jours sur Drive. Chaque dépôt a son propre run : relancer le montage ne refait pas le fond vert.
- « Test rapide » (Beauté, `preview_seconds`) + chaîne : refusé, pour ne pas monter un extrait de test.
- Espace Google Drive : un résultat déjà envoyé sur ton Drive (fond vert de plus de 2 Go, ou source rclone) est
  réutilisé tel quel, sans deuxième copie ; un montage de moins de 1,9 Go passe aux Shorts par sa release, sans Drive.
  L'espace libre est vérifié avant chaque envoi (message clair si le Drive est presque plein).
- Le lancement de l'étape suivante est un petit job séparé (« 🔗 Chaîne — lancer… ») : en cas d'échec,
  « Re-run failed jobs » ne refait que ce lancement, jamais le fond vert ni le montage.
- Aucun cron : la chaîne ne se déclenche que quand tu lances le fond vert avec la case cochée.

**Mise en place (une fois, depuis le téléphone)**
1. **Même compte Google partout** : le secret `RCLONE_CONFIG` (ce dépôt) et `RCLONE_CONF` (montage-lv-final et
   push-overlay) doivent pointer vers le même Google Drive. Crée le dossier **`montage-joints`** à la racine de ce Drive.
2. **Jeton de la chaîne — GitHub App** (recommandé : n'expire jamais) :
   - github.com → *Settings → Developer settings → GitHub Apps → New GitHub App* ; nom libre (ex. `chaine-maxmcneil`),
     *Homepage URL* : l'adresse de ce dépôt ; **décoche** *Webhook → Active* ;
     *Repository permissions → Contents : Read and write* ; *Only on this account* → **Create GitHub App**.
   - Note l'**App ID** ; en bas : **Generate a private key** (un fichier `.pem` se télécharge).
   - *Install App* → ton compte → **Only select repositories** : `montage-lv-final` et `push-overlay`.
   - Dans **ce dépôt** et dans **montage-lv-final** : *Settings → Secrets and variables → Actions* :
     onglet **Variables** → `CHAINE_APP_ID` = l'App ID ; onglet **Secrets** → `CHAINE_APP_KEY` = tout le contenu du `.pem`.
   - Alternative plus simple mais qui expire : un *fine-grained token* (dépôts montage-lv-final + push-overlay,
     *Contents : Read and write*) dans un secret `CHAINE_TOKEN` de ces deux mêmes dépôts.
3. Test : lance une vidéo courte avec **enchainer** coché, puis suis les onglets Actions des trois dépôts.

## Dépannage

| Message | Que faire |
|---|---|
| `Mesure du fond vert peu fiable` | Relance avec `key_color` (ex: `139D74`), couleur mesurée sur une image de ta vidéo |
| `Image de fond verticale introuvable` | Vérifie `input/background-vertical.jpg` (ou le nom donné dans `background_file_vertical`) |
| `Secret RCLONE_CONFIG manquant` | Configure le stockage externe (voir plus haut) |
| Vidéo longue traitée normalement avec un avertissement | Le stockage externe n'est pas configuré : voir plus haut |
| `Fichier introuvable via rclone` | Le log liste ce que rclone voit dans le dossier : corrige le chemin (dossiers, majuscules, extension). Un fichier « Partagé avec moi » n'est pas visible |
| `Serveur rclone : réponse HTTP ...` | rclone voit le fichier mais ne peut pas le lire : vérifie les droits du stockage et le `client_id` (voir plus haut) |
| `Morceaux manquants dans ...` | Un morceau n'est pas sur le stockage : **Re-run failed jobs** dans le run, ou voir le workflow de secours |
| `Le morceau N n'apparaît pas sur le stockage après l'envoi` | L'envoi du morceau a échoué : relance ce job |
| `Espace disque insuffisant pour assembler` | La vidéo est trop longue pour un runner GitHub (environ 3 h maximum) : réduis la durée |
| `Impossible de lire des images de la vidéo` | Le fichier est corrompu ou sa durée est incohérente : ré-exporte la vidéo |

## Conseils tournage

- Éclaire le fond vert **séparément** du sujet, pour un vert uniforme sans ombre portée
- Évite les vêtements verts ou brillants (reflets)
- Vitesse d'obturation rapide si possible : elle réduit le flou de mouvement sur les bords, source n°1 des défauts autour des doigts
- Garde les bords du cadre libres : c'est là que la couleur du fond est mesurée

## Limites GitHub Actions (runners gratuits)

- Pas de GPU : traitement CPU uniquement. Le temps de traitement dépend du runner, compte plusieurs fois la durée de la vidéo pour une vidéo courte, d'où le découpage en parallèle pour les longues.
- Un job GitHub est limité à **6 heures** : c'est pourquoi les vidéos longues sont découpées en morceaux.
- Releases : environ 2 Go par fichier. Un résultat plus gros doit aller sur ton stockage externe. Si la publication de la Release échoue, le résultat reste disponible en artifact pendant 14 jours.
- Un résultat porte le nom de la source (`nom_fond-remplace.mp4`) : retraiter la même source écrase l'ancien résultat sur ton stockage.
