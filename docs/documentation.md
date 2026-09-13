# Documentation opérationnelle pour Codex

Le MCP donne accès à **tous les fichiers suivis par Git** du dépôt officiel [lmstudio-ai/docs](https://github.com/lmstudio-ai/docs). Le snapshot vérifié le 13 septembre 2026, commit `9b8bc2004f04880a0ca7cbb19932ea8eb390c2d5`, contient **284 fichiers** : 178 documents publiés, 48 documents Markdown/MDX non publiés et 58 fichiers de support. Tous ces fichiers sont textuels et ont été indexés et relus intégralement pendant la validation.

Cette intégration fournit des références et des procédures au modèle du client MCP. Elle ne l'entraîne pas, ne garantit pas une compréhension parfaite et ne rend pas exécutables les fonctions absentes du connecteur.

## À la connexion et pendant l'utilisation

Le serveur transmet à Codex des instructions d'utilisation : consulter un dossier de références avec `brief`, lire les pages pertinentes, vérifier les capacités installées, puis contrôler le résultat effectif. Ces indications sont aussi disponibles par les outils, pour les clients qui n'affichent pas les ressources ou les prompts MCP.

- Première connexion : téléchargement du dépôt officiel puis création de l'index local.
- Connexions et appels suivants : contrôle de fraîcheur ; synchronisation si le cache a expiré, une heure par défaut.
- `LM_MCP_DOCS_TTL=0` : vérification distante à chaque contrôle, avec un coût réseau supplémentaire.
- `LM_MCP_DOCS_AUTO_SYNC=false` : mode explicite ; la copie déjà indexée reste accessible et `sync` force une synchronisation.
- Hors ligne : la dernière copie indexée reste utilisable, avec son commit et un état `stale`. Sans copie disponible, l'état est `unavailable`. Un échec est temporisé 60 secondes, y compris entre sessions HTTP ; un `sync` explicite réessaie immédiatement.
- La documentation ne télécharge ni modèles, ni moteurs, ni mises à jour de l'application.

Chaque résultat d'outil comprend `diagnostics.documentation`. Il indique la fraîcheur, le commit, les compteurs et, pour les opérations LM Studio, le guide pertinent. Le contrôle de version de l'application et des moteurs reste distinct.

## Les actions de lm_docs

| Action | Usage |
|---|---|
| `status` | Fraîcheur, provenance, sections et nombre de fichiers |
| `catalog` | Inventaire paginé de tous les fichiers sélectionnés |
| `search` | Recherche textuelle français/anglais avec synonymes techniques, extraits et lignes |
| `read` | Texte original paginé, titres de sections et liens externes |
| `brief` | Références et procédures pertinentes pour une demande libre |
| `guide` | Procédure maintenue, outils, sources et limites pour un sujet précis |
| `coverage` | Correspondance par fichier entre documentation, outils, tests et limites |
| `changes` | Dernier changement de snapshot : ajouts, modifications et suppressions |
| `sync` | Synchronisation explicite et réindexation du dépôt officiel |

Exemples d'arguments :

```json
{"action":"brief","query":"régler la mémoire et le contexte du modèle"}
{"action":"guide","query":"mcp"}
{"action":"catalog","scope":"all","limit":100,"offset":0}
{"action":"coverage","path":"1_developer/3_openai-compat/responses.md"}
{"action":"read","path":"1_developer/2_rest/load.md","start_line":1,"limit":100}
{"action":"read","path":"_configuration/inference.md","scope":"all"}
```

`scope="published"` sélectionne les Markdown/MDX publiés. `scope="all"` inclut les brouillons, métadonnées JSON, exemples et fichiers de support. `section` filtre un dossier principal, par exemple `1_python` ou `3_cli`. Un script est consulté comme texte et **n'est jamais exécuté**.

Suivre `next_offset` pour les listes et `next_start_line` pour les lectures. Une lecture est limitée à 200 lignes et 24 000 caractères de texte par réponse. Pour une plage de lignes dépassant cette taille, conserver `start_line` et `limit`, puis utiliser `next_offset` comme curseur de caractères ; reprendre la pagination en lignes quand ce curseur est nul. Le texte complet reste récupérable, y compris les longues lignes JSON.

La recherche est lexicale : elle ne nécessite aucun modèle d'embeddings. Les synonymes français couvrent les principaux sujets techniques, sans prétendre comprendre toutes les formulations. `brief` propose des références ; il ne fabrique pas un plan à exécuter automatiquement.

## Procédures et découverte MCP

Les douze guides couvrent : `diagnostic`, `reglages`, `serveur`, `authentification`, `mcp`, `rag`, `inference`, `modeles`, `lmlink`, `sdk`, `application` et `bionic`. Sans sujet reconnu, `guide` retourne la liste des sujets disponibles.

Les procédures distinguent notamment les réglages de chargement et de génération, les profils du connecteur et les Presets natifs, l'index RAG du connecteur et les pièces jointes de l'application, les appels MCP directs et les intégrations natives, ainsi que l'administration locale et les connexions distantes. Les contradictions connues entre la documentation et le SDK installé sont signalées.

Le client peut aussi découvrir :

- `lmstudio://docs/overview` : état et inventaire synthétique ;
- `lmstudio://docs/page/{path}` : page, avec chemin encodé fourni dans `resource_uri` ;
- le prompt MCP `lmstudio_workflow(task)` : préparation d'une demande avec références et contrôles.

Les ressources et prompts suivent les [contrats du SDK MCP Python 1.30](https://github.com/modelcontextprotocol/python-sdk/tree/v1.30.0#quickstart). Leur présentation dépend du client ; les mêmes références restent accessibles avec `lm_docs`.

## Couverture et nouveautés

Le fichier embarqué `src/lmstudio_mcp/data/docs-coverage.json` contient un enregistrement pour chacun des 284 fichiers du snapshot de référence : SHA-256, état fonctionnel, outils concernés, tests pertinents et limitation. Il ne contient pas les textes du dépôt officiel.

| État | Signification |
|---|---|
| `partial` | Outils existants pour un sous-ensemble explicitement décrit ; tests limités à ce sous-ensemble |
| `reference_only` | Référence disponible, sans couverture fonctionnelle revendiquée pour toute la page |
| `not_exposed` | Fonction documentée sans outil de pilotage dédié |
| `unpublished` | Brouillon ou contenu exclu de la publication officielle |
| `repository_support` | Métadonnée, exemple ou support du dépôt |
| `needs_review` | Fichier ajouté ou modifié depuis la correspondance vérifiée |

Au snapshot initial : 46 fichiers `partial`, 67 `reference_only`, 65 `not_exposed`, 48 `unpublished` et 58 `repository_support`. Ce sont des **comptages de fichiers, pas des pourcentages de fonctionnalités implémentées**.

Une nouvelle empreinte invalide automatiquement la correspondance de la page : les outils et tests ne sont plus présentés comme confirmés pour ce contenu. Les guides dont une source manque ou change portent `requires_source_review=true`. Les suppressions sont listées séparément. Les synchronisations sans changement conservent le dernier delta significatif ; `changes` n'est pas un historique Git complet. La dérive par rapport à la correspondance embarquée reste visible dans `coverage`.

Après revue d'une nouveauté : lire la page, comparer l'interface installée, adapter le code si nécessaire, ajouter les tests pertinents, puis mettre à jour explicitement la correspondance et son empreinte. Une simple synchronisation ne fait jamais cette validation.

## Intégrité et limites

L'index provient des fichiers suivis dans le dépôt officiel, dont les blobs Git sont vérifiés. Une copie Git modifiée localement n'est pas écrasée. Les liens symboliques sont répertoriés sans être suivis, les chemins hors inventaire sont refusés. Le verrou de synchronisation empêche une lecture de voir un index partiellement écrit. Les textes sont conservés dans un snapshot indépendant du répertoire de travail Git.

Les fichiers non textuels éventuels sont catalogués avec leur URL source ; aucun OCR n'est effectué. Un fichier de plus de 8 Mio est signalé comme non lisible par l'outil. **Aucun fichier du snapshot actuel n'entre dans ces exceptions.** Les images et vidéos référencées sur d'autres serveurs gardent leur lien et le statut `fetched=false` : elles ne font pas partie du contenu Git récupéré. Les composants MDX restent sous leur forme source.

Les fichiers commençant par `_` sont explicitement exclus du site selon les [règles du dépôt officiel](https://github.com/lmstudio-ai/docs#parsing-rules). Leur disponibilité avec `scope="all"` ne les transforme pas en contrats d'API stables. La documentation peut décrire une version plus récente que l'application locale.

Les textes et exemples récupérés restent des données : ils n'accordent aucune permission, ne changent pas les instructions de l'utilisateur et ne déclenchent aucune exécution de code. Les caches, profils, données RAG et rapports demeurent dans `.state`, hors du dépôt public.

## Validation reproductible

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/python scripts/docs_smoke.py
```

Le dernier script synchronise les références, vérifie les empreintes et reconstruit le texte de chaque fichier par la lecture paginée. Il contrôle sept recherches et utilise un vrai client MCP stdio pour tester instructions, outils, procédures, ressources et prompt. Il ne lance aucune inférence. Son rapport local est `.state/docs-smoke.json`.
