# Corrections après le QA du 6 novembre 2026

Dernière mise à jour : [normalisation hybride, passages de glace, rayons en microns,
bornes thermiques/plev7c et commentaires](normalized-coordinates-20261109.md).

Voir aussi [l'harmonisation des recettes et les catalogues thématiques](fix-interactions.md).
post_apply.yaml est désormais uniquement le manifeste de compatibilité.

Mise à jour du 9 novembre : [les dernières décisions approuvées](reviewed-structures-20261109.md)
couvrent aussi les sols, msfty, les neuf sector de bassins, zostoga et la suppression
des bornes instantanées et copies de coefficients. Régénérer les anciens plans.

Les 107 lignes du cluster sont recoupées avec le rapport complet et les 584 rapports
individuels. Seuls les résultats structurés en échec déclenchent les recettes.
Une recette disponible ne signifie pas que sa disparition a déjà été vérifiée sur
les véritables fichiers après cette nouvelle version.

## Adaptations

| Famille | Correction |
| --- | --- |
| C002a | Retirer les échappements Markdown des commentaires des 47 branded variables vérifiées, soit 51 fichiers. Préserver le texte et ses variantes de casse. |
| C020 | Compléter l'identité de la profondeur NEMO existante avec depth/Z/down et le nom lev_bnds. Vérifier des profondeurs finies, croissantes et en mètres. |
| C058/C087 | Couvrir typec3pft/typec4pft et la dimension de chaînes sector. Autoriser le renommage str_len→strlen dans le contrôle final d'une correction de libellé. |
| C060/C067 | Supprimer les bornes instantanées dégénérées, y compris les anciennes bornes détachées. Renommer aussi depth_bounds et plev_bounds. |
| C074/C074a | Reprendre les 17 axes d'interfaces à 80 niveaux et leurs coefficients de référence. Retirer ap:bounds/b:bounds, garder les formules sur les éventuelles bornes de lev. Conserver toutes les valeurs de lev. |
| C084 | Appliquer les long_name explicitement recommandés tant que l'ancienne valeur correspond encore au diagnostic. |
| C095 / C095a | Vérifier les centres plev7c : les valeurs 90000,...,9000 sont en Pa, même si l'attribut indique mb/hPa. Corriger l'unité sans changer les valeurs. Corriger aussi les rayons microne→micron avec validation des valeurs. |
| C032/C078/C096/C101a | Créer les trois labels sector approuvés ; conserver la variable numérique basin, sa dimension et l'ordre des données. |
| C101 | Convertir le seul masque basin(y,x) en int32 avec la table 0–10 approuvée ; préserver les codes et les points manquants. |
| C097 | Synchroniser les 24 noms de fichiers avec leur grid_label corrigé. Vérifier les cinq couples g111→g010 et g112→g010/g011/g012/g014. Refuser les collisions avant toute écriture. |
| C098 | Détacher les bornes des anciennes coordonnées horizontales inutilisées. Toute référence active bloque le retrait. |
| C099 | Supprimer les variables texte redondantes line/type et les anciens alias line_label/type_label après vérification des mêmes labels ordonnés dans sector. Garder les dimensions. |
| C076 | Ajouter units_metadata="temperature: on_scale" à tossq selon la décision utilisateur. |
| C100 | Remplacer le remplissage NUL des trois sector lisibles à 16 passages par des espaces. Garder les libellés et leur ordre. |

Les suffixes a/b désignent des déclencheurs d'une même famille, pas des méthodes
distinctes pour chaque message. C078 réutilise la méthode sector et ne recrée pas
de flags sur les axes 1D. Régénérer les anciens plans.

## Lancement au TGCC

Copier cette version de l'outil au TGCC. Le conteneur fournit Python/netCDF4.
Aucun accès Internet ni uv n'est requis pour ces recettes.

```bash
cd /ccc/work/cont003/cmip7/levavasg/esgf-fix-tool

fix_tool() {
  esgf-toolbox env PYTHONPATH="$PWD/src" python -u -m esgf_fix_tool.cli.main "$@"
}

report="../QA_results_v3_fixed/qa_result_20261106-1802_df22cad6bd5d2d4ff9ce6c70bee55ce8.json"
copie="/ccc/work/cont003/cmip7/levavasg/CM6-ESMCO2-FT-esm-pi-s.01"

fix_tool plan "$report" -o fixplan.post-apply.json

fix_tool apply fixplan.post-apply.json \
  --only-under "$copie" --fixlog fixlog.post-apply.jsonl
```

Les chemins du nouveau QA pointent déjà vers cette copie. Aucun --map-path n'est
nécessaire pour ce dossier. Pour une autre copie, mapper info.parent_dir vers la
nouvelle racine correspondante. Utiliser le JSON complet, pas le cluster.

Pour isoler les hybrides, ajouter `--only-rule C074 --only-rule C074a` à plan.
L'option `--dry-run` sur apply prépare les changements et vérifie les collisions
sans modifier les NetCDF ni leurs noms. C097 écrit le nom final dans le fixlog.
Le même plan est rejouable après le renommage.

## Informations restant à obtenir

| Famille | Informations nécessaires |
| --- | --- |
| C104 edt/evu | Confirmer pourquoi ces deux produits al utilisent 80 interfaces. |
| C051 hfx/hfy | Sens des 75 niveaux olevel dans les produits u. Cet axe n'est pas singleton. |
| C103 lat | Bornes de latitude des huit profils ; géométrie nav_lat restant à fournir. |
| C080 géométrie | Coordonnées/sommets effectifs, masques et reproduction du calcul du checker. |
| C106r/C109 | Les statistiques hors tclm mensuel et les bornes scalaires restent à investiguer. C106 reconstruit désormais les climatologies mensuelles : voir monthly-climatology.md. |
| C110 | Identifiant de grille enregistré pour les 13 sorties zonales. |
| C114 | Périmètre et référence des comparaisons entre datasets. |

FILE004 et PATH003 relèvent du repacking et de la publication DRS. Les exceptions
du checker restent visibles. Une politique waive n'efface aucun résultat du QA brut.

## Références et essais

- Rapports individuels et snapshots consistency-output de la campagne du 6 novembre.
- [Discussion partagée](https://chatgpt.com/share/6aef2015-7f34-83eb-89ad-b3107cfc5f52?no_universal_links=1).
- [CF 1.11, coordonnées, labels et bornes](https://cfconventions.org/Data/cf-conventions/cf-conventions-1.11/cf-conventions.html).
- Copies locales des vocabulaires WCRP/CMIP7 et de la configuration du checker.

Les essais locaux portent sur des NetCDF synthétiques et les paramètres extraits
des rapports. Un nouveau QA/QC au TGCC doit confirmer le résultat sur les fichiers réels.
