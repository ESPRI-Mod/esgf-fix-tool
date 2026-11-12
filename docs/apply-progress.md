# Appliquer les correctifs au TGCC

Le plan existant reste utilisable : les recettes et leurs paramètres ne changent pas.
Après transfert du dépôt actualisé, depuis son dossier :

```bash
fix_tool() {
  esgf-toolbox env PYTHONPATH="$PWD/src" python -u -m esgf_fix_tool.cli.main "$@"
}

copie="/ccc/work/cont003/cmip7/levavasg/CM6-ESMCO2-FT-esm-pi-s.01"

# Facultatif : vérifier les préconditions sans écrire de fichier.
fix_tool apply fixplan.all.json --only-under "$copie" \
  --dry-run --fixlog fixlog.all.dry-run.jsonl

# Appliquer puis relancer le QA/QC sur la copie.
fix_tool apply fixplan.all.json --only-under "$copie" --fixlog fixlog.all.jsonl
```

Aucun accès Internet, uv, ni dépendance supplémentaire n'est nécessaire si le Python du
conteneur fournit déjà netCDF4, PyYAML et Typer. NCO local reste utilisé pour une permutation
isolée compatible quand ncpdq est visible dans cet environnement. Une transformation composée
utilise Python/netCDF4 afin de tout réaliser en une seule passe.

## Écritures réduites

C001 nettoie maintenant history sur toutes les variables du fichier sélectionné,
y compris ps, les coefficients et les auxiliaires absents du diagnostic initial.
La suppression ne touche jamais history au niveau global. Une seconde passe après
la préparation des autres recettes couvre les variables créées ou remplacées.
Elle est incluse dans l'écriture directe ou dans l'unique réécriture déjà prévue.
Le contrôle final refuse une sortie qui conserverait un history de variable.
Les anciens plans contenant une suppression de history bénéficient aussi de cette
passe ; pour inclure les nouveaux fichiers signalés, régénérer le plan depuis le
JSON complet du nouveau QA.

Toutes les recettes d'un fichier passent d'abord dans une vue des en-têtes en mémoire. Les
valeurs sont lues à la demande ; les grandes variables scientifiques ne sont pas chargées.

| Situation | Opération physique | rewrite_count |
|---|---|---|
| Attributs, renommages de variables/dimensions, petits auxiliaires | Modification directe du fichier ouvert ; même inode | 0 |
| Plusieurs permutations, suppressions d'axes singleton ou fusions de dimensions | Une sortie regroupant tous les correctifs, y compris les attributs | 1 |
| Fichier déjà conforme | Lecture seule | 0 |
| --dry-run | Préconditions en mémoire uniquement | 0 |

Il n'y a plus de copie complète préalable. Les contrôles directs vérifient les en-têtes et les
auxiliaires concernés, sans parcourir les grandes données scientifiques. Les coordonnées
renommées sont sauvegardées et restaurées seulement si nécessaire pour contourner le défaut
NetCDF-4 reproduit dans les tests. Une réécriture structurelle vérifie chaque tableau face à la
source, avec sa permutation/suppression d'axe explicite ; format, compression, packing et types
d'attributs sont conservés. Les lectures se font par blocs contigus bornés en mémoire.

C059 écrit uniquement `lon` et ses bornes dans le fichier ouvert quand aucun correctif du
même fichier ne change sa structure. Le journal indique alors `write_mode: in-place` et
`rewrite_count: 0`. Lors d'une réécriture Python, l'empreinte des blocs numériques est
calculée pendant la copie. La vérification relit la sortie et compare cette empreinte.
Le chemin NCO conserve sa comparaison avec la source.

Sur un fichier NetCDF-4 qui possède déjà une coordonnée `bnds`, C068 peut remplacer
uniquement les petits tableaux de bornes reconnus. Les anciens sont conservés sous
des noms `fix_original_*` et les dimensions d'origine restent dans le fichier.
Les coordonnées pointent vers les tableaux canoniques. Depuis la décision du 9 novembre,
C060 supprime physiquement les bornes instantanées dégénérées et C074 les anciennes
copies des coefficients hybrides. Ces suppressions imposent une réécriture unique
regroupée, même pour un gros fichier. Si un autre tableau utilise
une des dimensions à fusionner, la réécriture groupée reste nécessaire.

La copie doit être indépendante : un fichier comportant plusieurs liens physiques est refusé
avant toute modification directe, afin de ne pas toucher l'original. Une réécriture structurelle
peut remplacer un de ces liens sans changer les autres. Une copie par cp -al ne convient donc
pas au mode direct. Sur les formats NetCDF classiques, la bibliothèque peut déplacer des données
en interne lors de l'agrandissement de l'en-tête ; l'outil ne recopie pas lui-même ces fichiers.

Le mode direct n'est pas atomique : une interruption peut laisser certains attributs déjà
modifiés. Les recettes se rejouent et les fichiers conformes ne sont plus écrits. Le mode avec
sortie structurelle conserve le fichier cible jusqu'à validation, puis le remplace. En simulation,
aucune sortie physique n'est créée ni validée : refaire le QA/QC après l'application réelle.

## Suivre l'exécution

Chaque fichier est annoncé avant traitement. Chaque correctif affiche ID, titre, paramètres,
**En cours**, puis **Préparé — vérifications en attente**, puis **Done** après succès de
l'ensemble du fichier. En simulation, le dernier statut est **Done (simulation)**.

Il n'y a aucun message périodique ni thread de minuterie. Les phases et compteurs affichés
correspondent aux opérations réelles. La console reste silencieuse pendant un appel bloquant
à la bibliothèque ou à NCO.

```bash
tail -f fixlog.all.events.jsonl
```

Ce journal reçoit immédiatement les événements, erreurs et interruptions. Le fichier habituel
fixlog.all.jsonl conserve une ligne par fichier terminé, avec durées, write_mode
(in-place / rewrite-once / none), rewrite_count et verification_scope. Ce dernier distingue
les contrôles directs (metadata-and-edited-auxiliaries), la vérification des sorties structurelles
(full-mapped-arrays), le contrôle sans écriture (no-write-preflight) et la simulation
(preflight-only). --quiet masque la console et conserve les journaux.

C074 lit les petits tableaux de coefficients de la référence incluse dans le paquet.
Voir [le correctif hybride](hybrid-coordinate.md) pour son périmètre.
# File errors and continuation

The `apply` command continues after ordinary per-file exceptions by default.
It prints the full traceback, flushes a failed-file record to the JSONL fixlog,
then processes the next entry. No extra option is required. `--quiet` hides routine
progress but keeps errors and tracebacks visible. Use `--fail-fast` to restore
immediate stopping. Ctrl-C and SystemExit still stop execution.

The final counts distinguish `changed`, `unchanged` and `failed`. `files` counts
attempted entries, including failures. An errored file is displayed as `failed`,
never `nothing` or `Done`. If any file failed, the command exits with status 1
after printing the final summary, even when later files succeeded.

Each failed fixlog record contains `status=failed`, the path, exception type,
message, complete traceback and `planned_steps`. It has no confirmed successful
steps. Its write mode and rewrite count are unknown: direct updates may fail after
some edits, so failure must not imply that a file is unchanged. Structural outputs
are still verified before atomic replacement; no failed temporary output is installed.

Per-file preflight errors and missing inputs can also be reported and skipped.
Plan-wide validation (`--only-under` and conflicting final destinations) stays
blocking before writes. Failure to write the main fixlog also stops processing.

The Python API preserves its existing fail-fast default. Call
`apply_plan(..., continue_on_error=True)` to opt in; its progress callback receives
`changed=None` for a failed file.

## Zostoga and orphan bounds

C050/C111 reduce the replicated field to time only and delete the obsolete
horizontal variables. When selected together, their shared method
`reduce_replicated_zostoga` runs before C098 (`drop_unused_horizontal_bounds`),
including on application of an old or reversed plan. C098 returns without changes
when nav_lat/nav_lon is already absent. It still rejects invalid parameter names
and refuses to detach bounds from a retained active coordinate.
