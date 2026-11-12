# Derniers contrôles QA v6 : lev et axis_nbounds

Source : QA_results_v6/qa_result_20261111-0926_72fe283c63c086e694e76b25c34c6f28.json,
rapports individuels et en-têtes consistency-output.

| Cas | Fichiers | Résultat |
| --- | ---: | --- |
| lev dépasse 1 sur ta/zg à six heures | 4 | C102 est bien sélectionné : normaliser avec ap/101325+b. |
| lev:bounds manque sur les mêmes ta/zg | 4 | C103b réutilise la même méthode et ajoute lev_bnds(lev,bnds), ses valeurs et formula_terms. |
| lev:bounds manque sur edt/evu | 2 | C103e : passage approuvé de 80 interfaces à 79 centres, puis bornes de référence. Voir la mise à jour ci-dessous. |
| axis_nbounds vs bnds dans les champs océaniques fixes | 5 | C068b : quatre renommages nécessaires ; hfgeou possède déjà bnds et reste conforme pour cette règle. |

## ta/zg : méthode existante vérifiée

Les quatre axes lev ont 79 valeurs. Leurs empreintes, ainsi que celles des deux
coefficients ap/b, correspondent exactement à la référence fournie. Leurs valeurs
lev sont encore des pressions bien que units="1" soit déclaré. C074 est une
préparation de coefficients et de métadonnées ; il ne remplace pas C102.
Le rapport v6 sélectionne bien C102 et C103b pour les quatre fichiers, chacun
appelant la méthode idempotente normalize_hybrid_coordinate.

Les calculs réutilisés sont :

```python
lev = ap / 101325.0 + b
lev_bnds = ap_bnds / 101325.0 + b_bnds
```

L'attribut lev:bounds est lev_bnds, dont les dimensions sont (lev,bnds), avec :

```text
formula_terms = "ap: ap_bnds b: b_bnds ps: ps"
```

Chaque centre doit appartenir à son intervalle et à [0,1]. Les coefficients,
ps, les données de ta/zg et leur compression sont conservés. Si leurs dimensions
sont déjà alignées, ces changements portent seulement sur les petits axes et les
attributs dans le fichier ouvert : aucun recopiage de la variable principale.
Si un réalignement structurel est encore nécessaire, le compilateur conserve la
réécriture unique déjà prévue. Le rejeu n'effectue aucune écriture.

Les tests couvrent les deux tranches de ta et de zg, C102 seul, C103b seul et les
deux ensemble ; ils interdisent une réécriture du champ principal quand le schéma
est déjà aligné et comparent ses valeurs, sa compression et ses chunks.

## edt/evu : décision révisée après confirmation LMDZ

La décision de review décrite ci-dessous est remplacée par la
[conversion approuvée de 80 interfaces à 79 centres](edt-evu-centering-20261112.md).
C103e et C104 utilisent désormais cette transformation, avant C074/C102 si ceux-ci
sont aussi sélectionnés. Le refus d'inventer des bornes à 80 centres reste actif
quand seule la normalisation générique est demandée.

Avant cette confirmation, l'utilisateur confirmait la cible al et demandait de garder ces deux
cas en review. Les rapports montrent 80 valeurs et ap/b identiques à la référence
fournie pour les 80 interfaces. Le fichier de référence ne fournit des bornes que
pour les 79 centres ; il ne contient pas les 80 paires de bornes de centres.
Aucune paire supplémentaire, extrapolation ou réduction de la variable scientifique
n'est inventée.

Un label explicite al impose maintenant la conservation des bornes éventuelles,
quelle que soit la longueur de lev : 80 valeurs ne suffisent plus à les supprimer
comme s'il s'agissait nécessairement de alh. La préparation numérique refuse une
référence de centres à 80 niveaux qui n'a pas été fournie. Les règles C103e et
C104 restaient en review et C074c n'incluait pas edt/evu, afin de ne pas tenter cette
préparation indirectement par un diagnostic inter-datasets. Le label alh continue
de suivre la politique de bornes facultatives approuvée précédemment.

## axis_nbounds : règle et passage commun

Le v6 montre axis_nbounds=2 dans areacello, basin, deptho et sftof. hfgeou possède
déjà bnds=2. La règle C068b couvre les cinq fichiers du diagnostic de cohérence.

En plus de cette sélection explicite, apply normalise axis_nbounds à la fin de la
préparation de chaque fichier sélectionné, même par un autre correctif :

- Sans bnds existante, renommer la dimension en bnds et conserver son identité.
  Cela permet une modification directe sans réécrire les champs.
- Si bnds existe déjà, fusionner les dimensions de taille 2 et remapper tous les
  tableaux qui utilisent axis_nbounds, dans la réécriture unique du fichier.
- Conserver les tableaux, leur ordre, leur type et leur compression ; préserver
  les véritables dimensions de quatre sommets vertices/nvertex.
- Refuser avant écriture une taille différente de 2, une dimension illimitée ou
  une collision de variables de coordonnées ambiguë.

Le compilateur reçoit désormais la fusion depuis la méthode partagée elle-même,
ce qui permet ce passage commun sans ajouter artificiellement une règle au plan.
Un ancien C068 peut devenir « déjà conforme » lorsqu'un correctif précédent a
normalisé axis_nbounds. Un contrôle après écriture vérifie son absence.

## Lancement

Transférer l'outil complet et régénérer le plan depuis le rapport complet v6 :

```bash
report="../QA_results_v6/qa_result_20261111-0926_72fe283c63c086e694e76b25c34c6f28.json"
fix_tool plan "$report" -o fixplan.v6.json
fix_tool apply fixplan.v6.json --only-under "$copie" --fixlog fixlog.v6.jsonl
```

Pour isoler les cas demandés, ajouter -r C102 -r C103b -r C068b à plan.
Les tests utilisent des fichiers synthétiques. Aucun NetCDF de simulation n'a été
modifié sur le Mac ; relancer leur QA au TGCC après application.
