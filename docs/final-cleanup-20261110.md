# Dernières décisions après QA v5

Décision latitude remplacée le 12 novembre : [bornes conventionnelles communes aux neuf diagnostics NEMO](conventional-latitude-bounds-20261112.md). C103 ajoute désormais les bornes ; les anciennes recettes de retrait ne peuvent plus les supprimer. Le texte ci-dessous décrit la décision historique de QA v5.

Mise à jour : voir [les contrôles QA v6 du 12 novembre](lev-and-axis-bounds-20261112.md), notamment la priorité du label al et la normalisation commune de axis_nbounds.

Ces décisions remplacent celles des notes antérieures sur les bornes latitude des
profils par bassin, computed_standard_name, les unités de b et l'ordre time-first.
Source : QA_results_v5/qa_result_20261110-1439_f4a06d03d490aceb73665421b259cda8.json,
son cluster, les rapports individuels et leurs en-têtes consistency-output.

| Cas | Fichiers distincts | Traitement |
| --- | ---: | --- |
| fix_original_lat_bnds / lon_bnds | 2 | C115 : supprimer tous les anciens fix_original_*, après contrôle des références. |
| Bornes latitude des profils NEMO par bassin | 8 | C103d/C103f : retirer lat_bnds et lat:bounds. C103 ignore ensuite l'exigence CMIP7 de ces bornes. |
| Formule des bornes alternate_hybrid_sigma_half | 6 | C074b et préparation partagée : supprimer les bornes facultatives alh, sans changer lev/ap/b/ps. |
| Structures hybrides et anciens indices | 75 diagnostics, préparation partagée | C074c : reprendre la référence approuvée et supprimer les indices devenus inutilisés. Ne pas effacer wavelength. |
| axis_nbounds / nvertex | 2 signalés, 4 fichiers de la série vérifiés | C068a : normaliser les dimensions de taille 2 en bnds ; conserver les vrais sommets de taille 4. |
| Descriptions ta/zg | 2 signalés, 4 fichiers de la série vérifiés | C116 : Air Temperature / Geopotential height, type NC_CHAR identique ; conserver comment. |
| computed_standard_name sur lev | 81 | C107 passe à fix ; les recettes hybrides ne le recréent plus. |
| DR cell_methods, ordre spatial puis temporel | 5 | C106a : rétablir area: mean avant time: ..., y compris sur ps. |
| long_name de lev aux interfaces | 19 | C084 et attributs communs : hybrid sigma pressure coordinate. |
| long_name de ap/b pour edt/evu | 2 chacun | C084 et attributs communs : libellés sans (k) pour ces produits al. Garder les libellés (k) demandés pour alh. |
| units=1 sur b et b_bnds | 81 / 56 | C108 : supprimer cet attribut facultatif ; accepter les coefficients sans unités explicites. |

## Nettoyage et écritures

À chaque fichier sélectionné par apply, un nettoyage final partagé retire les
fix_original_* et les indices techniques klev, klev1, klevp1 lorsqu'ils n'indexent
plus aucune variable conservée et ne sont pas référencés. Leurs dimensions sont
supprimées seulement si elles deviennent inutilisées. Un indice encore utilisé
est conservé. Une référence CF active à une variable temporaire bloque sa suppression
avec un message explicite, sans inventer une nouvelle association.

Les remplacements d'auxiliaires ne créent désormais aucun nouveau fix_original_*,
même si un ancien appel utilise keep_original=True. Le fichier original est protégé
par la sortie temporaire et le remplacement atomique, plutôt que par des variables
persistantes dans le résultat. Les suppressions physiques peuvent nécessiter une
réécriture, regroupée une seule fois avec les autres modifications. Les attributs
seuls restent modifiés directement. Les tableaux scientifiques et leur compression
sont vérifiés ; aucune exécution sur les NetCDF réels n'a eu lieu sur ce Mac.

## Huit profils par bassin, exception ciblée

Le retrait des bornes est limité à hfbasin, hfbasinpmadv, hfbasinpmdiff, htovgyre,
htovovrt, sltbasin, sltovgyre et sltovovrt, sur g112 et le branding tavg-u-hyb-sea.
La recette vérifie la géométrie lat(332) et le vecteur fourni. Les autres lat_bnds,
notamment ceux des champs atmosphériques, ne sont pas supprimés.

La latitude, les indices numériques basin, sector et le champ principal sont
conservés. Les anciens alias de grille nav_lat/nav_lon/latitude/longitude et leurs
sommets sont retirés uniquement dans ces profils s'ils ne sont plus référencés.
Le manque de bornes latitude sera une exception CMIP7 assumée, plutôt qu'un motif
pour reconstruire des intervalles chevauchants ou inversés. Un ancien plan
add_basin_latitude_bounds suit désormais cette décision de retrait.

## Hybrides : famille et longueur sont distinctes

Le [vocabulaire officiel CMIP7](https://github.com/WCRP-CMIP/cmip7-cmor-tables/blob/main/tables/CMIP7_coordinate.json)
confirme must_have_bounds=no et z_bounds_factors vide pour alternate_hybrid_sigma_half,
contrairement à alternate_hybrid_sigma, dont les bornes sont requises.
La politique suit donc alh, même pour les six axes qui ont 79 points ; aucun 80e point
n'est ajouté. Les valeurs des coefficients actifs et de lev restent celles validées
avec la référence fournie. Les niveaux complets al à 79 points gardent leurs bornes.

Pour les axes sans bornes, lev:bounds puis lev_bnds/ap_bnds/b_bnds sont retirés après
vérification des autres références. Les dimensions/indices klev* devenus orphelins
sont retirés au nettoyage final. Les 80 interfaces conservent la structure sans
bornes approuvée précédemment, y compris edt/evu.

Toutes les recettes partagent les mêmes attributs finaux : pas de
computed_standard_name, pas de units sur b/b_bnds, long_name de lev générique.
Le libellé des termes ap/b suit la variante de la DR : sans (k) pour les produits al,
avec (k) pour alh. Les tableaux ap/ap_bnds/b/b_bnds sont remis sur lev et (lev,bnds)
lorsqu'ils existent et sont nécessaires ; bnds a la taille 2.

## Descriptions et comparaisons

Le rapport de cohérence peut désigner le fichier de comparaison alors que l'attribut
manque dans l'autre tranche temporelle. Le plan étend C116 et C068a aux fichiers
signalés du même dataset exact, sans traverser les produits ou les grilles. Ainsi
les deux tranches de ta, et les deux de zg, sont vérifiées, même avec -r C116 seul.

Les différences inter-datasets ne signifient pas toutes une anomalie. Les 79 centres,
les 80 interfaces, wavelength, les grilles océaniques et les labels de bassin ont des
rôles scientifiques distincts. Les correctifs retirent les restes inutiles et
harmonisent les structures approuvées ; ils ne rendent pas arbitrairement toutes
les listes de coordonnées identiques. Les comparaisons restantes restent en review
et demandent des groupes de produits comparables.

## cell_methods et cas encore ouverts

L'ordre prescrit est rétabli, par exemple :

```text
area: mean time: mean within years time: mean over years
```

C106a répare les cinq fichiers modifiés par l'ancien contournement. L'ancien nom
reorder_climatology_cell_methods restaure maintenant l'ordre DR, donc un ancien plan
ne peut plus remettre time en premier. Les moyennes/maxima/minima et les valeurs ne
sont pas recalculés. Les climatologies tclm encore non reconstruites continuent de
passer de 120 mois à 12 suivant la décision numérique antérieure ; une sortie déjà
à 12 mois ne repasse pas par cette moyenne. Le faux positif CF quotidien est ignoré
par C106r au lieu de modifier la syntaxe conforme à la DR.

Restent explicitement en review : les bornes rlat de msfty, et l'exigence de bornes
lev sur edt/evu, dont le branding al contraste avec les 80 interfaces physiques.
Aucune borne artificielle n'est créée pour ces deux fichiers. Les messages CF
units/positive de seasurface, le repacking et les comparaisons inter-datasets non
approuvées ne sont pas résolus par ce lot.

## Utilisation

Transférer l'outil complet, puis régénérer le plan depuis le rapport complet v5 :

```bash
report="../QA_results_v5/qa_result_20261110-1439_f4a06d03d490aceb73665421b259cda8.json"
fix_tool plan "$report" -o fixplan.v5.json
fix_tool apply fixplan.v5.json --only-under "$copie" --fixlog fixlog.v5.jsonl
```

Les chemins du rapport sont utilisés. Un --map-path n'est nécessaire que si la copie
se trouve ailleurs. Le QA réel au TGCC reste nécessaire pour constater les résidus.
