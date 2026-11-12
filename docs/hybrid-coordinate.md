# Axe hybride IPSL et coefficients de référence

Les recettes hybrides renseignent aussi les attributs suivants sur les bornes
des coefficients, lorsqu'elles existent ou sont créées :

```text
ap_bnds:long_name = "vertical coordinate formula term: ap(k+1/2)"
b_bnds:long_name = "vertical coordinate formula term: b(k+1/2)"
```

Le diagnostic CF signalant leur nom manquant est pris en charge par C084b,
qui réutilise le setter d'attributs de C084. Les identifiants historiques ATM et
les identifiants DRS publiés, y compris files.d20261112 et v20261112, sont reconnus.
La correction se fait directement sur les attributs, sans normaliser à nouveau
les valeurs ni interpoler le champ. Régénérer le plan depuis le nouveau rapport
complet pour traiter les fichiers déjà corrigés.

Depuis le QA du 6 novembre, C074 reconnaît aussi les 17 axes alh à 80 interfaces
sans standard_name. Leur empreinte correspond exactement à la pression de référence
du groupe à 80 niveaux. C074a reprend la même méthode pour retirer les attributs
bounds invalides de ap/b. La nouvelle décision du 9 novembre autorise
[C102/C103b à normaliser lev et ses bornes](normalized-coordinates-20261109.md)
à partir des coefficients existants avec P_REF=101325 Pa.
Voir [les variantes actuelles et la procédure](post-apply-20261106.md).

C074 appelle `fix_hybrid_coefficients(ds, coordinate)`. La recette conserve les
attributs de formule `p = ap + b*ps` et `formula_terms="ap: ap b: b ps: ps"`.
Elle vérifie que `ps` est en Pa et que la totalité de l'axe `lev` correspond à la
référence, dans le même ordre. Le calcul `ap + 101325*b` des 79 centres reproduit
exactement l'empreinte de `lev` dans les rapports individuels de novembre.

Les coefficients proviennent de `hybrid_coefficients_reference.nc` fourni par
l'utilisateur. L'extraction JSON incluse dans le paquet conserve les valeurs
binaires des doubles après relecture, les deux sources et le SHA-256 du NetCDF.
Le fichier contient `ap` et `b` sur 79 centres et sur 80 interfaces. Pour les
bornes des coefficients des centres, C074 associe les interfaces consécutives
dans l'ordre du fichier. Chaque centre est exactement la moyenne des deux
interfaces retenues pour chacun des deux coefficients.

- `ap(lev)` et `b(lev)` prennent les valeurs du groupe correspondant à l'axe.
- `alevel` reçoit `ap_bnds(lev,bnds)` et `b_bnds(lev,bnds)`.
- Les six fichiers `alevhalf` ont aussi 79 niveaux et la même empreinte de
  `lev` que les 56 fichiers `alevel`. Ils reçoivent donc les mêmes coefficients
  sur 79 niveaux, sans ajout de bornes. Le nom `alevhalf` reste un point à
  vérifier dans la production. Un axe déjà sur 80 interfaces et conforme à la
  référence peut utiliser les 80 coefficients sans bornes.
- Les valeurs de `lev` et de ses éventuelles bornes sont conservées. La recette
  ne crée pas `lev_bnds` à partir des coefficients. Si `lev_bnds` existe pour
  `alevel`, sa formule référence les bornes des coefficients corrigés. Un
  avertissement sur les valeurs ou les bornes de `lev` peut donc subsister.

NetCDF permet de renommer une variable, mais ses dimensions sont fixées à la
création. Selon la décision du 9 novembre, la recette ne conserve plus de copies
`fix_original_ap`, `fix_original_b` ou de leurs bornes. Elle retire aussi les copies
existantes après validation de la référence et des références CF. Cette suppression
physique et les remplacements de dimensions imposent une réécriture unique regroupée.
Les grands champs scientifiques et lev gardent leurs valeurs, vérifiées en sortie.
Les corrections portant seulement sur les attributs restent sur place lorsqu'aucune
suppression ni modification structurelle n'est requise.

Les anciens plans `fix_hybrid_coordinate_metadata` appliquent encore l'ancienne
recette. Régénérer le plan pour inclure les coefficients. C074 reconnaît aussi les
diagnostics ultérieurs où `ap` ou `b` est encore lié à `klev` ou `klevp1`.

```bash
fix_tool plan "$report" -r rules/surface_and_hybrid.yaml --only-rule C074 \
  --map-path "$source_root=$copie" -o fixplan.C074.json
fix_tool apply fixplan.C074.json --only-under "$copie" --fixlog fixlog.C074.jsonl
```
