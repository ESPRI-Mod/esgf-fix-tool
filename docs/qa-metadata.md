# Recommandations CF et CMIP7 du 3 novembre 2026

Les 584 rapports individuels ont été lus à partir des scores structurés. Les nombres
ci-dessous comptent les messages dans les fichiers, y compris les doublons émis par
le checker. Ils diffèrent du nombre de formulations regroupées dans le rapport cluster.
Les métadonnées ont été vérifiées dans les JSON `consistency-output`.

| Règle | Messages | Décision et variantes |
| --- | ---: | --- |
| C075 | 58 | `temperature: on_scale` pour 32 fichiers en K et 13 en degC. `temperature: difference` pour les 13 tendances en K s-1, dont `tntmp`. |
| C076 | 1 | `tossq` en degC2 reste en revue. Confirmer la définition du second moment et l'échelle avant de renseigner `units_metadata`. |
| C077 | 572 | Avertissement ignoré dans cette simulation qui déclare CF 1.13. Le calendrier `proleptic_gregorian` est sans secondes intercalaires. |
| C078 | 9 | La décision du 9 novembre fournit sector(basin,strlen=21) avec global_ocean, atlantic_arctic_ocean, indian_pacific_ocean. Même méthode que C032/C096/C101a, sans réordonner les données. |
| C079 | 138 | Une opération par fichier, soit 46. Retirer le doublon `units="m"` sur `olevel_bounds` si le parent porte la même unité et référence ces bornes. Axes T/U/V/W, respectivement 33/5/5/3 fichiers. |
| C080 | 189 | Revue sur 166 fichiers. Cinq couples axe / nombre de points, précisés ci-dessous. |
| C081 | 39 | Avertissement ignoré pour les 39 identifiants vérifiés. La valeur est `area: areacello volume: volcello` et les deux variables sont déclarées dans `external_variables`. |
| C082 | 51 | Avertissement de syntaxe ignoré pour les noms CF `depth` (28), `longitude` (21), `height` (1) et `grid_longitude` (1). La vérification scientifique des intégrations reste indépendante. |
| C083 | 1 | `rsus` terrestre prend exactement `area: time: mean where land` si l'ancien texte correspond au rapport. |
| C084 | 2 | Les deux libellés DR pour `dbze` et `scatratio` sont ajoutés lorsque l'attribut est vide. |
| C085 | 572 | `Time Intervals` (531), `Time Points` (36), `Monthly Climatology` (2) et `Monthly Mean Daily Statistics` (3). La recette vérifie la cohérence du libellé avec `temporal_label`. |
| C086 | 341 | `lat` reçoit `Latitude` (13). `nav_lat` et `nav_lon` reçoivent `latitude` et `longitude` (164 chacun), avec prise en charge des noms après C071/C072. |
| C087 | 11 | `str_len` de taille 255 devient `strlen` pour `seasurface` (5) et `type` (6). |
| C088 | 328 | Les deux tableaux de sommets partagent la dimension de taille 4. Une opération `nvertex → vertices` sur chacun des 164 fichiers. Les dimensions de bornes de taille 2 relèvent de C068. |

Pour C080, les rapports signalent 85 points hors des bornes de longitude dans 143 fichiers,
86 dans 12, et 84 dans 11. Pour la latitude, ils signalent 52 points dans 12 fichiers
et 32 dans 11. Les empreintes des coordonnées et des bornes regroupent les fichiers par
géométrie. Le `grid_label` seul ne suffit pas, certaines géométries apparaissant aussi sous
`g112`. L'étape suivante consiste à comparer les centres et sommets d'origine et les masques
de ces géométries, ainsi que le calcul du checker.

Références [CF 1.13 §3.1.2, §3.5, §4.4.3, §7.1, §7.2 et §7.3.4](https://cfconventions.org/Data/cf-conventions/cf-conventions-1.13/cf-conventions.html).

Le plan de cette série comprend 1 204 opérations pour 577 fichiers. Ce sont des corrections
d'attributs et des renommages de dimensions. Sur des copies indépendantes, elles s'appliquent
directement si le même plan ne contient pas de transformation structurelle pour le fichier.

```bash
fix_tool plan "$report" -r rules/qa_metadata.yaml \
  --map-path "$source_root=$copie" -o fixplan.qa-metadata.json

fix_tool apply fixplan.qa-metadata.json --only-under "$copie" \
  --fixlog fixlog.qa-metadata.jsonl
```

Ajouter `--only-rule C079` à `plan` pour isoler une règle. Pour un plan complet, charger
`-r rules/qa_metadata.yaml` avant le catalogue `seed_20261029.yaml`.
