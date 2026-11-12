# Normalisation et bornes approuvées le 9 novembre

Mise à jour pour edt/evu : [conversion de 80 interfaces à 79 centres approuvée le 12 novembre](edt-evu-centering-20261112.md). Le précédent statut review de ces deux produits est remplacé par C103e/C104.

Les variantes ont été recoupées avec les rapports individuels de QA_results_v3_fixed.
Les recettes complètent les méthodes existantes et restent rejouables. Régénérer
les plans. La sélection par défaut charge désormais tous les catalogues, avec
rules/post_apply.yaml en premier.

| Recettes | Fichiers du rapport actuel | Correction |
| --- | ---: | --- |
| C102 / C103b | 62 / 56, avec recouvrement | lev=ap/101325+b ; lev_bnds=ap_bnds/101325+b_bnds. Vérifier les niveaux dans [0,1] et dans leurs bornes avant écriture. |
| C105 | 3 | sector(line,strlen=21), quatre labels dans l'ordre explicitement fourni ; supprimer les tableaux texte redondants/corrompus. |
| C095a / C105a | 1 | Reconnaître les six valeurs en mètres ou déjà en microns, puis écrire 5,15,25,35,50,75 avec units=micron. |
| C103a | 2 | Bornes des 18 couches thermiques ORCHIDEE selon le schéma fourni, avec limite inférieure du sol à 90 m. |
| C095 / C103c | 1 | Corriger l'unité de plev selon les valeurs vérifiées ; ajouter les bornes explicites plev7c de la DR. |
| C112 | 5 | Remplacer le commentaire par le texte attendu exact pour les cinq produits approuvés, si le contenu correspond toujours au QA. |
| C103 | 8 | Latitude des profils : review, en attente de la géométrie et des bornes nav_lat. |

## Axe hybride

P_REF vaut exactement 101325 Pa. La normalisation lit les coefficients présents
dans le fichier et ne les reconstruit pas. ap, b, ap_bnds, b_bnds et ps conservent
leurs valeurs. ap et b doivent être alignés sur lev et les coefficients de bornes
doivent déjà être sur (lev,bnds). Ils ne sont ni transposés ni réécrits par cette
méthode. Une ancienne représentation (bnds,klev), par exemple, est refusée avant
écriture et doit être traitée par le correctif de dimensions des coefficients.

lev doit correspondre à la pression issue de ses coefficients, ou à sa version
déjà normalisée. Les coefficients, unités et bornes doivent être finis et cohérents.
Tout niveau hors de ses bornes interrompt la préparation avant écriture.

La sortie contient lev:bounds=lev_bnds et les formula_terms demandés. Les attributs
ap:bounds et b:bounds sont retirés. Les variables fix_original_* non référencées
sont supprimées, après validation ; aucune référence active ne doit être perdue.
Sans suppression ni changement de structure, seules les petites coordonnées sont
écrites sur place. Une suppression physique impose la réécriture regroupée unique
du fichier, comme pour les décisions précédentes.

C074/C074a reste la recette de mise en cohérence avec les coefficients de référence.
C102/C103b effectue la normalisation à partir des coefficients présents. Leurs essais
de composition vérifient l'ordre des recettes et la conservation des coefficients.
Les deux produits edt/evu à 80 niveaux non identifiés restent un cas distinct en review.

## Passages de glace

Les trois produits sont siareaacrossline, simassacrossline et sisnmassacrossline.
Les quatre indices de line restent inchangés ; les valeurs scientifiques ne sont
pas déplacées. Les libellés sont exactement :

1. fram_strait
2. canadian_archipelego
3. barents_opening
4. bering_strait

sector est un tableau char(line,strlen), avec strlen=21, standard_name=region et
long_name=ocean passage. Les chaînes sont complétées par des espaces. Le champ
principal a coordinates=sector. Les anciennes variables line/line_label sont
retirées. Si C087 a déjà renommé str_len en strlen=255, le redimensionnement est
préparé sans tronquer d'autres chaînes. Une dimension partagée avec des variables
non prévues bloque la correction. Le renommage du fichier vers g014 reste rejouable.

## Bornes physiques

Les bornes thermiques suivent le schéma utilisateur avec transition à 2 m et fond
à 90 m. Les 19 limites calculées doivent croître strictement, contenir les 18 centres
et correspondre à la référence publiée arrondie à trois décimales, à 0,001 m près.
Les centres de depth sont conservés.

La définition plev7c provient de la copie vérifiée WCRP-universe au commit
e8711372fddb0295916afff1e90c6e5469d8537f, déjà conservée dans le projet.
Ses centres en Pa sont 90000,74000,62000,50000,37500,24500,9000 et ses limites
100000,80000,68000,56000,44000,31000,18000,0. L'ordre et les unités du parent
sont vérifiés. Dans cette simulation, ces valeurs sont déjà en Pa : un attribut
mb, ou hPa posé par la version précédente, est corrigé en Pa sans changer les
centres. Les sept paires de bornes sont ajoutées telles que fournies par la DR,
sans interpolation. Un axe réellement exprimé en hPa (900,740,...,90) reste en
hPa et reçoit les mêmes bornes physiques exprimées en hPa. Toute autre série
est refusée, avec ses valeurs et unités dans le message d'erreur.

C095 corrige uniquement l'unité ; C103c ajoute les bornes et vérifie aussi l'unité.
Exclure C103c n'ajoute donc pas ses bornes indirectement via C095. Régénérer les
anciens plans : les paramètres de C095 ont changé.
Les valeurs et les empreintes de la source sont embarquées dans bounds_specs.json ;
aucun accès réseau n'est requis au TGCC.

## Commentaires

Les cinq produits approuvés sont les trois passages de glace, wmo et wo.
La méthode fix_registry_comment existante reçoit replace_expected=true pour ces
cas. Elle remplace tout le commentaire par le texte attendu exact du QA. Un contenu
modifié depuis le rapport provoque un arrêt avant écriture. Les 47 corrections
d'échappements Markdown précédentes conservent leur comportement initial.

## Exécution

La commande complète avec le Python esgf-toolbox reste dans
[post-apply-20261106.md](post-apply-20261106.md).

```bash
fix_tool plan "$report" -o fixplan.all.json
fix_tool plan "$report" -r C095 -r C103c -o fixplan.plev.json
fix_tool plan "$report" --exclude-rule C103c -o fixplan.other.json
```

Les sélecteurs sont répétables ; --only-rule reste disponible. Appliquer le
nouveau plan sur la copie, puis relancer le QA/QC réel au TGCC. Les tests locaux
utilisent des NetCDF synthétiques et les cas exacts extraits des rapports.
