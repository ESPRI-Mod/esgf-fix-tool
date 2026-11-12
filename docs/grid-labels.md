# Région et identifiant de grille des sorties réduites

Lecture structurée des 584 rapports individuels du 3 novembre 2026, recoupée
avec le rapport complet et les fichiers `consistency-output`. Les trois familles
donnent 41 messages sur 33 fichiers. Le plan actualisé applique 24 opérations.

| Règle | Messages / fichiers | Décision |
| --- | ---: | --- |
| C089 | 8 / 4 | Garder `region=nh` et passer de `g112` à `g011`. |
| C090 | 8 / 4 | Garder `region=sh` et passer de `g112` à `g012`. |
| C091 | 8 / 8 | `g010` pour `co2mass` (`g111`, série temporelle) et les sept sorties océaniques globales réduites (`g112`, x=y=1 à l'origine). |
| C092 | 6 / 6 | `g014` pour trois produits à 16 détroits (`oline`) et trois produits de glace à quatre passages (`siline`). |
| C093 | 9 / 9 | Recommandation ignorée sur décision de l’utilisateur pour les huit profils `hyb` et `msfty`. C054 retire le singleton `x` et C025 complète maintenant `rlat`. |
| C094 | 2 / 2 | Passer à `g010` pour `zostoga` journalier et mensuel. C050/C111 vérifient désormais la réplication exacte puis conservent une valeur valide par date, sans moyenne. |

Les quatre variables hémisphériques sont `siarea`, `siextent`, `sisnmass`
et `sivol`. Chacune déclenche à la fois le contrôle de région et la
recommandation de grille dans chaque hémisphère. Les sept sorties océaniques
globales sont `bigthetao`, `masso`, `so`, `sos`, `thetao`, `tos` et `volo`.
Les transects sont `hfacrossline`, `mfo`, `sfacrossline`, `siareaacrossline`,
`simassacrossline` et `sisnmassacrossline`.

Les identifiants correspondent à la configuration `grid_topology.toml` du
checker et aux entrées du CV CMIP7 local. La recette vérifie la simulation,
la région, le produit et sa forme avant de modifier l'attribut global
`grid_label`. Elle accepte l'état déjà corrigé et les variantes avec ou sans
C049 ou C047 appliqués. Elle conserve les valeurs et l'ordre des données.
Ces opérations seules donnent `rewrite_count: 0`. Avec C049, les éventuelles
suppressions d'axes singleton restent regroupées avec les autres corrections
structurelles.

Le [DRS CMIP7](https://wcrp-cmip.github.io/cmip7-guidance/docs/CMIP7/Global_Attributes/#2-filenames)
inclut `grid_label` dans le nom du fichier. Sur la copie de travail, `apply`
garde les chemins du plan et écrit une entrée `drs_rename` avec `source` et
`target` dans le fixlog. Utiliser ces chemins à l'étape de renommage DRS,
après les corrections sur la copie. Un journal de simulation signale les
noms prévus avec `dry_run: true`.

Après le QA du 6 novembre, C097 dans `src/esgf_fix_tool/rules/post_apply.yaml` réalise désormais
ce renommage sur les fichiers dont ATTR005 signale l'incohérence. Il refuse les
collisions avant toute écriture et conserve l'inode et les tableaux. Le fixlog
indique `final_file`. Le même plan résout ce nom final lors d'un second passage.
Voir [la procédure complète](post-apply-20261106.md).

```bash
fix_tool plan "$report" -r src/esgf_fix_tool/rules/grid_labels.yaml \
  --map-path "$source_root=$copie" -o fixplan.grid-labels.json

fix_tool apply fixplan.grid-labels.json --only-under "$copie" \
  --fixlog fixlog.grid-labels.jsonl
```

Pour tester uniquement les sorties du nord, ajouter `--only-rule C089` à
`plan`. Pour un plan complet, ajouter `-r src/esgf_fix_tool/rules/grid_labels.yaml` avant
`src/esgf_fix_tool/rules/catalogue_fallbacks.yaml`.
