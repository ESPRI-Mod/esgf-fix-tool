# Dernières décisions : intégrations et coordonnées

Mise à jour latitude : [reconstruction conventionnelle approuvée le 12 novembre](conventional-latitude-bounds-20261112.md), pour les huit transports et msfty. Elle remplace les bornes natives et le statut review de rlat décrits ci-dessous.

Note historique : voir [QA v5 — nettoyage final](final-cleanup-20261110.md) et la [conversion edt/evu approuvée le 12 novembre](edt-evu-centering-20261112.md), qui remplacent les stratégies décrites ci-dessous.

Source : rapport complet QA_results_v4 du 2026-11-10 à 09:46, cluster et snapshots
individuels. Les nombres ci-dessous désignent des fichiers distincts, pas des
modèles de messages. Cette décision remplace les anciennes stratégies indiquées
comme review ou comme retrait de axis sur seasurface.

| Règle | Fichiers dans QA v4 | Traitement |
| --- | ---: | --- |
| C051 | 4 : hfx et hfy, mensuel et journalier | Somme verticale des 75 contributions en W, sortie (time,y,x). |
| C073a | 5 | Ajouter seasurface:axis="Z", selon la dernière décision. |
| C102 | 23 : 6 à 79 niveaux et 17 à 80 interfaces | Préparation des coefficients puis normalisation partagée. |
| C104 | 2 : edt et evu | Même méthode que C102 pour les 80 interfaces, pas de nouveau calcul spécifique. |
| C103 | 8 profils par bassin | Ajouter lat_bnds(lat,bnds) depuis la colonne T native fournie. |
| C103r | 1 : msfty | Review : les bornes de grid_latitude rlat ne sont pas celles de la latitude géographique approuvée. |
| C059 | 2 : ta et zg à six heures | Arrondi des lon et bornes existant, reconnaissant bounds_lon ou lon_bnds. |
| C067/C068 | 2 chacun | Renommage des bornes et dimension bnds existants, réutilisés. |

## hfx/hfy : somme, données manquantes et conservation

Les champs XIOS concernés sont des contributions par couche ; aucune épaisseur
n'est multipliée. La méthode `integrate_heat_transport` additionne les valeurs
valides des 75 niveaux, avec un accumulateur float64 et un seul arrondi au type
stocké. Une colonne entièrement masquée reste masquée ; les couches masquées ne
contribuent pas. Une valeur infinie ou NaN non masquée bloque la transformation.
Les champs compactés scale_factor/add_offset sont refusés.

Les attributs d'origine, notamment cell_methods, long_name et description, sont
conservés conformément à la précision de l'utilisateur. Les seules suppressions
concernent olevel et ses bornes devenues inutiles. Une autre variable ou référence
CF utilisant cet axe bloque la suppression. Les coordonnées horizontales, le temps
et ses bornes sont conservés.

Les données sont traitées par blocs, sans charger la variable complète. La sortie
est produite dans le fichier temporaire partagé par toutes les transformations,
puis chaque somme est recalculée depuis la source pour vérifier le résultat.
Le remplacement atomique intervient après ces contrôles ; la source reste intacte
si le calcul ou la validation échoue. Une seule réécriture regroupe les corrections.
Compression zlib, niveau (dont 2), shuffle et chunks des axes conservés sont retenus.
Rejouer sur un champ déjà (time,y,x) ne réintègre pas les données.

## Hybride : préparation partagée C074/C074a puis C102

Une erreur C102 seule ne signifie pas que les dimensions des coefficients sont
correctes. La normalisation appelle maintenant la même préparation de référence
que C074, même en l'absence d'un diagnostic C074 distinct. Elle ne rajoute pas une
règle au plan ; elle réutilise son implémentation et ses vérifications.

La sélection 79/80 repose sur la longueur **et la correspondance du vecteur lev
complet** à la référence fournie, en pression ou déjà normalisé. Les empreintes
stockées dans les snapshots confirment cette correspondance pour les 23 + 2
fichiers recensés ; les valeurs sont vérifiées de nouveau à l'exécution.

- 79 niveaux : référence de centres fournie, ap/b sur lev et bornes sur (lev,bnds).
  Les anciens tableaux (bnds,klev) sont remplacés à l'aide de la référence
  explicitement fournie. lev=ap/101325+b et lev_bnds=ap_bnds/101325+b_bnds.
  Chaque niveau doit être compris dans ses bornes avant écriture.
- 80 interfaces, dont edt/evu : les ap/b existants doivent être exactement égaux
  aux coefficients des 80 interfaces fournis. Ils sont réalignés sur lev sans
  modifier leurs valeurs. lev=ap/101325+b, long_name indique les interfaces ;
  aucune borne de couche n'est fabriquée ni attachée à cet axe d'interfaces.
  Les éventuels anciens tableaux de coefficients-bounds ne sont pas transformés
  en bornes d'interfaces ; ils restent préservés.

La normalisation conserve ses coefficients d'entrée **après** cette préparation,
ainsi que ps et le champ principal. Le contrôle final utilise ce point de référence.
Les attributs bounds sur ap/b et les anciens fix_original_* approuvés sont retirés.
L'attribut vide b_bnds:coordinates est supprimé. Toutes les opérations de structure
partagent le même fichier temporaire ; seuls les petits tableaux modifiés sont
écrits directement lorsque le schéma ne nécessite pas de reconstruction.

Les six produits estampillés alh qui ont 79 niveaux ne sont pas allongés à 80 et
leur branding n'est pas changé. Leur axe correspond à la référence 79 fournie ;
la question scientifique du branding/staggering reste distincte du présent calcul.

## Latitude : bornes natives provisoires, aucune extrapolation

La référence compacte `fixes/basin_latitude_reference.json` provient du fichier
`coordinates_xios.nc` fourni : colonne T d'indice **97 en base zéro**. Sa provenance
inclut le SHA256 du fichier source et les noms des tableaux. Aucun accès Internet
n'est nécessaire au TGCC. La totalité du vecteur latitude des huit snapshots
correspond exactement à cette colonne avec le dernier point corrigé à 89.36999.
La méthode vérifie de nouveau cette égalité avant de créer les bornes.

Les limites sont la moyenne des sommets 0/1 et 2/3, après promotion en float64.
Les extrémités restent natives : cellule sud de largeur nulle à -84.21752167 et
cellule nord orientée [89.40338898,89.26733398]. Les centres sont contenus dans les
intervalles, mais ces extrémités ne constituent pas une grille contiguë ordinaire.
Elles ne sont ni triées ni extrapolées. **Le prochain QA CF/CMIP7 sur une copie doit
confirmer les contrôles de géométrie et d'orientation.** Les NetCDF de simulation
n'étant pas présents sur ce Mac, ce QA réel n'a pas été exécuté ici.

Ajouter ces petits tableaux se fait directement dans le fichier ouvert, sans
réécrire le champ géophysique. La recette refuse d'écraser des bornes différentes.

## seasurface

C073a ajoute axis="Z". L'ancien nom de méthode drop_osurf_axis suit aussi cette
nouvelle décision, afin qu'un ancien plan ne retire pas l'attribut. C073 sur les
éventuels messages CF units/positive reste en review : la stratégie antérieure de
retrait de Z contredirait maintenant la décision. Aucun units/positive n'est inventé.

## Lancement

Transférer le dossier complet mis à jour, puis régénérer le plan :

```bash
report="../QA_results_v4/qa_result_20261110-0946_d34f748ccff6dbe539c21af6fbe15340.json"
fix_tool plan "$report" -o fixplan.v4.json
fix_tool apply fixplan.v4.json --only-under "$copie" --fixlog fixlog.v4.jsonl
```

Ces commandes utilisent les chemins du rapport. Ajouter --map-path seulement si
la copie à corriger se trouve à un autre emplacement. Les règles en review et
waive ne produisent aucune écriture. L'analyse locale donne 137 étapes pour 62
fichiers sur ce rapport ; le plan complet comprend aussi les corrections déjà
présentes dans le dépôt.
