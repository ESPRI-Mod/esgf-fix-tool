# Corrections approuvées après QA v4

Source : QA_results_v4/qa_result_20261110-0946_d34f748ccff6dbe539c21af6fbe15340.json,
son cluster, les rapports individuels et leurs snapshots consistency-output.
Le tableau Excel du 10 novembre sert de référence ; il n'est pas modifié par ce lot.

| Diagnostic | Fichiers | Traitement |
| --- | ---: | --- |
| rlat non monotone de msfty | 1 | C025 : corriger uniquement le doublon terminal avec nextafter dans le type stocké. |
| Noms descriptifs manquants sur bounds_nav_lat/lon | 15, deux variantes par fichier | C098 : supprimer les coordonnées et bornes horizontales réellement orphelines. |
| Standard name descriptif de klevp1 | 2 | C004 existant : retirer uniquement la valeur exacte number of layer interfaces. |
| Standard name bounds de bnds | 2 | C011 existant : retirer l'attribut exact, préserver les données et références. |
| formula_terms sur lev_bnds, CF Appendix A | 56 | C107a : waive, conserver l'attribut. |
| units manquant sur le masque basin 2D | 1 | C101 : ajouter units=1, conserver l'entier, les codes, les points manquants et flag_*. |

## msfty

Le snapshot indique rlat(332) en float64. La correction conserve les 331 premiers
points. Si l'axe est déjà strictement monotone, aucune valeur n'est réécrite. Sinon,
seul un axe croissant avec un dernier point exactement égal à l'avant-dernier est
accepté. Le dernier point devient le prochain flottant représentable vers +inf.
La sortie doit rester finie, strictement croissante et ne pas dépasser 90 degrés.
Les autres défauts de monotonie sont refusés avant écriture. Le champ msfty, ses
valeurs, son ordre et les autres coordonnées sont préservés. Aucun calcul de bornes
rlat n'est inventé : le diagnostic distinct de bornes manquantes reste à investiguer.

## Coordonnées orphelines des sorties hm

Les quinze fichiers concernent bigthetao, masso, so, sos, thetao, tos, volo, ainsi
que siarea, siextent, sisnmass et sivol pour chacun des deux hémisphères. Leurs
snapshots montrent x=y=1, des coordonnées horizontales non attachées au champ et
des tableaux bounds_nav_lat/lon inutilisés. Les deux variantes déclenchent la même
recette C098 ; il ne s'agit pas d'ajouter des descriptions à ces tableaux.

La méthode vérifie le branding hm, le modèle, la grille, les dimensions et toutes
les références CF conservées avant toute suppression. Les variables nav_lat,
nav_lon et leurs bornes sont retirées ensemble, avec leurs alias canoniques s'ils
sont présents et réellement orphelins. Une variable spatiale non singleton ou une
référence active bloque la suppression. Les dimensions encore utilisées par le
champ sont conservées ; les dimensions devenues inutiles sont supprimées. Le champ
n'est ni moyenné ni réordonné. Toutes les suppressions partagent une réécriture.
Le comportement historique C098 pour les profils hyb reste un simple retrait de
l'attribut bounds orphelin.

## Métadonnées techniques et bassins

Les deux fichiers encore affectés pour klevp1/bnds sont ta sur 1850–1854 et zg sur
1855–1859, tous deux en tpt-al à six heures. Le nouveau QA fournit toujours les
valeurs exactes déjà traitées par C004/C011. Les tests confirment que ces méthodes
les retirent et sont rejouables. Une erreur sur un autre correctif du même fichier
peut empêcher l'installation de la sortie regroupée : préparer un correctif ne
signifie pas qu'il a été appliqué physiquement.

La décision units=1 sur le masque basin.ti-u-hxy-u est maintenant explicite.
C007 ne peut plus retirer cet attribut du masque 2D, même dans un ancien plan ou
un ordre inversé. Une éventuelle conversion CF vers None est classée waive (C101w).
La table 0–10 et le traitement séparé des coordonnées basin 1D ne changent pas.
Les autres avertissements concernant region ne sont pas automatiquement effacés.

C107a ignore uniquement le diagnostic Appendix A sur lev_bnds:formula_terms.
Le texte des formules reste inchangé. D'autres messages sur ces formules ne sont
pas assimilés automatiquement à cette exception.

## Lancement

```bash
report="../QA_results_v4/qa_result_20261110-0946_d34f748ccff6dbe539c21af6fbe15340.json"
fix_tool plan "$report" -o fixplan.v4.json
fix_tool apply fixplan.v4.json --only-under "$copie" --fixlog fixlog.v4.jsonl
```

Pour isoler les corrections de ce lot : ajouter à plan
`-r C025 -r C098 -r C004 -r C011 -r C101`. Les waivers n'ajoutent aucune étape
d'écriture. Transférer tout l'outil mis à jour et régénérer le plan depuis le JSON
complet. Les NetCDF réels n'ont pas été modifiés sur ce Mac ; relancer leur QA au TGCC.
