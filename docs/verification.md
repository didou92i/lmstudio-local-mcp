# Vérification du 13 septembre 2026 — MCP 2.1

## Environnement

- macOS, LM Studio **0.4.24+1**, dernière version publiée vérifiée le 13 septembre.
- Moteurs sélectionnés : **MLX 1.11.0**, **llama.cpp 2.37.0**.
- Python 3.14.3 ; SDK MCP 1.30.0 ; SDK officiel LM Studio 1.5.0 ; pypdf 6.18.1 ; python-docx 1.2.0. Dépendances verrouillées dans `uv.lock`.
- Gemma 4 E4B MLX 4 bits et Nomic Embed Text v1.5 GGUF Q4_K_M déjà installés.
- Documentation officielle synchronisée au commit `9b8bc2004f04880a0ca7cbb19932ea8eb390c2d5` du 8 septembre 2026.

## Résultats confirmés

- **82 tests automatiques réussis**, Ruff sans erreur. Réinstallation depuis `uv.lock` vérifiée hors ligne. Le premier contrôle des mises à jour est aussi testé sur une machine démarrée depuis moins d’une minute ; seuls les essais suivants après un échec sont temporisés.
- MCP stdio : initialisation, découverte des **28 outils**, appels réels avec le client officiel.
- MCP HTTP Streamable : découverte des 28 outils et appels répétés réussis. Arrêt/redémarrage réel du serveur LM Studio vérifié, API de nouveau accessible ; serveur laissé démarré et processus HTTP de test arrêté.
- Chargement/déchargement : existence puis absence des instances vérifiées ; les instances créées par les tests ont été nettoyées.
- Inférence native : calcul attendu ; conversation conservant un code via `response_id` ; reconnaissance d’un PNG rouge ; réponse compatible OpenAI.
- Embeddings : deux vecteurs de **768 dimensions**.
- Documentation : **284 fichiers suivis par Git**, tous indexés et relus intégralement, empreintes vérifiées ; 178 Markdown/MDX publiés, 48 non publiés, 58 fichiers de support.
- Douze procédures avec sources et limites ; correspondance par fichier vers les outils/tests ou une absence de prise en charge. Ajouts/modifications/suppressions détectés ; sources changées signalées comme `needs_review`.
- Recherche français/anglais : sept demandes de contrôle retrouvent leur page attendue parmi les cinq premiers résultats, environ 0,07 à 0,14 seconde par recherche locale observée.
- MCP documentaire réel : instructions à l'initialisation, catalogue, références par demande, guides, ressource d'orientation, modèle de ressource de page et prompt `lmstudio_workflow` vérifiés en stdio.
- Tests isolés : fraîcheur, échec réseau et conservation hors ligne, temporisation entre sessions, TTL nul, synchronisation concurrente, cache corrompu, chemins arbitraires/liens symboliques refusés, brouillons étiquetés et pagination complète. Ressources et prompt testés en HTTP avec LM Studio indisponible.
- Connexions : test local, sélection d’un profil, utilisation de son API, blocage effectif du CLI local pendant la sélection distante, retour au profil local.
- Diagnostic : serveur, matériel, disque, configurations de modèles et détection d’un connecteur MCP dont le programme manque.
- Administration : simulation des mises à jour stables des moteurs et consultation de LM Link.
- Connecteurs : sauvegarde et modification d’une configuration isolée, découverte réelle, puis appel autorisé d’un outil de test retournant 42. La configuration MCP de l’application n’a pas été remplacée.
- SDK : schéma des réglages réellement installés, sauvegarde d’un profil, chargement et inspection croisée SDK/REST.
- RAG réel : document fictif indexé avec Nomic, recherche par similarité, réponse de Gemma avec citation exacte, puis exclusion du document après sa modification.
- Enregistrement Codex confirmé : `lmstudio-local`, activé en stdio, démarrage 45 s et appels 1 000 s maximum. Un redémarrage du client MCP peut être nécessaire pour recharger la liste des outils.

Preuve de lecture documentaire complète : `.state/docs-smoke.json`.

Preuves locales synthétiques : `.state/smoke-stdio.json` et `.state/operations-smoke.json`. `.state/validation.json` conserve l’empreinte application, moteurs, serveur, code et dépendances : sa modification invalide la validation précédente. Les preuves de transport HTTP et de redémarrage sont consignées séparément dans `.state/server-smoke.json`. Ces rapports restent locaux et ne sont pas publiés, car ils peuvent contenir des chemins de fichiers, des configurations et des extraits documentaires.

## Anomalies et corrections apportées

**Contexte Gemma :** un contexte demandé à **4 096** est rapporté à **34 304** par l’API native, le CLI et le SDK officiel (`get_load_config` et `get_context_length`). Le parallélisme CLI à 1 est bien rapporté à 1. Cet écart n’est pas présenté comme résolu : le MCP renvoie une erreur avec `configuration_mismatches` et l’instance effectivement chargée. Ces lectures concordantes ne permettent pas de distinguer définitivement réglage ignoré par le moteur et valeur mal rapportée par LM Studio.

**RAG et format de Gemma :** le premier appel natif mélangeait des éléments hors JSON. La génération RAG utilise désormais l’endpoint compatible OpenAI avec un schéma JSON. Gemma omet parfois les repères `[S1]` dans le texte malgré une citation correcte dans le champ structuré ; le connecteur les ajoute uniquement après vérification des IDs et des citations exactes. Les références inconnues et citations inventées restent refusées. Il ne s’agit pas d’une validation sémantique de toutes les affirmations.

**Vision et variabilité :** une sortie limitée à 32 tokens a été tronquée ; le test visuel fixe désormais température 0, raisonnement désactivé et 128 tokens. Le test final a reconnu la couleur. Un ancien essai de mémoire conversationnelle avait aussi échoué avant une exécution réussie. Ces essais ne garantissent pas la fidélité systématique des réponses de Gemma.

## Couverture et limites

- Les options avancées GGUF sont validées contre le schéma SDK installé ; les champs inconnus sont refusés. Les réglages MLX supplémentaires passent par les flags CLI pris en charge. Les overrides bruts du protocole moteur et tous les réglages internes de Bionic ne sont pas exposés.
- Le SDK Python 1.5.0 installé n’accepte pas `api_token` dans son constructeur, contrairement à la documentation consultée. Les outils SDK refusent donc une connexion authentifiée avec cette version ; les outils REST gèrent le jeton. L’adaptateur SDK accepte HTTP/WS, tandis que les profils REST permettent HTTPS.
- Les profils distants ont été testés sur une origine locale déclarée distante pour contrôler l’isolation. Aucun deuxième ordinateur, certificat TLS distant ni compte LM Link connecté n’a été testé.
- LM Link : statut testé ; activation, nommage, changement de machine et connexion au compte non exercés. Les commandes disponibles sont vérifiées contre le CLI installé.
- Téléchargement de nouveaux modèles, installation effective de moteurs et import GGUF par copie : outils implémentés, sans téléchargement ni import massif pendant la validation. Les contrats de téléchargement sont testés avec une API simulée.
- Parallélisme, décodage spéculatif, mémoire GPU et quantification KV dépendent du moteur et du matériel. Aucun benchmark multi-GPU ou modèle de brouillon n’a été exécuté. Les options CLI non vérifiables dans l’état sont signalées comme telles.
- Connecteurs : un outil MCP synthétique a été exécuté directement. La délégation complète d’une action externe par un modèle local via les intégrations natives LM Studio, ainsi que les transports distants SSE/HTTP de connecteurs tiers, ne sont pas validées en conditions réelles.
- RAG : index propre au connecteur, sans gestion des pièces jointes ou bases internes de l’interface LM Studio. TXT testé sur modèles réels ; DOCX/tableaux, refus de PDF sans texte, documents invalides, chemins hors racines et citations inventées testés automatiquement. Aucun OCR ni grande bibliothèque de documents réels testé.
- Exemple n8n fourni à titre indicatif ; aucun test dans une instance n8n.
- Les mises à jour sont détectées et les interfaces contrôlées. Les nouveautés futures exigent une adaptation et des tests ; aucune promesse de compatibilité universelle automatique.

## Sources et entretien

La copie locale vient de [lmstudio-ai/docs](https://github.com/lmstudio-ai/docs), dépôt qui alimente le site officiel. Les changements d’application sont vérifiés sur [le changelog](https://lmstudio.ai/changelog/lmstudio). La sortie JSON RAG suit [la documentation des sorties structurées](https://lmstudio.ai/docs/developer/openai-compat/structured-output). Les erreurs de réseau rendent la vérification inconnue, jamais « à jour ».

La documentation est synchronisée automatiquement selon son TTL ; `lm_docs(action="sync")` force cette actualisation. Voir [le périmètre documentaire](documentation.md). `lm_status(refresh_updates=true)` force le contrôle officiel ; les tests réels sont dans `scripts/`. Les mises à jour de l’application passent par l’interface officielle. `lm_runtime(action="update")` ne concerne que les moteurs stables.
