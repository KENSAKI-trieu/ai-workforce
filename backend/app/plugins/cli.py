"""Command line access to the plugin catalogue, for local development.

    python -m app.plugins.cli list
    python -m app.plugins.cli validate
    python -m app.plugins.cli show <plugin>
    python -m app.plugins.cli installed --tenant <uuid|domain>
    python -m app.plugins.cli install <plugin> --tenant <uuid|domain>
    python -m app.plugins.cli uninstall <plugin> --tenant <uuid|domain>
    python -m app.plugins.cli preview <plugin> --slot classifier

``--tenant`` takes a tenant domain as well as a UUID, because nobody types a UUID from
memory while trying a package out.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.models import Tenant
from app.plugins.loader import plugin_catalogue, plugin_root
from app.plugins.manifest import PluginManifestError
from app.plugins.resolver import (
    build_prompt_overlay,
    build_skill_restriction,
    installed_manifests,
)
from app.plugins.service import (
    PluginInstallError,
    install_plugin,
    list_installs,
    uninstall_plugin,
)
from app.services.agents.hr_prompts import default_prompt


def _resolve_tenant(db: Session, token: str) -> Tenant:
    try:
        tenant_id = uuid.UUID(token)
    except ValueError:
        tenant = db.query(Tenant).filter(Tenant.domain == token).first()
    else:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise SystemExit(f"No tenant matches '{token}'")
    return tenant


def _cmd_list(_: argparse.Namespace) -> int:
    catalogue = plugin_catalogue(refresh=True)
    if not catalogue:
        print(f"No plugin packages found under {plugin_root()}")
        return 0
    for manifest in catalogue.values():
        print(
            f"{manifest.name:<24} {manifest.version:<8} {manifest.target_role:<10} "
            f"{manifest.display_name}"
        )
    return 0


def _cmd_validate(_: argparse.Namespace) -> int:
    """Report every package that fails validation, rather than only the first."""
    root = plugin_root()
    paths = sorted(root.glob("*/plugin.yaml")) if root.is_dir() else []
    if not paths:
        print(f"No plugin packages found under {root}")
        return 0

    from app.plugins.loader import load_manifest_file

    failures = 0
    for path in paths:
        try:
            manifest = load_manifest_file(path)
        except PluginManifestError as exc:
            failures += 1
            print(f"FAIL {path.parent.name}: {exc}")
        else:
            print(f"OK   {manifest.name} {manifest.version}")
    return 1 if failures else 0


def _cmd_show(args: argparse.Namespace) -> int:
    manifest = plugin_catalogue(refresh=True).get(args.plugin)
    if manifest is None:
        raise SystemExit(f"No plugin package named '{args.plugin}'")
    metadata = manifest.public_metadata()
    for key, value in metadata.items():
        print(f"{key}: {value}")
    print(f"source: {manifest.source_path}")
    return 0


def _cmd_installed(args: argparse.Namespace) -> int:
    with SyncSessionLocal() as db:
        tenant = _resolve_tenant(db, args.tenant)
        rows = list_installs(db, tenant.id)
        if not rows:
            print(f"{tenant.domain} has no plugins installed")
            return 0
        for row in rows:
            print(f"{row.plugin_name:<24} {row.plugin_version:<8} {row.target_role}")
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    with SyncSessionLocal() as db:
        tenant = _resolve_tenant(db, args.tenant)
        try:
            row, manifest = install_plugin(db, tenant.id, args.plugin)
        except PluginInstallError as exc:
            raise SystemExit(str(exc)) from exc
        print(
            f"Installed {manifest.name} {manifest.version} "
            f"for {tenant.domain} (role {row.target_role})"
        )
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    with SyncSessionLocal() as db:
        tenant = _resolve_tenant(db, args.tenant)
        try:
            role = uninstall_plugin(db, tenant.id, args.plugin)
        except PluginInstallError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Uninstalled {args.plugin} from {tenant.domain} (role {role})")
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    """Show the prompt a tenant's installed packages actually produce for one slot."""
    with SyncSessionLocal() as db:
        tenant = _resolve_tenant(db, args.tenant)
        manifests = installed_manifests(db, tenant.id, args.role)
        overlay = build_prompt_overlay(manifests)
        restriction = build_skill_restriction(manifests)

    text = overlay.get(args.slot) or default_prompt(args.slot)
    print(f"--- {tenant.domain} / {args.role} / {args.slot} ---")
    print(text)
    if not restriction.is_empty:
        print("--- skill narrowing ---")
        allowed = "unrestricted" if restriction.allowed is None else sorted(restriction.allowed)
        print(f"allowed: {allowed}")
        print(f"denied: {sorted(restriction.denied)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.plugins.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List discovered packages").set_defaults(func=_cmd_list)
    sub.add_parser("validate", help="Validate every package").set_defaults(func=_cmd_validate)

    show = sub.add_parser("show", help="Show one package in detail")
    show.add_argument("plugin")
    show.set_defaults(func=_cmd_show)

    installed = sub.add_parser("installed", help="List a tenant's installed packages")
    installed.add_argument("--tenant", required=True)
    installed.set_defaults(func=_cmd_installed)

    install = sub.add_parser("install", help="Enable a package for a tenant")
    install.add_argument("plugin")
    install.add_argument("--tenant", required=True)
    install.set_defaults(func=_cmd_install)

    uninstall = sub.add_parser("uninstall", help="Disable a package for a tenant")
    uninstall.add_argument("plugin")
    uninstall.add_argument("--tenant", required=True)
    uninstall.set_defaults(func=_cmd_uninstall)

    preview = sub.add_parser("preview", help="Show the resolved prompt for a tenant")
    preview.add_argument("--tenant", required=True)
    preview.add_argument("--role", default="HR")
    preview.add_argument("--slot", default="classifier")
    preview.set_defaults(func=_cmd_preview)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Packages are authored in the customer's own language, so their names and prompts
    # carry non-ASCII text. A Windows console defaults to a legacy code page and raises
    # UnicodeEncodeError on the first Vietnamese or Japanese character printed.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
