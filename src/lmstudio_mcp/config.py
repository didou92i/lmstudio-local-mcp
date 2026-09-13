import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    base_url: str = "http://127.0.0.1:1234"
    api_token: str = Field(default="", repr=False)
    lms_path: Path = Path.home() / ".lmstudio/bin/lms"
    app_plist: Path = Path("/Applications/LM Studio.app/Contents/Info.plist")
    state_dir: Path = ROOT / ".state"
    update_ttl: int = Field(default=3600, ge=0)
    timeout: float = Field(default=300, gt=0, le=3600)
    allowed_integrations: list[str] = []
    mcp_config_path: Path = Path.home() / ".lmstudio/mcp.json"
    rag_roots: list[Path] = [ROOT / "documents"]

    @model_validator(mode="after")
    def local_only(self):
        url = urlsplit(self.base_url)
        if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("LM_STUDIO_BASE_URL must be a loopback HTTP origin, e.g. http://127.0.0.1:1234")
        self.base_url = self.base_url.rstrip("/")
        return self

    @classmethod
    def from_env(cls):
        return cls(
            base_url=os.getenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234"),
            api_token=os.getenv("LM_STUDIO_API_TOKEN", ""),
            lms_path=Path(os.getenv("LMS_PATH", str(Path.home() / ".lmstudio/bin/lms"))),
            state_dir=Path(os.getenv("LM_MCP_STATE_DIR", str(ROOT / ".state"))),
            update_ttl=int(os.getenv("LM_MCP_UPDATE_TTL", "3600")),
            timeout=float(os.getenv("LM_MCP_TIMEOUT", "300")),
            allowed_integrations=[s.strip() for s in os.getenv("LM_MCP_ALLOWED_INTEGRATIONS", "").split(",") if s.strip()],
            mcp_config_path=Path(os.getenv("LM_MCP_CONFIG_PATH", str(Path.home() / ".lmstudio/mcp.json"))),
            rag_roots=[Path(s).expanduser().resolve() for s in os.getenv("LM_MCP_RAG_ROOTS", str(ROOT / "documents")).split(os.pathsep) if s],
        )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoadOptions(StrictModel):
    context_length: int | None = Field(default=None, ge=1)
    eval_batch_size: int | None = Field(default=None, ge=1)
    flash_attention: bool | None = None
    num_experts: int | None = Field(default=None, ge=1)
    offload_kv_cache_to_gpu: bool | None = None


class AdvancedLoadOptions(StrictModel):
    context_length: int | None = Field(default=None, ge=1)
    gpu: str | None = None
    parallel: int | None = Field(default=None, ge=1)
    ttl: int | None = Field(default=None, ge=1)
    identifier: str | None = None
    speculative_draft_mtp: bool | None = None
    speculative_draft_simple: bool | None = None
    speculative_draft_model: str | None = None
    speculative_draft_max_tokens: int | None = Field(default=None, ge=1)
    speculative_draft_min_tokens: int | None = Field(default=None, ge=1)
    speculative_draft_min_continue_probability: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def valid_gpu(self):
        if self.gpu is not None and self.gpu not in {"off", "max"}:
            try:
                value = float(self.gpu)
                if not 0 <= value <= 1:
                    raise ValueError()
            except ValueError:
                raise ValueError("gpu must be off, max or a ratio between 0 and 1") from None
        if self.speculative_draft_simple and not self.speculative_draft_model:
            raise ValueError("speculative_draft_simple requires speculative_draft_model")
        return self


class ChatOptions(StrictModel):
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=1)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1)
    min_p: float | None = Field(default=None, ge=0, le=1)
    repeat_penalty: float | None = Field(default=None, gt=0)
    max_output_tokens: int = Field(default=1024, ge=1)
    reasoning: str | None = None
    context_length: int | None = Field(default=None, ge=1)
    store: bool = False
    previous_response_id: str | None = None

    @model_validator(mode="after")
    def valid_reasoning(self):
        if self.reasoning not in {None, "off", "on", "low", "medium", "high"}:
            raise ValueError("Unsupported reasoning value")
        return self


class Integration(StrictModel):
    id: str
    allowed_tools: list[str] = Field(min_length=1)
