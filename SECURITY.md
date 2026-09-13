# Confidentialité et signalement

Les configurations de connexion, jetons, documents, index RAG et journaux restent sur la machine qui exécute le serveur. Le dossier `.state/` peut contenir des informations sensibles, y compris les sauvegardes de configuration MCP : ne pas le publier.

Les résultats retournés au client MCP peuvent contenir les extraits documentaires demandés. Un serveur LM Studio distant reçoit les entrées d’inférence et d’embeddings qui lui sont adressées.

Le transport HTTP écoute sur loopback. Toute exposition à un réseau nécessite une configuration distincte d’authentification et de transport. Un connecteur MCP tiers peut exécuter son programme configuré et les outils expressément autorisés.

Pour signaler un problème, fournir une reproduction minimale avec des données fictives. Ne pas joindre de jeton, de fichier `.env`, de configuration privée, de journal brut ou de document de travail à une issue publique. Utiliser le signalement privé GitHub lorsqu’il est disponible pour une vulnérabilité.
