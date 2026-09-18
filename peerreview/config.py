"""Configuration loading (YAML or TOML) and agent specification."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import AUTHOR, EDITOR, REVIEWERS, AgentSpec
from .prompts import build_system_prompt

DEFAULTS: dict[str, Any] = {
    "project": {
        "name": "project",
        "manuscript": "papers/manuscript.md",
        "appendix": None,
        "supplementary": [],
        "codebook": None,
        "journal": None,
        "runs_dir": "runs",
        "db": None,                       # defaults to <runs_dir>/peerreview.db
    },
    "deliberation": {
        "max_rounds": 20,
        "stable_rounds_before_stop": 2,
        "intervention_word_limit": 350,
        "max_agenda_per_round": 3,
        "author_first_response": True,
        "enforce_word_limit": True,
    },
    "execution": {
        "isolation": "thread",            # serial | thread | process
        "max_workers": 5,
    },
    "context": {
        "max_prompt_tokens": 60000,
        "recent_messages": 14,
        "own_turn_memory": 3,
        "manuscript_sections_per_issue": 2,
        "message_excerpt_words": 260,
    },
    "defaults": {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "temperature": 0.3,
        "max_tokens": 2000,
    },
    "agents": {
        "author": {"role": "author", "name": AUTHOR,
                   "expertise": "the manuscript's author team"},
        "reviewer1": {"role": "reviewer", "name": "Reviewer1", "specialization": "substantive",
                      "expertise": "substantive theory, mechanisms, scope conditions, "
                                   "and the relationship to the literature"},
        "reviewer2": {"role": "reviewer", "name": "Reviewer2", "specialization": "methods",
                      "expertise": "causal identification, estimands, measurement, "
                                   "specification and statistical inference"},
        "reviewer3": {"role": "reviewer", "name": "Reviewer3", "specialization": "generalist",
                      "expertise": "general-audience importance, claim calibration, "
                                   "external validity and exposition"},
        "editor": {"role": "editor", "name": EDITOR, "expertise": "chair of the deliberation"},
    },
    "revision_stage": {
        "enabled": False,
    },
}

AGENT_KEYS = ("author", "reviewer1", "reviewer2", "reviewer3", "editor")


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_config_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".toml"}:
        import tomllib
        return tomllib.loads(text)
    import yaml
    return yaml.safe_load(text) or {}


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    path: Path | None = None

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        if path.is_dir():
            for candidate in ("peerreview.yaml", "peerreview.yml", "peerreview.toml",
                              "config.yaml", "config.yml", "config.toml"):
                if (path / candidate).exists():
                    path = path / candidate
                    break
            else:
                raise FileNotFoundError(f"no peerreview config found in {path}")
        return cls(data=_deep_merge(DEFAULTS, _read_config_file(path)), path=path)

    @property
    def root(self) -> Path:
        return self.path.parent if self.path else Path.cwd()

    def resolve(self, value: str | None) -> Path | None:
        if not value:
            return None
        candidate = Path(os.path.expanduser(str(value)))
        return candidate if candidate.is_absolute() else (self.root / candidate)

    # ------------------------------------------------------------ accessors
    @property
    def project(self) -> dict[str, Any]:
        return self.data["project"]

    @property
    def deliberation(self) -> dict[str, Any]:
        return self.data["deliberation"]

    @property
    def execution(self) -> dict[str, Any]:
        return self.data["execution"]

    @property
    def context(self) -> dict[str, Any]:
        return self.data["context"]

    @property
    def name(self) -> str:
        return self.project.get("name") or (self.path.parent.name if self.path else "project")

    @property
    def journal(self) -> str | None:
        return self.project.get("journal")

    @property
    def runs_dir(self) -> Path:
        return self.resolve(self.project.get("runs_dir") or "runs")

    @property
    def db_path(self) -> Path:
        configured = self.project.get("db")
        return self.resolve(configured) if configured else (self.runs_dir / "peerreview.db")

    @property
    def revision_stage_enabled(self) -> bool:
        return bool(self.data.get("revision_stage", {}).get("enabled"))

    def manuscript_inputs(self) -> dict[str, Any]:
        supplementary = self.project.get("supplementary") or []
        if isinstance(supplementary, str):
            supplementary = [supplementary]
        return {
            "manuscript": self.resolve(self.project.get("manuscript")),
            "appendix": self.resolve(self.project.get("appendix")),
            "supplementary": [self.resolve(item) for item in supplementary],
            "codebook": self.resolve(self.project.get("codebook")),
            "journal": self.journal,
        }

    # --------------------------------------------------------------- agents
    def agent_entry(self, key: str) -> dict[str, Any]:
        entry = dict(DEFAULTS["agents"][key])
        entry.update(self.data.get("agents", {}).get(key, {}) or {})
        return entry

    def agent_specs(self) -> dict[str, AgentSpec]:
        defaults = self.data["defaults"]
        word_limit = int(self.deliberation.get("intervention_word_limit", 350))
        specs: dict[str, AgentSpec] = {}
        for key in AGENT_KEYS:
            entry = self.agent_entry(key)
            name = entry.get("name") or key.capitalize()
            custom_prompt = entry.get("system_prompt")
            prompt_file = entry.get("system_prompt_file")
            if prompt_file:
                custom_prompt = self.resolve(prompt_file).read_text(encoding="utf-8")
            params = {
                "temperature": entry.get("temperature", defaults.get("temperature", 0.3)),
                "max_tokens": entry.get("max_tokens", defaults.get("max_tokens", 2000)),
            }
            for extra in ("base_url", "api_key_env", "script", "script_path", "executable",
                          "timeout", "max_retries", "top_p"):
                if extra in entry:
                    params[extra] = entry[extra]
                elif extra in defaults:
                    params[extra] = defaults[extra]
            if params.get("script_path"):  # relative to the config file, not the cwd
                params["script_path"] = str(self.resolve(params["script_path"]))
            specs[name] = AgentSpec(
                id=name,
                role=entry.get("role", "reviewer"),
                name=name,
                expertise=entry.get("expertise", ""),
                system_prompt=build_system_prompt(
                    role=entry.get("role", "reviewer"), name=name,
                    expertise=entry.get("expertise", ""), journal=self.journal or "",
                    word_limit=word_limit, specialization=entry.get("specialization"),
                    custom_prompt=custom_prompt),
                provider=entry.get("provider", defaults.get("provider", "anthropic")),
                model=entry.get("model", defaults.get("model", "claude-sonnet-5")),
                params=params,
            )
        missing = [name for name in (AUTHOR, EDITOR, *REVIEWERS) if name not in specs]
        if missing:
            raise ValueError(f"configuration is missing agents: {', '.join(missing)}")
        return specs

    def model_config(self) -> dict[str, Any]:
        return {spec.id: {"provider": spec.provider, "model": spec.model, "params": spec.params}
                for spec in self.agent_specs().values()}

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)


TEMPLATE = """# peerreview project configuration
project:
  name: {name}
  manuscript: papers/manuscript.md      # .md or .pdf
  appendix: null                        # papers/appendix.pdf
  supplementary: []
  codebook: null
  journal: British Journal of Political Science
  runs_dir: runs

deliberation:
  max_rounds: 20
  stable_rounds_before_stop: 2          # stop after N rounds with no position change
  intervention_word_limit: 350
  max_agenda_per_round: 3
  author_first_response: true

execution:
  isolation: thread                     # serial | thread | process
  max_workers: 5

context:
  max_prompt_tokens: 60000
  recent_messages: 14
  own_turn_memory: 3

# Provider defaults; any agent may override provider/model independently.
defaults:
  provider: {provider}
  model: {model}
  temperature: 0.3
  max_tokens: 2000

agents:
  author:
    # provider: anthropic
    # model: claude-opus-5
    expertise: the manuscript's author team

  reviewer1:
    specialization: substantive
    expertise: substantive theory, mechanisms, scope conditions, relationship to the literature

  reviewer2:
    specialization: methods
    expertise: causal identification, estimands, measurement, specification, inference

  reviewer3:
    specialization: generalist
    expertise: general-audience importance, claim calibration, external validity, exposition
    # provider: openai
    # model: gpt-4o

  editor:
    expertise: chair of the deliberation

# Optional stage: after deliberation, have the Author produce a revised
# manuscript and a response letter from the revision plan.
revision_stage:
  enabled: false
"""


def render_template(name: str, provider: str = "anthropic", model: str = "claude-sonnet-5") -> str:
    return TEMPLATE.format(name=name, provider=provider, model=model)
