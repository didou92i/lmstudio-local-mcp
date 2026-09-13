"""Versioned, complete repository reference with bounded retrieval for MCP clients."""
import asyncio
import fcntl
import hashlib
import os
import re
import shutil
import time
import unicodedata
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urljoin
from uuid import uuid4

from .client import ConnectorError, now
from .doc_guides import GUIDES, OPERATION_TOPICS
from .storage import atomic_json, read_json

REPOSITORY = "https://github.com/lmstudio-ai/docs.git"
BROWSE = "https://github.com/lmstudio-ai/docs/blob/"
SCHEMA = 1
BASELINE = Path(__file__).parent / "data/docs-coverage.json"
# Repository parsing rules: https://github.com/lmstudio-ai/docs#parsing-rules
MARKDOWN = {".md", ".mdx"}
ALIASES = {
    "reglage": "configuration config settings parameters", "reglages": "configuration config settings parameters",
    "contexte": "context contextlength context_length", "memoire": "memory ram vram context",
    "chargement": "load loading", "charger": "load loading", "decharger": "unload",
    "modele": "model models", "modeles": "model models", "serveur": "server",
    "reseau": "network remote", "connexion": "connection server authentication",
    "authentification": "authentication token permissions", "jeton": "token authentication",
    "erreur": "error troubleshooting", "diagnostic": "error troubleshooting server",
    "connecteur": "mcp integrations", "connecteurs": "mcp integrations",
    "documents": "documents retrieval rag", "rag": "rag retrieval embeddings",
    "installation": "install setup requirements", "mise": "update", "jour": "update",
    "telecharger": "download", "telechargement": "download", "raisonnement": "reasoning",
    "annuler": "cancel abort", "annulation": "cancel abort", "outils": "tools mcp",
    "image": "image vision", "images": "image vision", "parametres": "parameters config settings",
    "confidentialite": "privacy offline", "hors": "offline", "ligne": "offline",
    "tokenisation": "tokenization tokenize tokens", "prereglage": "preset presets",
    "prereglages": "preset presets", "configurer": "configuration config settings",
}
STOP = {"le", "la", "les", "de", "des", "du", "un", "une", "en", "et", "pour", "avec", "sur", "dans",
        "comment", "mon", "mes", "je", "tu", "il", "est", "a", "au", "the", "and", "of", "to", "in"}


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def terms(text, expand=False):
    found = set(re.findall(r"[a-z0-9_]+", normalize(text))) - STOP
    if expand:
        found |= {v for t in list(found) for v in ALIASES.get(t, ALIASES.get(t.rstrip("s"), "")).split()}
    return found


def page_info(path, content):
    lines = content.splitlines()
    title = Path(path).stem
    if lines and lines[0] == "---":
        for line in lines[1:]:
            if line == "---":
                break
            if line.startswith("title:"):
                title = line.partition(":")[2].strip().strip('"\'')
    else:
        title = next((line.lstrip("# ") for line in lines if line.startswith("# ")), title)
    headings = [{"line": i, "title": line.lstrip("# ")} for i, line in enumerate(lines, 1)
                if re.match(r"^#{1,6} ", line)]
    links = []
    for i, line in enumerate(lines, 1):
        for match in re.finditer(r'''(?:\]\(|(?:src|href)=["'])([^\s)"'<>]+)''', line):
            target = match[1]
            if target.startswith("/") and not target.startswith("//"):
                target = urljoin("https://lmstudio.ai", target)
            if target.startswith(("https://", "http://")):
                links.append({"line": i, "url": target, "fetched": False})
    return {"title": title, "line_count": len(lines), "headings": headings, "links": links}


def sync_state(path):
    try:
        value = read_json(path)
        if not isinstance(value, dict):
            raise TypeError()
        return value
    except (OSError, ValueError, TypeError):
        return {"status": "failed", "warning": "Local documentation freshness state unreadable; verify upstream again."}


class Knowledge:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.state_dir / "lmstudio-docs"
        self.meta = settings.state_dir / "docs-sync.json"
        self.index_path = settings.state_dir / "docs-index.json"
        self._index = None
        self._index_stamp = None
        try:
            self.baseline = read_json(BASELINE, {"files": {}})
        except (OSError, ValueError):
            self.baseline = {"files": {}}

    @asynccontextmanager
    async def locked(self):
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.settings.state_dir / "docs.lock").open("a") as lock:
            deadline = time.monotonic() + 20
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ConnectorError("Documentation busy; retry the read after the current sync") from None
                    await asyncio.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    async def git(self, *args):
        # Public reference download: no LM tokens, credential helpers, global URL rewrites or hooks.
        env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
        process = await asyncio.create_subprocess_exec(
            "git", "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=",
            "-c", "http.extraHeader=", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never",
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        try:
            out, _ = await asyncio.wait_for(process.communicate(), 15)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise ConnectorError("Official documentation Git operation failed; last indexed copy retained")
        return out.decode().rstrip("\n")

    def index(self):
        try:
            stat = self.index_path.stat()
        except OSError:
            return {"files": {}, "schema": SCHEMA}
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self._index_stamp:
            try:
                value = read_json(self.index_path)
                if (not isinstance(value, dict) or value.get("schema") != SCHEMA or
                        not isinstance(value.get("files"), dict)):
                    raise ValueError()
            except (OSError, ValueError):
                return {"files": {}, "schema": SCHEMA,
                        "index_error": "Local index unreadable or incompatible; rebuild from the official clone with sync."}
            self._index, self._index_stamp = value, stamp
        return self._index

    async def _origin(self):
        if self.root.is_symlink() or (self.root / ".git").is_symlink():
            raise ConnectorError("Documentation repository must not be a symbolic link")
        if await self.git("-C", str(self.root), "remote", "get-url", "origin") != REPOSITORY:
            raise ConnectorError("Documentation origin differs from the official repository")
        if await self.git("-C", str(self.root), "status", "--porcelain"):
            raise ConnectorError("Documentation cache modified locally; sync refused to preserve changes")

    async def _fetch(self):
        if not self.root.exists():
            stage = self.root.with_name(".docs-clone-" + uuid4().hex)
            try:
                await self.git("clone", "--depth", "1", "--branch", "main", REPOSITORY, str(stage))
                stage.rename(self.root)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        else:
            await self._origin()
            await self.git("-C", str(self.root), "fetch", "--depth", "1", "origin", "main")
            await self.git("-C", str(self.root), "checkout", "--detach", "FETCH_HEAD")

    async def _build_index(self):
        await self._origin()
        commit = await self.git("-C", str(self.root), "rev-parse", "HEAD")
        tree = await self.git("-C", str(self.root), "ls-tree", "-rlz", "HEAD")
        files = {}
        for entry in tree.split("\0"):
            if not entry:
                continue
            info, path = entry.split("\t", 1)
            mode, kind, oid, size = info.split()
            p = Path(path)
            if p.is_absolute() or ".." in p.parts or ".git" in p.parts:
                raise ConnectorError("Unsafe path in documentation tree")
            published = not any(part.startswith((".", "_")) for part in p.parts)
            record = {"path": path, "section": p.parts[0] if len(p.parts) > 1 else "repository",
                      "published": published and p.suffix in MARKDOWN, "size": int(size) if size.isdigit() else 0,
                      "kind": "document" if p.suffix in MARKDOWN else "support", "readable": False,
                      "title": p.name, "git_blob": oid}
            target = self.root / path
            if kind != "blob" or mode not in {"100644", "100755"}:
                record["limitation"] = "Non-regular Git entry; never followed or executed"
            elif record["size"] > 8 * 1024 * 1024:
                record["limitation"] = "File exceeds the 8 MiB text retrieval limit"
            elif target.is_symlink() or not target.resolve().is_relative_to(self.root.resolve()):
                raise ConnectorError("Unsafe link in documentation cache")
            else:
                raw = target.read_bytes()
                blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                if blob != oid:
                    raise ConnectorError("Documentation changed during indexing; last indexed copy retained")
                record["sha256"] = hashlib.sha256(raw).hexdigest()
                try:
                    content = raw.decode("utf-8")
                    if "\0" in content:
                        raise UnicodeError()
                    record.update(page_info(path, content), content=content, readable=True)
                except UnicodeError:
                    record["kind"] = "asset"
                    record["limitation"] = "Binary asset: catalogued with source URL; no OCR or media interpretation"
            files[path] = record
        if not files or not any(f["kind"] == "document" for f in files.values()):
            raise ConnectorError("Official repository has no documentation; last indexed copy retained")
        previous = self.index()
        before = previous.get("files", {})
        changes = []
        for path in sorted(set(before) | set(files)):
            old, new = before.get(path), files.get(path)
            if old is None:
                state = "added"
            elif new is None:
                state = "removed"
            elif old["git_blob"] != new["git_blob"]:
                state = "modified"
            else:
                continue
            changes.append({"path": path, "change": state, "old_blob": old and old["git_blob"],
                            "new_blob": new and new["git_blob"]})
        value = {"schema": SCHEMA, "commit": commit, "indexed_at": now(), "files": files,
                 "previous_commit": previous.get("commit"), "changes": changes}
        # Preserve the latest meaningful delta across no-op syncs. Coverage drift persists independently.
        if previous.get("commit") == commit:
            value["changes"] = previous.get("changes", [])
            value["previous_commit"] = previous.get("previous_commit")
        atomic_json(self.index_path, value)
        return value

    def _metadata(self):
        index, meta = self.index(), sync_state(self.meta)
        files = list(index["files"].values())
        available = bool(files)
        age = max(0, time.time() - meta.get("checked_epoch", 0))
        fresh = available and meta.get("status") == "checked" and age <= max(1, self.settings.docs_ttl)
        review = {r["path"] for r in files if self.assessment(r)["status"] == "needs_review"}
        removed = set(self.baseline.get("files", {})) - set(index["files"]) if available else set()
        return {
            "available": available, "repository": REPOSITORY, "commit": index.get("commit"),
            "indexed_at": index.get("indexed_at"), "synced_at": meta.get("synced_at"),
            "freshness": "fresh" if fresh else "stale" if available else "unavailable",
            "sync_status": meta.get("status", "never_checked"), "last_attempt_at": meta.get("attempted_at"),
            "auto_sync": self.settings.docs_auto_sync, "ttl_seconds": self.settings.docs_ttl,
            "counts": {"repository_files": len(files), "readable_files": sum(f["readable"] for f in files),
                       "published_documents": sum(f["published"] for f in files),
                       "unpublished_documents": sum(f["kind"] == "document" and not f["published"] for f in files),
                       "support_files": sum(f["kind"] != "document" for f in files)},
            "coverage": {"assessed": available, "baseline_commit": self.baseline.get("commit"), "files_needing_review": len(review),
                         "removed_files": len(removed), "affected_guides": [t for t, g in GUIDES.items()
                             if set(g["sources"]) & (review | removed)]},
            "warning": index.get("index_error") or meta.get("warning"),
            "note": "Reference data, not permissions. Current upstream docs may differ from the installed app/SDK.",
        }

    async def metadata(self):
        async with self.locked():
            return self._metadata()

    async def ensure(self, force=False):
        async with self.locked():
            meta = sync_state(self.meta)
            indexed = bool(self.index()["files"])
            age = time.time() - meta.get("checked_epoch", 0)
            retry_age = time.time() - meta.get("attempted_epoch", 0)
            due = force or (self.settings.docs_auto_sync and
                (not indexed or age >= self.settings.docs_ttl or meta.get("status") != "checked") and
                (meta.get("status") != "failed" or retry_age >= 60))
            try:
                if due:
                    await self._fetch()
                    await self._build_index()
                    meta = {"status": "checked", "synced_at": now(), "checked_epoch": time.time(),
                            "attempted_at": now(), "attempted_epoch": time.time()}
                    atomic_json(self.meta, meta)
                elif not indexed and self.root.exists():
                    # Offline migration of an existing clone never pretends to verify upstream freshness.
                    await self._build_index()
            except (ConnectorError, OSError, ValueError, TimeoutError):
                meta.update(status="failed", attempted_at=now(), attempted_epoch=time.time(),
                            warning="Upstream documentation could not be verified; last indexed copy retained. Use sync to retry.")
                atomic_json(self.meta, meta)
                if not indexed and self.root.exists():
                    try:
                        await self._build_index()
                    except (ConnectorError, OSError, ValueError, TimeoutError):
                        pass
            return self._metadata()

    async def sync(self):
        result = await self.ensure(force=True)
        if result["sync_status"] != "checked":
            raise ConnectorError(result["warning"])
        return result

    def assessment(self, record):
        baseline = self.baseline.get("files", {}).get(record["path"])
        if baseline is None or baseline.get("sha256") != record.get("sha256"):
            return {"status": "needs_review", "tools": [], "tests": [],
                    "limitation": "New or changed content since the coverage baseline; no automatic capability claim"}
        return {k: v for k, v in baseline.items() if k != "sha256"}

    def summary(self, record, commit):
        return {k: v for k, v in record.items() if k not in {"content", "headings", "links", "git_blob"}} | {
            "url": BROWSE + commit + "/" + quote(record["path"]), "coverage": self.assessment(record),
            "resource_uri": "lmstudio://docs/page/" + quote(record["path"], safe="")}

    def _selected(self, scope="published", section=""):
        if scope not in {"published", "all"}:
            raise ConnectorError("scope must be published or all")
        return [r for r in self.index()["files"].values()
                if (scope == "all" or r["published"]) and (not section or r["section"] == section)]

    def _search(self, query, scope="published", section=""):
        expanded, exact = terms(query, True), terms(query)
        if not expanded:
            return []
        result = []
        commit = self.index().get("commit", "")
        for record in self._selected(scope, section):
            if not record["readable"]:
                continue
            text = normalize(record["content"])
            title_terms = terms(record["title"] + " " + record["path"])
            hits = expanded & terms(text)
            if not hits:
                continue
            score = len(hits) + 5 * len(hits & exact) + 8 * len(expanded & title_terms)
            if normalize(query).strip() in text:
                score += 15
            lines = record["content"].splitlines()
            ranked = [(len(terms(line) & expanded) + 3 * len(terms(line) & exact), i)
                      for i, line in enumerate(lines, 1)]
            _, line = max(ranked, key=lambda x: (x[0], -x[1]))
            excerpt = "\n".join(lines[max(0, line-2):line+3])[:1800]
            result.append(self.summary(record, commit) | {"score": score, "line": line, "excerpt": excerpt,
                           "url": BROWSE + commit + "/" + quote(record["path"]) + f"#L{line}"})
        return sorted(result, key=lambda r: (-r["score"], r["path"]))

    def _guide(self, topic):
        guide = GUIDES[topic]
        index = self.index()
        sources = [self.summary(index["files"][p], index["commit"]) for p in guide["sources"] if p in index["files"]]
        return {"topic": topic, **guide, "sources": sources,
                "missing_sources": [p for p in guide["sources"] if p not in index["files"]],
                "guide_baseline_commit": self.baseline.get("commit"),
                "requires_source_review": len(sources) != len(guide["sources"]) or
                    any(s["coverage"]["status"] == "needs_review" for s in sources),
                "verification": "Read source pages and check lm_status/lm_models and installed schemas before acting."}

    def context(self, operation):
        topic = OPERATION_TOPICS.get(operation, "diagnostic")
        return {"topic": topic, "next_action": f"lm_docs(action='guide', query='{topic}')",
                "coverage_action": "lm_docs(action='coverage')",
                "instruction": "Read relevant official sources before an unfamiliar operation; verify effective state afterwards."}

    async def run(self, action, query="", path="", start_line=1, limit=12, offset=0, scope="published", section="",
                  prepared=False):
        if action == "sync":
            return await self.sync()
        if not prepared:
            await self.ensure()
        async with self.locked():
            source, index = self._metadata(), self.index()
            limit, offset = max(1, min(limit, 200)), max(0, offset)
            if action == "status":
                return source | {"topics": list(GUIDES), "sections": dict(Counter(r["section"] for r in index["files"].values()))}
            if action == "guide":
                topics = [normalize(query)] if normalize(query) in GUIDES else []
                return {"source": source, "procedures": {t: self._guide(t) for t in topics},
                        "available_topics": list(GUIDES),
                        "query_matched_topic": normalize(query) in GUIDES}
            if not source["available"]:
                return {"source": source, "available": False, "next_action": "lm_docs(action='sync')"}
            if action == "read":
                # Only exact paths in the indexed Git tree: never read arbitrary user files or follow links.
                record = index["files"].get(path)
                if record is None or (scope != "all" and not record["published"]):
                    raise ConnectorError("Select a published path from catalog/search, or use scope='all' for repository support files")
                if not record["readable"]:
                    return {"source": source, **self.summary(record, index["commit"])}
                lines = record["content"].splitlines()
                start = max(1, start_line)
                chunk = lines[start-1:start-1+limit]
                text = "\n".join(chunk)
                # Offset is a character cursor within this line range for unusually long source lines.
                body = text[offset:offset+24000]
                more = offset + len(body) < len(text)
                return {"source": source, **self.summary(record, index["commit"]), "start_line": start,
                        "end_line": min(start+len(chunk)-1, len(lines)), "text": body,
                        "next_offset": offset+len(body) if more else None,
                        "next_start_line": None if more or start+len(chunk) > len(lines) else start+len(chunk),
                        "headings": record["headings"], "links": record["links"],
                        "url": BROWSE + index["commit"] + "/" + quote(path) + f"#L{start}"}
            if action == "search":
                rows = self._search(query, scope, section)
                limit = min(limit, 50)
                return {"source": source, "results": rows[offset:offset+limit], "total_results": len(rows),
                        "next_offset": offset+limit if offset+limit < len(rows) else None,
                        "total_documents": sum(r["published"] for r in index["files"].values())}
            if action == "brief":
                matches = self._search(query, scope, section)[:min(limit, 10)]
                topics = [t for t, g in GUIDES.items() if any(r["path"] in g["sources"] for r in matches)]
                if normalize(query) in GUIDES:
                    topics = [normalize(query)]
                return {"source": source, "request": query, "references": matches,
                        "procedures": {t: self._guide(t) for t in topics[:3]},
                        "checks": ["lm_status: installed app/SDK and freshness", "lm_models: exact IDs and capabilities",
                                   "Read complete relevant pages with read and next_start_line", "Verify effective state after action"],
                        "note": "Retrieval suggestions, not an autonomous plan or a guarantee that an operation is implemented."}
            if action == "changes":
                rows = index.get("changes", [])
                return {"source": source, "previous_commit": index.get("previous_commit"),
                        "changes": rows[offset:offset+limit], "total_changes": len(rows),
                        "next_offset": offset+limit if offset+limit < len(rows) else None,
                        "review_required": "Use coverage; content changes never mark themselves implemented or tested."}
            if action in {"catalog", "coverage"}:
                selected = self._selected(scope, section)
                if path:
                    selected = [r for r in selected if r["path"] == path]
                if query:
                    selected = [r for r in selected if terms(query, True) & terms(r["path"] + " " + r["title"])]
                rows = [self.summary(r, index["commit"]) for r in sorted(selected, key=lambda r: r["path"])]
                counts = dict(Counter(r["coverage"]["status"] for r in rows))
                removed = sorted(set(self.baseline.get("files", {})) - set(index["files"]))
                return {"source": source, "files": rows[offset:offset+limit], "total_files": len(rows),
                        "next_offset": offset+limit if offset+limit < len(rows) else None,
                        "coverage_counts": counts, "baseline_commit": self.baseline.get("commit"),
                        "removed_since_baseline": removed,
                        "note": "Coverage describes connector functions, not documentation retrieval. Tests cover the stated subset only."}
            raise ConnectorError("Unknown documentation action")
