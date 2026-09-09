"""M7: deployed-mode behaviour (inline charts, OS-level read-only DB, relaxed
config) and the Cloud Run artifacts. No Docker, no network.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

import config
from agent import tools
from db import engine, migrate

_ROOT = Path(__file__).resolve().parents[1]


# --- make_chart: inline data URI when deployed --------------------------------- #
_SPEC = {
    "chart_type": "line",
    "x": "year",
    "y": "v",
    "title": "t",
    "data": [{"year": 2020, "v": 1}, {"year": 2021, "v": 2}, {"year": 2022, "v": 3}],
}


def test_make_chart_local_writes_a_file(monkeypatch, tmp_path):
    monkeypatch.setattr(tools.get_settings(), "DEPLOY_MODE", "local")
    monkeypatch.setattr(tools, "_CHART_DIR", tmp_path / "charts")
    ctx = tools.RunContext()
    out = tools.make_chart(dict(_SPEC), ctx)
    assert out["chart_path"].endswith(".html")
    assert list((tmp_path / "charts").glob("*.html"))
    assert ctx.charts == [out["chart_path"]]


def test_make_chart_deployed_returns_inline_data_uri(monkeypatch, tmp_path):
    monkeypatch.setattr(tools.get_settings(), "DEPLOY_MODE", "deployed")
    monkeypatch.setattr(tools, "_CHART_DIR", tmp_path / "charts")
    ctx = tools.RunContext()
    out = tools.make_chart(dict(_SPEC), ctx)

    assert "chart_path" not in out
    uri = out["chart_data_uri"]
    assert uri.startswith("data:text/html;base64,")
    html = base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")
    assert "<html" in html.lower() and "plotly" in html.lower()
    assert not (tmp_path / "charts").exists()  # nothing written to disk
    assert ctx.charts == [uri]


# (config validation for deployed mode lives in test_smoke.py)


# --- ro_engine: OS-level mode=ro when deployed ------------------------------- #
def test_ro_engine_deployed_uses_mode_ro_and_still_reads(sqlite_env, monkeypatch):
    migrate.main()  # build the file first (rw)
    monkeypatch.setattr(config.get_settings(), "DEPLOY_MODE", "deployed")
    engine.ro_engine.cache_clear()

    eng = engine.ro_engine()
    assert "mode=ro" in str(eng.url)

    with eng.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM datasets")).scalar() == 0
        with pytest.raises(Exception):  # noqa: B017 - any write failure is fine
            conn.execute(text("CREATE TABLE nope (x)"))
    engine.ro_engine.cache_clear()


# --- Cloud Run artifacts ---------------------------------------------------- #
def test_deploy_requirements_is_a_subset_of_root():
    def pkgs(p: Path) -> set[str]:
        out = set()
        for line in p.read_text().splitlines():
            line = line.split("#")[0].strip()
            if line:
                out.add(line.lower().replace("_", "-"))
        return out

    root = pkgs(_ROOT / "requirements.txt")
    deploy = pkgs(_ROOT / "deploy" / "requirements.txt")
    assert deploy, "deploy/requirements.txt is empty"
    assert deploy <= root, f"not in root requirements.txt: {sorted(deploy - root)}"
    # the things the slim image deliberately drops
    assert {"jupyterlab", "ruff", "pytest", "kaleido"} & root
    assert not ({"jupyterlab", "ruff", "pytest", "kaleido"} & deploy)


# Distribution name -> the name you actually ``import``.
_IMPORT_NAME = {
    "scikit-learn": "sklearn",
    "pyyaml": "yaml",
    "pydantic-settings": "pydantic_settings",
    "python-dotenv": "dotenv",
}
# Modules the runtime may import without declaring, because something it does
# declare guarantees them.
_TRANSITIVE_OK = {"pydantic"}  # pydantic-settings depends on it


def test_every_runtime_import_is_installed_in_the_image():
    """Anything ``app/`` or ``agent/`` imports must be in deploy/requirements.txt.

    The converse of the subset test above, and the one that matters in
    production: a module missing here still builds, still boots, and still
    serves /health — then raises ModuleNotFoundError on the first request that
    reaches it. That is exactly how ``redis`` shipped. M7 wrote that file and
    listed redis as deliberately dropped; M8 then put a Redis-backed budget
    check ahead of /ask, and the gap surfaced as a 500 on Cloud Run.
    """
    import ast
    import sys

    declared = set()
    for line in (_ROOT / "deploy" / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            name = line.lower()
            declared.add(_IMPORT_NAME.get(name, name.replace("-", "_")))
    declared |= _TRANSITIVE_OK

    local = {p.name for p in _ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    local |= {"config", "app", "agent", "db", "sources", "model", "ingest"}

    missing: dict[str, str] = {}
    for path in sorted((*(_ROOT / "app").rglob("*.py"), *(_ROOT / "agent").rglob("*.py"))):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module.split(".")[0]]
            else:
                continue
            for mod in mods:
                if mod in sys.stdlib_module_names or mod in local or mod in declared:
                    continue
                missing.setdefault(mod, f"{path.relative_to(_ROOT).as_posix()}:{node.lineno}")

    assert not missing, "imported at runtime but not in deploy/requirements.txt: " + ", ".join(
        f"{m} ({where})" for m, where in sorted(missing.items())
    )


def test_dockerfile_shape():
    df = (_ROOT / "deploy" / "Dockerfile").read_text()
    assert "FROM python:3.11-slim" in df
    assert "deploy/requirements.txt" in df
    assert "DEPLOY_MODE=deployed" in df
    assert "claude-haiku-4-5" in df
    assert "USER appuser" in df
    assert "gunicorn" in df and "app.api:create_app()" in df


def test_cloudrun_manifest_is_valid_and_scales_to_zero():
    manifest = yaml.safe_load((_ROOT / "deploy" / "cloudrun.yaml").read_text())
    assert manifest["kind"] == "Service"
    tmpl = manifest["spec"]["template"]
    assert tmpl["metadata"]["annotations"]["autoscaling.knative.dev/minScale"] == "0"
    (container,) = tmpl["spec"]["containers"]
    env = {e["name"]: e for e in container["env"]}
    assert env["ANALYST_MODEL"]["value"] == "claude-haiku-4-5"
    assert env["ANTHROPIC_API_KEY"]["valueFrom"]["secretKeyRef"]["name"] == "anthropic-api-key"
    assert container["ports"][0]["containerPort"] == 8080


def test_dockerignore_excludes_secrets_and_dev_trees():
    ignore = (_ROOT / ".dockerignore").read_text().splitlines()
    for pat in (".env", "tests", "notebooks", ".venv"):
        assert pat in ignore
