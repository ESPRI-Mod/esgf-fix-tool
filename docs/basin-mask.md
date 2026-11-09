# Masque de régions basin — C101

Le QA du 6 novembre signale dix fichiers pour l'absence de units sur basin.
La décision utilisateur du 9 novembre distingue deux représentations.

| Cas | Fichiers | Action |
| --- | ---: | --- |
| basin(y,x), Region Selection Index, produit basin_ti-u-hxy-u | 1 | fix C101 |
| basin(basin), 1=Global, 2=Atlantic, 3=Indo-Pacific | 9 | fix sector C032/C101a/C078/C096, voir la décision suivante |

Le masque 2D est stocké en int32, sur les mêmes dimensions y=332, x=362.
Les codes ne sont pas remappés. Toute valeur non manquante doit être un entier
entre 0 et 10, sans arrondi. Un code hors plage ou une valeur fractionnaire
interrompt la préparation avant toute écriture.

```text
basin:standard_name = "region"
basin:long_name = "Region Selection Index"
basin:flag_values = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10  // int32
basin:flag_meanings = "global_land southern_ocean atlantic_ocean pacific_ocean arctic_ocean indian_ocean mediterranean_sea black_sea hudson_bay baltic_sea red_sea"
```

Aucun attribut units n'est ajouté. Le masque de points manquants est conservé.
Lors d'une conversion de type, le fill flottant 1e20 est remplacé par le fill int32
-2147483647 et missing_value est également typé int32 s'il existait. Il ne devient
jamais un code de région. Les autres variables gardent leurs valeurs, leurs
dimensions et leur compression. Le masque conserve aussi sa compression.

NetCDF ne permet pas de changer le type d'une variable existante directement.
La conversion utilise une seule réécriture regroupée, vérifiée avant remplacement
atomique, avec les autres corrections prévues pour ce fichier. Aucun ancien
masque flottant ou variable temporaire n'est conservé dans la sortie.
Si basin est déjà int32, les attributs seuls sont corrigés sur place.
Un deuxième passage ne réécrit pas le fichier conforme.

La décision suivante fournit les trois labels des neuf axes 1D : global_ocean,
atlantic_arctic_ocean, indian_pacific_ocean, dans l'ordre des codes 1,2,3.
Ces axes deviennent [sector(basin,strlen)](reviewed-structures-20261109.md).
Leur table 1–3 reste distincte de la table 0–10 du masque 2D.
Ne pas rejouer un ancien plan de simple suppression des flags.

Le diagnostic CF sur units/region peut subsister selon la décision utilisateur.
Cette correction ne masque aucun message dans les rapports QA bruts et ne crée
pas d'exception globale pour tous les objets portant standard_name=region.

## Tester uniquement cette correction au TGCC

Après transfert de cette version, depuis le dossier de l'outil et avec la fonction
fix_tool habituelle utilisant le Python de esgf-toolbox :

```bash
report="../QA_results_v3_fixed/qa_result_20261106-1802_df22cad6bd5d2d4ff9ce6c70bee55ce8.json"
copie="/ccc/work/cont003/cmip7/levavasg/CM6-ESMCO2-FT-esm-pi-s.01"

fix_tool plan "$report" -r rules/post_apply.yaml --only-rule C101 -o fixplan.C101.json
fix_tool apply fixplan.C101.json --only-under "$copie" --fixlog fixlog.C101.jsonl
```

Ce plan contient une opération pour un fichier. Les deux diagnostics, CF units
et CMIP7 ATTR001 units, donnent la même opération dédupliquée. Le parseur ne
nécessite ni connexion Internet ni nouvelle dépendance. Les essais locaux sont
effectués sur des NetCDF synthétiques ; les codes effectifs du masque réel sont
vérifiés à l'exécution au TGCC.
