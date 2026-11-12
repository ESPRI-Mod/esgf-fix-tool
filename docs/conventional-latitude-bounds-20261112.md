# Bornes conventionnelles communes aux neuf diagnostics NEMO

La décision utilisateur du 12 novembre remplace la suppression de lat_bnds et
l'exception de bornes manquantes. Les neuf fichiers signalés par le QA complet
QA_results_v6_fixed sont désormais corrigés par C103, avec la même méthode
`add_basin_latitude_bounds` dans `fixes/basin_latitude_bounds.py`.

| Produits | Coordonnée conservée | Bornes créées |
| --- | --- | --- |
| hfbasin, hfbasinpmadv, hfbasinpmdiff, htovgyre, htovovrt, sltbasin, sltovgyre, sltovovrt | lat(lat), latitude, degrees_north | lat_bnds(lat,bnds) |
| msfty | rlat(rlat), grid_latitude, degrees, axis=Y, long_name="Grid Latitude" | rlat_bnds(rlat,bnds) |

Les neuf axes sont vérifiés contre les 332 positions de la référence déjà incluse
dans l'outil, extraite de la colonne 97 de coordinates_xios.nc. Les 331 premières
positions sont conservées. La dernière est harmonisée à 89.36999, y compris
dans msfty où elle remplace l'ancienne correction par np.nextafter.
Les noms de coordonnées, les dimensions et leurs autres attributs sont conservés.

## Calcul retenu

Les limites internes sont les milieux des points successifs. Les deux limites
externes sont extrapolées d'un demi-pas, sans les fixer aux pôles. Au nord, le
dernier pas presque nul est remplacé pour cette extrapolation par le dernier pas
non dégénéré, entre les indices 329 et 330 (indices Python).

```python
edges[1:-1] = 0.5 * lat[:-1] + 0.5 * lat[1:]
edges[0] = lat[0] - 0.5 * (lat[1] - lat[0])
edges[-1] = lat[-1] + 0.5 * (lat[-2] - lat[-3])
bounds = np.column_stack((edges[:-1], edges[1:]))
```

Pour cet axe, la limite sud est -84.27803802490234 degrés et la limite nord est
89.48226798461914 degrés. Un contrôle géographique vérifie leur appartenance à
[-90,90], sans les modifier ni les remplacer par les latitudes des pôles.
Les intervalles sont strictement croissants, contigus et contiennent leur centre.
La dernière bande a ainsi une largeur positive significative, malgré les deux
centres nord presque confondus. lat_bnds et rlat_bnds sont identiques numériquement.

Ces bornes sont **conventionnelles**, non les limites physiques natives des
sections de transport NEMO. Un commentaire sur la variable de bornes indique
explicitement la construction utilisée. Les sommets natifs ne sont pas recopiés.

## Compatibilité et écriture

C103 passe de waive à fix et inclut msfty. C103d, qui traitait les bornes natives
chevauchantes ou inversées, et C103f, qui traitait la cohérence inter-datasets,
réutilisent cette même méthode. L'ancien nom de méthode drop_basin_latitude_bounds
reste accepté dans les anciens plans, mais délègue désormais à la reconstruction :
aucun ancien retrait ne peut annuler la nouvelle décision.

Lorsque C025 est sélectionné pour msfty, il s'exécute avant la recette de bornes et
utilise le même point terminal 89.36999. C103r reste un review pour les autres
grilles hors des neuf produits approuvés, qui ne sont pas extrapolées arbitrairement.

La création ou mise à jour des petits axes et de leurs bornes est faite directement
dans le fichier ouvert. Aucune réécriture de la variable géophysique n'est requise.
Si d'autres transformations structurelles sont sélectionnées, elles partagent
la réécriture unique existante. Les anciennes bornes de type/dimensions différents
peuvent également nécessiter cette réécriture. Le rejeu conforme n'écrit rien.

## Lancement ciblé

Après transfert de l'outil complet au TGCC, régénérer le plan depuis le rapport
complet (et non le cluster). Ces commandes ne sont pas exécutées sur la simulation
depuis le Mac :

```bash
report="../QA_results_v6_fixed/qa_result_20261112-0920_137cced9a925110e3b9a73fc9ebfe648.json"
fix_tool plan "$report" -r C103 -o fixplan.latitude.json
fix_tool apply fixplan.latitude.json --only-under "$copie" --fixlog fixlog.latitude.jsonl
```

Sans -r, le plan standard inclut également ces neuf fichiers et les autres règles
actives. Relancer ensuite le QA/QC CF et CMIP7, notamment COORD008, sur ces fichiers.
