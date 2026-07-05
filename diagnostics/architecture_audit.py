from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"old", "__pycache__"}
LEGACY_SHIMS = {
    "app_runtime.py",
    "simulation.py",
    "rendering.py",
    "render_central2.py",
    "doctrine.py",
    "emotion_runtime.py",
    "npc_types.py",
    "save_load.py",
    "tuning.py",
    "constants.py",
    "emx_system.py",
    "emx_composites.py",
    "emx_sphere.py",
}
ROOT_ONLY_SHIM_MODULES = {
    "app_runtime",
    "render_central2",
    "doctrine",
    "emotion_runtime",
    "npc_types",
    "save_load",
    "tuning",
    "constants",
    "emx_system",
    "emx_composites",
    "emx_sphere",
}
RESTRICTED_MODULES = {
    "runtime.app_runtime": "runtime",
    "simulation.simulation": "simulation",
    "simulation.doctrine": "simulation",
    "simulation.npc_types": "simulation",
    "rendering.render_central2": "rendering",
    "persistence.save_load": "persistence",
    "emx.emx_system": "emx",
    "emx.emx_composites": "emx",
    "emx.emx_sphere": "emx",
}
RESTRICTED_FROM_IMPORTS = {
    "runtime": {"app_runtime"},
    "simulation": {"simulation", "doctrine", "npc_types"},
    "rendering": {"render_central2"},
    "persistence": {"save_load"},
    "emx": {"emx_system", "emx_composites", "emx_sphere"},
}


@dataclass(frozen=True)
class AuditIssue:
    path: str
    line: int
    message: str


def _is_skipped(relative_path: Path) -> bool:
    return any(part in SKIP_DIRS for part in relative_path.parts)


def _is_legacy_shim(relative_path: Path) -> bool:
    return len(relative_path.parts) == 1 and relative_path.name in LEGACY_SHIMS


def _owning_package(relative_path: Path) -> str | None:
    if len(relative_path.parts) > 1:
        return relative_path.parts[0]
    return None


def _iter_import_targets(node: ast.AST) -> list[tuple[str, int]]:
    if isinstance(node, ast.Import):
        return [(alias.name, node.lineno) for alias in node.names]

    if isinstance(node, ast.ImportFrom):
        if node.level != 0 or not node.module:
            return []

        targets = [(node.module, node.lineno)]
        if node.module in RESTRICTED_FROM_IMPORTS:
            for alias in node.names:
                targets.append((f"{node.module}.{alias.name}", node.lineno))
        return targets

    return []


def audit_repo(root: Path) -> list[AuditIssue]:
    issues: list[AuditIssue] = []

    for path in root.rglob("*.py"):
        relative_path = path.relative_to(root)
        if _is_skipped(relative_path) or _is_legacy_shim(relative_path):
            continue

        owning_package = _owning_package(relative_path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            for target, lineno in _iter_import_targets(node):
                if target in ROOT_ONLY_SHIM_MODULES:
                    issues.append(
                        AuditIssue(
                            path=str(relative_path),
                            line=lineno,
                            message=f"imports root compatibility shim `{target}`",
                        )
                    )
                    continue

                if target in RESTRICTED_MODULES:
                    allowed_package = RESTRICTED_MODULES[target]
                    if owning_package != allowed_package:
                        issues.append(
                            AuditIssue(
                                path=str(relative_path),
                                line=lineno,
                                message=f"imports direct implementation module `{target}` instead of package surface",
                            )
                        )

    return sorted(issues, key=lambda issue: (issue.path, issue.line, issue.message))


def main() -> int:
    issues = audit_repo(REPO_ROOT)
    print("Architecture audit")
    print(f"Repository: {REPO_ROOT}")

    if not issues:
        print("PASS: no forbidden root-shim or direct implementation imports detected.")
        return 0

    print(f"FAIL: {len(issues)} issue(s) detected.")
    for issue in issues:
        print(f"{issue.path}:{issue.line}: {issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
