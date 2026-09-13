![LM Studio Local MCP — modèles, connecteurs et RAG, bannière vert et crème](assets/banner-lmstudio-mcp.png)

# LM Studio ↔ Codex — MCP 2.0

Serveur MCP indépendant pour administrer LM Studio et utiliser des modèles locaux depuis un client MCP. Il utilise l’API native `/api/v1`, l’API compatible OpenAI, la commande officielle `lms` et le SDK Python officiel. Il expose **28 outils** : administration, inférence, réglages, documentation, connecteurs et RAG. Les versions Python sont verrouillées dans `uv.lock`.

## Installation

Prérequis : Python 3.11 ou supérieur, [uv](https://docs.astral.sh/uv/), Git et LM Studio avec son serveur local activé. **Plateforme LM Studio validée : macOS.** Windows n’est pas pris en charge nativement ; le connecteur utilise des verrous POSIX et des chemins macOS par défaut.

Cloner ce dépôt, ouvrir un terminal dans son dossier, puis :

```bash
uv sync --frozen
cp .env.example .env
./run.sh --diagnose
```

Adapter `.env` uniquement si le port, l’authentification ou les chemins diffèrent. Le serveur LM Studio écoute par défaut sur `http://127.0.0.1:1234`.

Pour Codex CLI, depuis le dossier cloné :

```bash
codex mcp add lmstudio-local -- "$PWD/.venv/bin/python" -m lmstudio_mcp.server
```

Dans les paramètres MCP du client, prévoir 45 secondes pour le démarrage et jusqu’à 1 000 secondes par appel long. Pour les autres clients, adapter le chemin absolu de `examples/mcp-client.json`. L’installation crée un environnement local ; aucun modèle ni moteur n’est téléchargé automatiquement.

## Utilisation dans Codex

Après installation, enregistrer le serveur sous **lmstudio-local** dans le client MCP. Le programme reste dans le dossier cloné ; après un déplacement, adapter le chemin enregistré. Recharger le client si les outils ne sont pas encore visibles.

Exemples de demandes :

- « Vérifie LM Studio et liste mes modèles. »
- « Estime la mémoire nécessaire pour Gemma avant de le charger. »
- « Interroge Gemma avec cette consigne, puis libère son instance. »
- « Indexe ces documents et réponds avec les citations sources. »
- « Diagnostique mon connecteur MCP et vérifie son programme de démarrage. »
- « Enregistre un profil de chargement et compare les réglages effectifs. »
- « Vérifie les mises à jour des moteurs, sans les installer. »

Par défaut, l’inférence s’exécute sur la machine locale. Les résultats demandés par Codex sont ensuite transmis à Codex. Les autres logiciels nécessitent leurs propres connexions autorisées.

## Vérification à chaque utilisation

À l’ouverture d’une connexion MCP et avant chaque outil :

1. Lecture de la version de l’application installée et des moteurs présents.
2. Vérification réelle de `/api/v1/models` et de sa structure.
3. Contrôle de la fraîcheur des informations officielles : changelog et pages API, chargement, conversation. Cache d’une heure ; `lm_status(refresh_updates=true)` force la lecture distante. `LM_MCP_UPDATE_TTL=0` vérifie en ligne à chaque appel.
4. Comparaison de l’empreinte application/moteurs et du code/dépendances du connecteur avec le dernier test réel. Un changement invalide la validation et demande un nouveau test.
5. Contrôle des capacités utiles avant l’action, puis vérification de l’instance après chargement/déchargement. Les paramètres de chargement non appliqués produisent une erreur MCP avec l’état réellement obtenu.

Chaque réponse comprend `ok`, `data` ou `error`, et `diagnostics`. Une erreur réseau ne devient jamais une déclaration « à jour ». Une mutation en timeout a un résultat inconnu : consulter l’état avant de la répéter. Les POST ne sont pas réessayés automatiquement.

**Une détection de version ne prouve pas que toutes ses nouveautés fonctionnent.** Les nouveautés inconnues nécessitent une adaptation du code et les tests correspondants. Les changements de documentation restent signalés dans le cache jusqu’à revue. Aucun téléchargement de mise à jour logicielle n’est lancé à la connexion.

## Outils

| Outil | Usage |
|---|---|
| `lm_status` | Version, API, moteurs, dernières versions et validation |
| `lm_models` | Modèles, variantes, instances, capacités et configurations |
| `lm_load` | Chargement natif : contexte et options GGUF documentées |
| `lm_load_advanced` | GPU, parallélisme, TTL, décodage spéculatif via `lms` ; `identifier` requis |
| `lm_estimate` | Estimation mémoire sans chargement |
| `lm_unload` | Déchargement d’une instance précise avec vérification |
| `lm_download` | Téléchargement depuis le catalogue/Hugging Face |
| `lm_download_status` | Suivi du job de téléchargement |
| `lm_chat` | Texte, images, paramètres, raisonnement, conversation persistante et statistiques |
| `lm_openai_chat` | Historique explicite, sortie structurée JSON, définitions de fonctions |
| `lm_embeddings` | Vecteurs, en lot ou à l’unité |
| `lm_server_control` | État, démarrage sur loopback, arrêt et redémarrage du serveur |
| `lm_runtime` | Moteurs, matériel, disponibilité, simulation ou installation explicite d’une mise à jour stable |
| `lm_integrations` | MCP configurés, programme disponible, permissions |
| `lm_diagnose` | API, matériel, disque, instances, erreurs et signaux des journaux |
| `lm_connections` | Enregistrer, tester et sélectionner des serveurs LM Studio |
| `lm_docs` | Synchroniser, chercher et citer la documentation officielle |
| `lm_model_config` | Schéma SDK installé, inspection et chargement avancé |
| `lm_profiles` | Enregistrer et réutiliser des configurations de modèles |
| `lm_mcp_config` | Prévisualiser/appliquer une configuration avec sauvegarde et contrôle de conflit |
| `lm_mcp_probe` | Connexion réelle et découverte des outils d’un MCP |
| `lm_mcp_call` | Appel d’un outil MCP autorisé avec validation de ses arguments |
| `lm_rag_index` | Index local de TXT, MD, PDF texte et DOCX sélectionnés |
| `lm_rag_search` | Recherche avec extraits, chemins, pages et empreintes |
| `lm_rag_ask` | Réponse locale avec contrôle des identifiants et citations exactes |
| `lm_rag_manage` | Liste des collections ou suppression d’un index |
| `lm_link` | État et administration LM Link exposés par le CLI installé |
| `lm_import_model` | Simulation/import par copie d’un GGUF local |

`lm_chat` prend un texte ou une liste `{"type":"text","content":"..."}` / `{"type":"image","data_url":"data:image/png;base64,..."}`. Avec `options.store=true`, transmettre le `response_id` reçu dans `options.previous_response_id` pour continuer. Répéter le `system_prompt` souhaité. Les valeurs de raisonnement sont contrôlées contre celles du modèle.

`lm_openai_chat` retourne les appels de fonctions ; il ne les exécute pas. `lm_mcp_call` appelle directement un outil autorisé ; `lm_chat.integrations` permet sa délégation à un modèle local, selon les permissions de LM Studio.

## Réglages et administration

Consulter `lm_model_config(action="schema")` avant de configurer le SDK : contexte, GPU, quantification KV, mmap, RoPE, seed et autres options réellement exposées par la version installée. Les options supplémentaires GGUF sont refusées sur MLX. Utiliser `lm_load_advanced` pour les flags CLI disponibles : parallélisme, TTL, offload GPU, assistant de décodage.

`lm_profiles` stocke les paramètres dans ce dossier. Un chargement SDK exige un identifiant d’instance neuf ; l’inspection ne charge jamais implicitement un modèle. Les paramètres ignorés produisent une erreur avec l’instance réellement créée, permettant de la décharger.

`lm_diagnose` fournit des pistes de réparation fondées sur les erreurs, sans renvoyer les lignes brutes des journaux. Ces pistes ne sont pas une preuve de cause. Il vérifie aussi l’existence du programme des connecteurs configurés. `lm_server_control` pilote le serveur local ; `lm_runtime` gère les moteurs. L’installation de l’application et la connexion au compte LM Link restent dans les interfaces officielles.

## Connecteurs MCP

1. Lister les entrées via `lm_mcp_config(action="list")`.
2. Prévisualiser une modification (`upsert`, `remove`, `authorize`) avec `apply=false`.
3. Appliquer en reprenant `expected_digest` de cette prévisualisation : le MCP conserve les autres entrées et crée une sauvegarde.
4. Utiliser `lm_mcp_probe` pour établir la connexion et découvrir les outils réels.
5. Autoriser leurs noms exacts avec `allowed_tools`, puis appeler `lm_mcp_call`.

La prévisualisation et l’application peuvent être enchaînées par Codex dans la demande autorisée. Le contrôle de conflit évite d’écraser une modification concurrente. Changer une configuration invalide ses anciennes permissions. Aucune commande shell générique n’est proposée ; un connecteur stdio exécute néanmoins le programme configuré lors de sa connexion. Une configuration réussie dans `mcp.json` ne prouve pas son activation dans l’interface LM Studio, qui peut nécessiter un rechargement.

Les transports stdio, HTTP Streamable et SSE sont pris en charge. Les commandes, arguments et secrets complets ne sont pas retournés par l’inventaire. Les sauvegardes conservent la configuration originale et doivent rester privées.

## RAG documentaire

Déposer les documents dans `documents/`, puis utiliser `lm_rag_index` avec une liste explicite de fichiers et le modèle d’embeddings. Une collection conserve textes, vecteurs, fichiers, pages PDF et empreintes SHA-256 dans `.state/rag.sqlite3`.

`lm_rag_search` retourne les extraits ; `lm_rag_ask` interroge le modèle et vérifie les identifiants de sources et citations exactes. Une réponse sans citations vérifiables est remplacée par `answer=null`. Le format JSON est imposé ; les repères de sources manquants sont ajoutés après validation des citations structurées. **Ces contrôles ne prouvent pas que toutes les conclusions du modèle sont exactes.** Les documents sont des données, jamais des instructions exécutables.

Les fichiers modifiés, supprimés ou sortis des répertoires autorisés sont écartés. Changer de modèle d’embeddings, de moteur ou de serveur impose une réindexation complète avec `replace=true`. Les formats acceptés sont TXT/MD UTF-8, PDF texte et DOCX avec tableaux ; les PDF scannés nécessitent un OCR préalable. Limites : 25 Mo/fichier, 50 fichiers/appel, 500 pages/PDF, 2 500 fragments/appel, 10 000/collection.

Cet index appartient au connecteur ; il ne pilote pas les pièces jointes ni les index internes des conversations de l’application LM Studio. Les embeddings et questions utilisent **le serveur sélectionné** : choisir un profil distant lui transmet les extraits nécessaires. `lm_rag_manage(action="delete")` supprime l’index, jamais les documents sources.

## Serveurs et documentation officielle

`lm_connections` conserve des profils local, réseau privé ou HTTPS, teste `/api/v1/models`, puis sélectionne la connexion. Les jetons restent dans des variables `LM_REMOTE_*` ou `LM_STUDIO_*` référencées par le profil. Un profil distant désactive les commandes CLI locales ; il n’administre pas le système d’exploitation distant. Les opérations de retour au profil local restent accessibles même si la connexion sélectionnée est défaillante.

`lm_docs(action="sync")` synchronise uniquement [le dépôt officiel de documentation](https://github.com/lmstudio-ai/docs), sans installer de logiciel. `search` trouve les pages, `read` fournit leurs lignes et une URL figée sur le commit, `guide` propose les procédures réglages/diagnostic/MCP/RAG/serveur. Les fichiers masqués ou non publiés sont exclus. La copie locale fonctionne hors ligne ; son commit et sa date de synchronisation sont affichés. Synchroniser avant de travailler sur une nouveauté. La documentation ne remplace pas les tests de l’interface installée.

## Transport et n8n

Codex utilise stdio. `./run.sh --http` fournit également `http://127.0.0.1:8765/mcp` pour les clients locaux, avec les protections Host/Origin du SDK. Il ne publie pas le service sur Internet.

L’exemple `examples/n8n-mcp-workflow.json` est fourni à titre indicatif, désactivé par défaut et sans validation dans une instance n8n.

## Configuration

Copier `.env.example` vers `.env` si nécessaire. Les variables d’environnement du processus ont priorité. Ne pas placer de vrai jeton dans Git.

- `LM_STUDIO_BASE_URL` : origine HTTP locale, par défaut `http://127.0.0.1:1234`.
- `LM_STUDIO_API_TOKEN` : jeton LM Studio si son authentification est activée.
- `LMS_PATH` : chemin du binaire `lms`, par défaut `~/.lmstudio/bin/lms`.
- `LM_MCP_TIMEOUT` : délai API, 300 secondes par défaut.
- `LM_MCP_UPDATE_TTL` : durée du cache officiel en secondes, 3600 par défaut.
- `LM_MCP_STATE_DIR` : cache et preuves, `.state/` dans ce dossier par défaut.
- `LM_MCP_ALLOWED_INTEGRATIONS` : ancienne autorisation globale par ID de plugin ; préférer les permissions exactes via `lm_mcp_config`.
- `LM_MCP_CONFIG_PATH` : configuration MCP de LM Studio, par défaut `~/.lmstudio/mcp.json`.
- `LM_MCP_RAG_ROOTS` : répertoires autorisés séparés par `:` sur macOS, par défaut `documents/`.

Les jetons ne sont pas envoyés aux sites de vérification des versions. Le dossier `.state` contient notamment les textes/vecteurs du RAG, les sauvegardes de configuration, les profils et les preuves de test. Il reste privé et hors Git. `store=true` permet cependant à LM Studio de conserver les échanges côté serveur.

## Validation et maintenance

```bash
./run.sh --diagnose
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/python scripts/smoke_test.py
.venv/bin/python scripts/operations_smoke.py
.venv/bin/python scripts/server_smoke.py
# Ajout facultatif du test de redémarrage LM Studio :
.venv/bin/python scripts/server_smoke.py --restart
# Serveur HTTP lancé dans un autre terminal :
.venv/bin/python scripts/smoke_test.py --http http://127.0.0.1:8765/mcp
```

Le test réel utilise les deux modèles déjà téléchargés, effectue des inférences synthétiques puis libère uniquement les instances qu’il a créées. Il peut consommer plusieurs minutes et environ 7 Go pour Gemma. Il faut le relancer après changement d’application ou de moteurs ; ses limites détectées restent dans le rapport `.state/smoke-stdio.json`.

Pour une nouvelle installation : `uv sync --frozen`. Pour mettre à jour les dépendances du connecteur : `uv lock --upgrade`, `uv sync --frozen`, puis tous les tests. La mise à jour de l’application LM Studio se fait via son interface officielle ; `lm_runtime(action="update")` concerne seulement les moteurs stables et nécessite un nouveau test d’inférence.

Voir [les résultats et limites](docs/verification.md) pour les résultats et limites constatés. `operations_smoke.py` utilise une configuration MCP isolée et un document fictif ; il supprime les instances, profils et index créés pour le test.

## Sources

- https://lmstudio.ai/docs/developer/rest
- https://lmstudio.ai/docs/developer/rest/load
- https://lmstudio.ai/docs/developer/rest/chat
- https://lmstudio.ai/docs/developer/rest/download-status
- https://lmstudio.ai/docs/cli
- https://lmstudio.ai/changelog/lmstudio
- https://github.com/lmstudio-ai/docs
- https://lmstudio.ai/docs/python
- https://lmstudio.ai/docs/app/mcp

API et aide CLI locales inspectées le 13 septembre 2026. Les API stables exposées ici ne donnent pas accès à tous les réglages internes de l’interface, ni à l’ensemble des fonctionnalités de Bionic. Les possibilités futures sont détectées, mais leur implémentation reste un travail explicite.
