# Décisions de correction du 9 novembre 2026

Les recettes ci-dessous utilisent les rapports individuels de QA_results_v3_fixed
et les nouvelles définitions fournies par l'utilisateur. Les valeurs physiques
ne sont pas reconstituées lorsqu'elles ne satisfont pas ces définitions.

| Recettes réutilisées | Fichiers du QA actuel | Traitement |
| --- | ---: | --- |
| C060 | 36 | Supprimer physiquement time_bounds ou time_bnds des axes instantanés, après vérification de toutes les paires [time,time]. Retirer la référence bounds de time si elle existe. |
| C050/C111 | 2 | Vérifier chaque pas de temps de zostoga, conserver une valeur valide identique et supprimer les variables/axes horizontaux. Garder time et ses vraies bornes d'intervalle. |
| C074/C074a | 17 + 56 | Garder les coefficients de référence et les valeurs lev. Ne plus créer de fix_original_ap/b ni de copies de leurs bornes. Retirer les anciennes copies non référencées. |
| C022/C052/C053 | 7 | Renommer solay en depth, garder les 11 profondeurs, mettre m/depth/Z/down et créer depth_bnds selon les milieux des noeuds fournis. |
| C025 | 1 | Pour msfty, garder les 332 valeurs de nav_lat ou latitude, les exposer sur rlat(rlat), avec grid_latitude/degrees/Y. Retirer seulement nav_lon ou longitude entièrement nul. Compléter lev avec depth/Z, et renommer olevel_bounds en lev_bnds. |
| C032/C078/C096/C101a | 9 | Ajouter sector(basin,strlen=21), avec les trois labels approuvés. Conserver basin(basin) et l'ordre des tranches scientifiques. |

Les comptes peuvent se recouvrir : msfty est aussi un des neuf fichiers avec sector.
La table 0–10 du masque basin 2D reste indépendante (C101).

## Contrôles avant écriture

Pour zostoga, la méthode lit une tranche horizontale à la fois. Après exclusion
des points manquants et non finis, min et max doivent être strictement égaux à
chaque date. Une date sans valeur valide interrompt la préparation. Aucune moyenne
ni tolérance spatiale n'est appliquée. Les valeurs stockées sont conservées, y
compris pour un champ compacté avec scale_factor/add_offset. Aucune variable
scientifique spatiale supplémentaire n'est supprimée sans validation.

Pour solay, les valeurs existantes doivent être finies, strictement croissantes,
au nombre de 11, avec un premier noeud proche de 0 et un dernier proche de 2.
Les unités incorrectes 1 sont remplacées par m sans modifier les valeurs. Les
limites internes sont les moyennes des noeuds consécutifs ; les extrémités restent
les premier et dernier noeuds. Une liste d'indices 1..11 est refusée.

Pour les trois bassins, les codes doivent contenir une fois chacun 1,2,3. La
correspondance explicitement approuvée est :

| Code | Label final |
| --- | --- |
| 1 | global_ocean |
| 2 | atlantic_arctic_ocean |
| 3 | indian_pacific_ocean |

sector a standard_name=region et long_name=Ocean Basin. Les chaînes sont remplies
par des espaces. Selon la dernière décision utilisateur, la variable numérique
basin est conservée avec ses valeurs, ainsi que la dimension basin et l'ordre des
données. Elle devient un index (units=1, long_name=Ocean Basin Index) : retrait de
standard_name=region et des flag_* sur cette coordonnée, puisque les noms de régions
sont portés par sector. coordinates du champ devient sector. cell_methods reste
inchangé, y compris le mot basin dans ses commentaires. Le contrôle des références
cell_methods ne considère que les noms suivis de deux-points hors des parenthèses.
Les sorties anciennes où basin a déjà été supprimé restent rejouables ; ce correctif
ne recrée pas une variable absente.

## Coût des suppressions physiques

NetCDF ne permet pas de supprimer une variable d'un fichier ouvert. Ces demandes
nécessitent donc une réécriture, y compris pour un gros fichier 6hr dont seuls des
tableaux auxiliaires sont retirés. Toutes les transformations d'un fichier sont
regroupées en une seule sortie temporaire, vérifiée avant remplacement atomique.
Les grands champs restent inchangés dans le résultat. Il n'y a pas de copie
préliminaire du fichier, ni de réécriture par recette. Le second passage ne
réécrit pas un fichier déjà conforme.

Les simples renommages solay/depth avec ajout de bornes et les changements
d'attributs restent sur place si aucune autre recette du fichier n'impose une
suppression ou une restructuration. L'outil conserve les autres variables.

## Utilisation

Régénérer le plan avec le JSON complet du QA (pas le cluster) et les mêmes
catalogues, en gardant rules/post_apply.yaml en premier. La procédure complète
reste dans [post-apply-20261106.md](post-apply-20261106.md).

Pour isoler un test, utiliser --only-rule C060, C050, C074a, C022, C025 ou C032
sur la commande plan, puis apply sur le nouveau plan. dry-run prépare toutes les
validations sans modifier les NetCDF. Les chemins du QA actuel pointent déjà vers
la copie du TGCC. Relancer le QA/QC après application sur les fichiers réels.
