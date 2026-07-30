# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Adyen Admin CLI — replaces the Ruby gem binary.

Provides the same upload/publish workflow as push.rb (adyen-skins/push.rb)
but as a proper click-based CLI.

Usage:
    adyen-admin upload all
    adyen-admin upload SKINCODE1 SKINCODE2
    adyen-admin publish all
    adyen-admin publish SKINCODE1 SKINCODE2

    adyen-admin download SKINCODE

The credentials are loaded from credentials.yml (or credentials.yml.example
as a fallback), or from an explicit --credentials-file option.

Rate-limiting: a 5-second delay is added between sequential skin uploads to
avoid Adyen throttling, matching the original push.rb behaviour.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Optional

import click

from adyen_admin.client import AdyenAdmin, AuthenticationError
from adyen_admin.credentials import load_credentials
from adyen_admin.skins import Skin, SkinManager, path_from_code

logger = logging.getLogger(__name__)

_UPLOAD_RATE_LIMIT_SECONDS = 5


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--credentials-file",
    "-c",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to credentials.yml (defaults to ./credentials.yml)",
)
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, credentials_file: Optional[str], debug: bool) -> None:
    """Adyen Admin skin management CLI."""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["credentials_file"] = credentials_file


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("targets", nargs=-1, required=True)
@click.option(
    "--skins-path",
    default=".",
    show_default=True,
    help="Directory containing local skin directories.",
)
@click.option(
    "--shared-path",
    default="shared",
    show_default=True,
    help="Directory containing shared skin resources (merged before upload).",
)
@click.pass_context
def upload(
    ctx: click.Context,
    targets: tuple[str, ...],
    skins_path: str,
    shared_path: str,
) -> None:
    """
    Upload skins to the Adyen admin panel.

    TARGETS is either 'all' or a list of skin codes.

    Before uploading, shared resources from --shared-path are merged into a
    temporary copy of each skin (mirrors the preprocess() step in push.rb).
    """
    admin = _login(ctx)
    manager = admin.skins
    manager.default_path = Path(skins_path)

    skins_to_upload = _resolve_targets(manager, targets, skins_path)

    with click.progressbar(skins_to_upload, label="Uploading skins") as bar:
        for i, skin in enumerate(bar):
            if skin.path and str(skin.path) == "./shared":
                click.echo(f"  Skipping shared directory: {skin.path}")
                continue
            try:
                click.echo(f"\nUploading {skin.name} [{skin.path}]")
                _preprocess_and_upload(admin, skin, shared_path)
            except Exception as exc:
                click.echo(f"  ERROR: {exc}", err=True)

            if i < len(skins_to_upload) - 1:
                time.sleep(_UPLOAD_RATE_LIMIT_SECONDS)


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("targets", nargs=-1, required=True)
@click.option(
    "--skins-path",
    default=".",
    show_default=True,
    help="Directory containing local skin directories.",
)
@click.pass_context
def publish(
    ctx: click.Context,
    targets: tuple[str, ...],
    skins_path: str,
) -> None:
    """
    Publish test skins to live.

    TARGETS is either 'all' or a list of skin codes.
    """
    admin = _login(ctx)
    manager = admin.skins
    manager.default_path = Path(skins_path)

    skins_to_publish = _resolve_targets(manager, targets, skins_path)

    for skin in skins_to_publish:
        click.echo(f"Publishing {skin.name} [{skin.code}]")
        try:
            manager.publish(skin)
        except Exception as exc:
            click.echo(f"  ERROR: {exc}", err=True)


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("skin_code")
@click.option(
    "--dest",
    "-d",
    default=".",
    show_default=True,
    type=click.Path(file_okay=False),
    help="Destination directory for the downloaded skin.",
)
@click.option("--extract", is_flag=True, help="Extract the ZIP after download.")
@click.pass_context
def download(
    ctx: click.Context,
    skin_code: str,
    dest: str,
    extract: bool,
) -> None:
    """Download a skin ZIP from the Adyen admin panel."""
    admin = _login(ctx)
    skin = admin.skins.find(skin_code)
    if skin is None:
        click.echo(f"Skin not found: {skin_code!r}", err=True)
        sys.exit(1)

    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    if extract:
        out = admin.skins.download_and_extract(skin, dest_path)
        click.echo(f"Downloaded and extracted → {out}")
    else:
        out = admin.skins.download(skin, dest_path)
        click.echo(f"Downloaded → {out}")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@cli.command(name="list")
@click.option("--remote", is_flag=True, help="List remote skins from Adyen.")
@click.option("--local", "local_only", is_flag=True, help="List local skins only.")
@click.option(
    "--skins-path",
    default=".",
    show_default=True,
    help="Directory containing local skin directories.",
)
@click.pass_context
def list_skins(
    ctx: click.Context,
    remote: bool,
    local_only: bool,
    skins_path: str,
) -> None:
    """List available skins."""
    admin = _login(ctx)
    manager = admin.skins
    manager.default_path = Path(skins_path)

    if remote:
        skins = manager.all_remote()
        label = "Remote skins"
    elif local_only:
        skins = manager.all_local()
        label = "Local skins"
    else:
        skins = manager.all()
        label = "All skins"

    click.echo(f"{label} ({len(skins)}):")
    for skin in skins:
        path_str = str(skin.path) if skin.path else "(no local path)"
        click.echo(f"  {skin.code}  {skin.name:<30}  {path_str}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _login(ctx: click.Context) -> AdyenAdmin:
    creds_file = ctx.obj.get("credentials_file")
    try:
        creds = load_credentials(creds_file)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    admin = AdyenAdmin.from_credentials(
        account=creds.account,
        user=creds.user,
        password=creds.password,
    )
    try:
        admin.login()
    except AuthenticationError as exc:
        click.echo(f"Authentication failed: {exc}", err=True)
        sys.exit(1)
    return admin


def _resolve_targets(
    manager: SkinManager,
    targets: tuple[str, ...],
    skins_path: str,
) -> list[Skin]:
    if targets == ("all",):
        return manager.all_local()

    result = []
    for code in targets:
        skin = manager.find(code)
        if skin is None:
            # Fall back to local lookup
            local_path = path_from_code(code, skins_path)
            if local_path:
                skin = Skin(code=code, name=local_path.name, path=local_path)
            else:
                click.echo(f"  WARNING: Skin not found: {code!r}", err=True)
                continue
        result.append(skin)
    return result


def _preprocess_and_upload(
    admin: AdyenAdmin, skin: Skin, shared_path: str
) -> None:
    """
    Merge shared resources into a temporary skin directory, then upload.

    Mirrors the preprocess() function from push.rb:
    - Copies the skin to a temp directory.
    - Appends shared *.properties files to the skin's res/ locale files.
    - Uploads the temporary copy.
    - Cleans up the temp directory.
    """
    import shutil
    import tempfile

    if skin.path is None:
        raise ValueError(f"Skin {skin.code!r} has no local path to upload from")

    with tempfile.TemporaryDirectory(prefix="adyen-skin-") as tmp_dir:
        tmp_path = Path(tmp_dir) / skin.path.name
        shutil.copytree(skin.path, tmp_path)

        shared = Path(shared_path) / "res"
        if shared.exists():
            res_dir = tmp_path / "res"
            res_dir.mkdir(exist_ok=True)
            for props_file in sorted(shared.glob("*.properties")):
                target = res_dir / props_file.name
                shared_content = props_file.read_text()
                with target.open("a") as fh:
                    fh.write(f"\n\n#defaults\n{shared_content}")
                logger.debug("Merged %s into %s", props_file, target)

        admin.skins.default_path = Path(tmp_dir)
        admin.skins.upload(tmp_path)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
