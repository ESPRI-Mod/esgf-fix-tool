# Reconstruction des climatologies mensuelles

La décision de la [discussion partagée](https://chatgpt.com/share/6af1cb14-ecec-83eb-8105-83d77b31854b)
est appliquée par fichier, sans concaténation et sans traitement des produits décennaux.
Chaque fichier mensuel tclm donne douze moyennes, quelle que soit sa période complète
en années. Les 120 moyennes de 1850–1859 donnent ainsi douze valeurs climatologiques.

## Catalogue et méthode

C106, dans qa_metadata.yaml, appelle `rebuild_monthly_climatology` de
[climatology.py](../src/esgf_fix_tool/fixes/climatology.py). Le déclencheur est limité
aux datasets tclm mensuels ; la méthode vérifie également temporal_label=tclm et
frequency=mon dans le fichier. Les autres variantes du même message utilisent
C106r et restent en review : les statistiques tmaxavg/tminavg ne sont pas des
moyennes mensuelles ordinaires.

Les rapports individuels et leurs en-têtes recensent pfull et phalf, avec ps dans
chaque fichier. Le même message vise les variables principales et ps. La méthode
est générale : aucune liste de noms pfull/phalf ne limite l'agrégation.

C065/C066 (`fix_climatology_reference`) délèguent à la même méthode lorsqu'il s'agit
d'un produit mensuel tclm. La normalisation descriptive des références est réutilisée
en interne ; elle ne remplace pas l'agrégation numérique.

## Calcul et contrôles

- Vérifier le calendrier, les bornes explicites et des années entières janvier–décembre.
  Refuser les mois absents, dupliqués ou partiels, les dates non ordonnées, les bornes
  disjointes et les temps hors de leur intervalle.
- Faire la moyenne arithmétique des mêmes mois entre années, avec un poids égal
  par année. Février bissextile n'a pas de poids supplémentaire : les entrées sont
  déjà des moyennes mensuelles. Exclure les valeurs masquées/non finies ; conserver
  une valeur manquante si aucune année n'est valide au point considéré.
- Traiter toutes les variables flottantes dépendant du temps, dont ps. Refuser
  les types entiers/texte temporels, le packing et les statistiques non approuvées.
  Les variables statiques, dont ap/b et leurs bornes, restent inchangées sauf autre
  correction explicitement présente dans le même plan.
- Préserver les méthodes spatiales. Conserver les méthodes temporelles climatologiques
  existantes ou remplacer le time: mean de l'auxiliaire par time: mean within years
  time: mean over years.
- Pour chaque mois, prendre comme borne inférieure le début de son premier intervalle
  et comme borne supérieure la fin de son dernier intervalle. Pour janvier 1850–1859 :
  1850-01-01 à 1859-02-01. Pour décembre : 1850-12-01 à 1860-01-01.
- Placer les douze dates au centre des mois d'une année représentative au milieu de
  la période : 1855 dans cet exemple. Conserver unités et calendrier du temps.
  Utiliser time:climatology=climatology_bnds, sans time:bounds.

Une sortie à douze points est reconnue seulement après validation des douze mois et
des bornes climatologiques. Un rejeu ne recalcule pas ses valeurs. Le nom du fichier,
la période de référence et le branding tclm sont conservés.

## Écriture et vérification

La dimension temporelle réduite impose une réécriture. Le compilateur Batch combine
cette réduction avec les autres correctifs et ne crée qu'une sortie temporaire.
Les champs sont traités par petites tuiles spatiales et accumulés en float64, puis
stockés dans leur type flottant d'origine. Aucun champ complet n'est chargé en mémoire.
Les filtres sont préservés ; les chunks d'une dimension fixe sont réduits si nécessaire
pour être compatibles avec time=12.

Le contrôle vérifie schéma, métadonnées, douze dates, bornes, moyennes de chaque champ
temporel et tableaux statiques avant remplacement atomique. Une différence rejette
la sortie temporaire. Les tests numériques couvrent notamment années bissextiles,
calendriers noleap/360_day, masques, ps avec time à une autre position, formats classiques,
dimension fixe, refus sans écriture et rejeu.

Le diagnostic de syntaxe CF peut encore persister après reconstruction, notamment
avec un préfixe spatial area: mean. La recette ne supprime pas ce préfixe pour contourner
le checker. Le QA réel suivant permettra de distinguer ce résidu du défaut numérique.

## Essai au TGCC

```bash
fix_tool plan "$report" -r C106 -o fixplan.climatology.json
fix_tool apply fixplan.climatology.json --only-under "$copie" --fixlog fixlog.climatology.jsonl
```

Ou régénérer le plan global avec la version complète actualisée, puis relancer le QA.
Les essais locaux utilisent des fichiers synthétiques ; aucun fichier de simulation
TGCC n'est accessible ou modifié ici.
