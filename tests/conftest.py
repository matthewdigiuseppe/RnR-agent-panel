"""Shared fixtures. Every test runs on deterministic mock models: no API calls."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from peerreview.config import Config
from peerreview.db import Store
from peerreview.orchestrator import Orchestrator
from peerreview.reporting import Reporter

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO = REPO_ROOT / "examples" / "demo"


def make_project(tmp_path: Path, *, overrides: dict | None = None,
                 script: dict | None = None) -> Config:
    """Copy the bundled demo into a temp dir, optionally overriding config/script."""
    root = tmp_path / "project"
    shutil.copytree(DEMO, root)
    config = Config.load(root)
    if script is not None:
        (root / "mock_script.json").write_text(json.dumps(script), encoding="utf-8")
    if overrides:
        def merge(base, overlay):
            for key, value in overlay.items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    merge(base[key], value)
                else:
                    base[key] = value
        merge(config.data, overrides)
    return config


def run_project(config: Config) -> tuple[Store, str, Orchestrator]:
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.run()
    return store, orchestrator.run_id, orchestrator


@pytest.fixture(scope="session")
def demo_run(tmp_path_factory) -> tuple[Store, str, Config]:
    """One complete scripted deliberation, reused across read-only tests."""
    config = make_project(tmp_path_factory.mktemp("demo"))
    store, run_id, _ = run_project(config)
    return store, run_id, config


@pytest.fixture
def project(tmp_path) -> Config:
    return make_project(tmp_path)
