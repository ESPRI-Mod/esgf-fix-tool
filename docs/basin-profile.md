# C070 : profils de bassin sur lat(lat)

Les deux messages individuels COORD011 portent sur les mêmes huit fichiers. Ils donnent une
seule opération par fichier. Les huit profils ont x=1, y=332 et le même hash de nav_lat dans
QA_results_v3 : hfbasin, hfbasinpmadv, hfbasinpmdiff, htovgyre, htovovrt, sltbasin,
sltovgyre et sltovovrt.

L'utilisateur rapporte la décision des ingénieurs du modèle : la dernière latitude doit passer
à **89.36999**, à la place du doublon 89.36998 (valeur stockée 89.36997985839844).
La recette `fix_basin_profile_latitude(ds, var)` prépare puis vérifie une réécriture temporaire :

- nav_lat(y,1) devient lat(lat), axe Y ; y est renommé lat.
- La dimension x=1 est retirée par squeeze, sans moyenne ni suppression de ligne.
- Les 331 premières latitudes et les 332 lignes de tous les champs restent inchangées.
- Seule lat[-1] reçoit 89.36999 ; la stricte monotonie est vérifiée avant et après.
- nav_lon est conservé comme tableau auxiliaire non référencé, avec x retiré sans perte.
- Les références CF sont actualisées ; le retrait indépendant de longitude C069 reste idempotent.

Aucune borne latitudinale n'est créée. Les métadonnées de grille, les données scientifiques,
le format, la compression et les attributs globaux sont préservés. Les chunks perdent seulement
leur composante x singleton. Une autre taille de grille, une autre anomalie de latitude, des bornes
latitudinales préexistantes non examinées ou d'autres tableaux spatiaux font refuser le fichier.

Pour tester cette erreur seule : `plan -r rules/horizontal_coordinates.yaml --only-rule C070`,
puis `apply --dry-run`, avec les paramètres de chemins habituels. Retirer --dry-run pour conserver
le changement. Aucun NCO supplémentaire, accès réseau ou nouvelle dépendance n'est requis : le
Python netCDF4 existant du conteneur esgf-toolbox effectue et vérifie la conversion.
