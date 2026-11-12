# edt/evu : 80 interfaces vers 79 centres

La confirmation LMDZ transmise par l'utilisateur le 12 novembre 2026 remplace
le précédent statut review de C103e et C104. La méthode approuvée est :

```python
field_center[:, k, :, :] = 0.5 * field_interface[:, k, :, :] + 0.5 * field_interface[:, k + 1, :, :]
```

Le dernier rapport complet QA_results_v6_fixed du 12 novembre à 09:20 sélectionne
C103e sur exactement deux fichiers : edt_tavg-al-hxy-u et evu_tavg-al-hxy-u.
C104 couvre l'ancien diagnostic d'absence de standard_name. Ces deux règles
appellent `center_hybrid_interfaces`, dans `fixes/hybrid_centering.py`.

## Préparation et références

La recette vérifie la source IPSL-CM6-ESMCO2, le produit al, les dimensions
(time,lev,lat,lon), le type flottant non empaqueté, la totalité des 80 valeurs
de lev et les ap/b. Elle accepte les coefficients sur lev ou klevp1, mais exige
leur égalité avec la référence d'interfaces fournie. Toute autre variable dépendant
de lev est refusée : elle nécessiterait une méthode scientifique distincte.

Le fichier hybrid_coefficients_reference.nc est déjà extrait dans le JSON inclus
au paquet. Les 79 ap/b sont lus dans ce groupe de référence, sans extrapolation.
Les ap_bnds/b_bnds sont les paires d'interfaces adjacentes fournies. La recette
réutilise ensuite C074/C102 pour préparer :

```text
lev(lev=79) = ap/101325 + b
lev_bnds(lev,bnds=2) = ap_bnds/101325 + b_bnds
lev:bounds = "lev_bnds"
lev:formula_terms = "ap: ap b: b ps: ps"
lev_bnds:formula_terms = "ap: ap_bnds b: b_bnds ps: ps"
```

Chaque centre doit être dans [0,1] et dans ses bornes. Les coefficients ne sont
plus sur klevp1 ; les indices techniques inutilisés sont retirés par le nettoyage
commun. Les derniers choix de métadonnées hybrides restent applicables, notamment
l'absence de computed_standard_name et de units sur b/b_bnds.

## Données, masques et écriture

Le champ est lu par blocs bornés en mémoire, puis moyenné en double précision et
arrondi une seule fois dans son type initial. Une paire est masquée dès qu'une
des interfaces est masquée. Les valeurs infinies/NaN non masquées sont refusées.
Les attributs scientifiques, leur type de stockage, ps, le temps, ses bornes et
les coordonnées horizontales sont conservés, sous réserve des autres règles
explicitement sélectionnées.

Le moteur conserve zlib, son niveau, shuffle, les checksums et les chunks. Seul
un chunk vertical de 80 dépassant la nouvelle dimension est ramené à 79.
L'interpolation impose une réécriture du champ ; elle est regroupée avec les
autres changements dans une seule sortie temporaire. Le fichier initial reste
accessible jusqu'à validation des données et du schéma, puis remplacement atomique.
Un échec retire la sortie temporaire et conserve l'original.

Les dépendances placent cette conversion avant C074/C074a et C102/C103b lorsque
ces méthodes sont sélectionnées ensemble. Deux diagnostics appelant la recette
ne provoquent pas une double interpolation : un axe à 79 niveaux est simplement
validé et normalisé par la méthode existante. Un rejeu conforme n'écrit rien.

## Test au TGCC

Après transfert de l'outil complet, utiliser le rapport **complet**, non le cluster :

```bash
report="../QA_results_v6_fixed/qa_result_20261112-0920_137cced9a925110e3b9a73fc9ebfe648.json"
fix_tool plan "$report" -r C103e -r C104 -o fixplan.edt-evu.json
fix_tool apply fixplan.edt-evu.json --only-under "$copie" --fixlog fixlog.edt-evu.jsonl
```

`copie` doit désigner le dossier de simulation contenant les chemins du rapport.
La commande plan normale sans -r inclut aussi ces règles. Aucun fichier de la
simulation n'a été modifié sur le Mac. La vérification CF/CMIP7, dont COORD008,
reste à relancer sur les deux vrais fichiers après application au TGCC.
