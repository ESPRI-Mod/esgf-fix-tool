# Recettes communes et interdépendances

## Bassins : quatre diagnostics, une méthode

C032, C078, C096 et C101a sont regroupés dans
[basin_coordinates.yaml](../rules/basin_coordinates.yaml). Tous appellent
`fix_basin_sector` dans
[reviewed_structures.py](../src/esgf_fix_tool/fixes/reviewed_structures.py).
Ces identifiants restent distincts pour retrouver chaque diagnostic dans le QA
et sélectionner ses déclencheurs avec `-r`. Ils ne désignent pas quatre traitements
scientifiques différents. Les appels répétés deviennent sans effet après le premier.

La méthode commune :

- Conserve la variable basin(basin), son type, ses valeurs et la dimension basin.
- Vérifie les codes 1,2,3 et leur ordre, puis écrit les noms de régions dans sector.
- Décrit basin comme un index : units=1 et long_name=Ocean Basin Index.
- Retire standard_name=region et les flag_values/flag_meanings/flag_masks de basin.
  La coordonnée texte sector porte standard_name=region et les trois noms approuvés.
- Ne change jamais cell_methods, y compris « spanning a basin ».

Cela traite ensemble les quatre causes : sector absent, région numérique invalide,
flags interdits sur une coordonnée et unités manquantes. Un nouveau QA au TGCC
doit confirmer la disparition effective des messages. Les fichiers issus d'anciennes
versions, où basin était déjà supprimé, restent acceptés ; la méthode ne reconstruit
pas une variable absente. C101, masque 2D aux codes 0–10, reste indépendant.

## Coordonnées texte redondantes

C099 utilise la méthode existante `rename_redundant_text_label`, dont le nom est
conservé pour les anciens plans. Son comportement est désormais la suppression
physique de line/type et des anciens alias line_label/type_label.

Pour les passages océaniques et landCoverFrac, les labels doivent être identiques
à ceux de sector, dans le même ordre. Seul le remplissage final NUL/espace est ignoré.
Une différence bloque la préparation, sans écriture. Si sector manque, les labels
valides sont d'abord copiés par la méthode existante d'ajout d'alias. Les références
sont redirigées vers sector, les doublons retirés de coordinates et la dimension
d'index conservée. Les tableaux scientifiques gardent leur ordre et leurs valeurs.

Pour les trois passages de glace corrompus à quatre indices, C099 délègue à la
méthode de C105, qui utilise les quatre noms et leur ordre déjà approuvés. Aucun
nouveau libellé n'est déduit des octets corrompus. C099 et C105 peuvent être rejoués
dans les deux ordres. C087 et C100 tolèrent les suppressions/renommages correspondants ;
si type a disparu, C087 retrouve son alias sector sur la dimension type.

Une suppression physique de variable impose une réécriture. Toutes les suppressions
et autres transformations d'un même fichier restent regroupées en une seule sortie.
Un simple ajout de sector avec conservation de basin reste une écriture directe.

## Température

C076 est passé de review à fix dans qa_metadata.yaml. La méthode existante
`add_temperature_units_metadata` reconnaît le cas vérifié : tossq,
standard_name=square_of_sea_surface_temperature, units=degC2,
cell_methods="area: mean where sea time: mean". Elle ajoute temperature: on_scale
sans convertir les valeurs. Une autre combinaison reste refusée.

## Organisation des catalogues

post_apply.yaml ne contient plus de règles scientifiques : c'est un manifeste
`include` de chemins relatifs vers les catalogues thématiques. Le chargeur lit chaque
fichier une seule fois, refuse les cycles et les identifiants dupliqués entre fichiers.
Les anciennes commandes qui listent à la fois post_apply.yaml et les autres YAML
restent utilisables. Les nouveaux plans chargent tous les catalogues par défaut.

| Catalogue | Contenu déplacé depuis post_apply.yaml |
| --- | --- |
| basin_coordinates.yaml | C096/C101/C101a, avec C032/C078 déplacés de leurs anciens fichiers |
| coordinate_metadata.yaml | C095/C095b : métadonnées plev |
| coordinate_values_bounds.yaml | C095a/C103/C103a/C103b/C103c/C105a/C109 : valeurs et bornes |
| surface_and_hybrid.yaml | C074a/C102/C104/C107/C108/C073a : coefficients, lev, osurf |
| qa_metadata.yaml | C099/C100/C105/C106/C108a : textes et recommandations |
| horizontal_coordinates.yaml | C098 : références horizontales résiduelles |
| dimension_order.yaml | C111 : zostoga, même méthode que C050 |
| grid_labels.yaml | C097/C110/C113 : nom de fichier, grille et branding |
| cmip7_ipsl.yaml | C112 : commentaires approuvés, avant la règle générique |
| consistency.yaml | C114 : comparaisons entre datasets, toujours en review |

Les ID et les méthodes sont conservés. Les résultats de classification du rapport
complet sont comparés avant/après déplacement : seul C076 change de décision.
Les tests couvrent notamment les 24 ordres des quatre diagnostics de bassin,
les permutations des suppressions/normalisations de chaînes, les anciens alias,
le refus de perte d'information et le rejeu sans écriture.

## Troisième essai au TGCC

Transférer tout le dossier mis à jour, y compris les nouveaux YAML et le chargeur.
Régénérer le plan depuis le JSON complet (pas cluster.json), puis appliquer sur la copie.

```bash
fix_tool plan "$report" -o fixplan.all.json
fix_tool apply fixplan.all.json --only-under "$copie" --fixlog fixlog.all.jsonl
```

Pour tester uniquement les cas de ce lot :

```bash
fix_tool plan "$report" -r C032 -r C078 -r C096 -r C101a -r C076 -r C099 -o fixplan.test.json
```
