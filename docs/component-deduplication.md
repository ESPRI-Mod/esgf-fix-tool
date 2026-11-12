# Dédoublonnage ATM / SRF

Cette opération suit la décision scientifique fournie pour la simulation IPSL.
Elle ne dépend ni d'un diagnostic QA/QC ni d'une règle YAML.

| Produit | Conservé | Supprimé si son homologue existe |
| --- | --- | --- |
| areacella, hxy-u | ATM | SRF |
| evspsbl, hxy-u | ATM | SRF |
| snc, hxy-lnd | SRF | ATM |

Depuis le dossier de l'outil, avec `copie` désignant le dossier de simulation :

```bash
fix_tool deduplicate "$copie" --dry-run --fixlog deduplicate.preview.jsonl
fix_tool deduplicate "$copie" --fixlog deduplicate.jsonl
```

La seconde commande supprime effectivement les fichiers secondaires validés.
Le programme conserve intégralement les fichiers préférés, sans réécriture.
Le rejeu ne supprime rien de plus lorsque les doublons sont déjà retirés.
Ces commandes sont à lancer sur la simulation ; aucune suppression de données
de simulation n'a été exécutée depuis le Mac lors de l'ajout de cette fonction.

L'outil recherche récursivement les fichiers .nc sous le chemin fourni. Les deux
chemins doivent être identiques, à la substitution du dossier ATM/SRF près.
Le nom complet, incluant le produit, la fréquence, la grille et la période, doit
être identique. Les identifiants NetCDF source_id, experiment_id, variant_label,
branded_variable, frequency, region et grid_label sont vérifiés ; la coordonnée
temporelle doit être identique pour les produits temporels.

Les valeurs des matrices géophysiques ne sont pas comparées : les produits
des deux composantes ne sont pas nécessairement numériquement équivalents.
Seules les familles explicitement listées sont traitées, toutes périodes confondues.
Un evspsbl hxy-lnd ou un snc hxy-u est conservé.

Sans homologue préféré, le fichier secondaire reste présent (`missing_keeper`).
Un fichier illisible, un désaccord d'identité/couverture temporelle ou un lien
symbolique provoque un `failed` pour la paire, sans suppression de celle-ci.
Les autres paires sont traitées et le code de sortie vaut 1 si une paire a échoué.
Le journal JSONL trace les deux chemins, la validation et l'issue de l'opération.

Cette commande utilise les dossiers de composantes ATM/SRF du dossier de
simulation, par exemple CMIP7/ATM et CMIP7/SRF. Elle ne déduit pas une composante
depuis un chemin de publication MIP-DRS7 qui ne contient plus cette information.
Lancer le dédoublonnage avant de régénérer le plan QA ou de publier les fichiers,
afin que les étapes suivantes ne référencent pas les copies retirées.
