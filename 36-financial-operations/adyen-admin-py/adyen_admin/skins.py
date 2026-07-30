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
Adyen skin management.

Replaces lib/adyen-admin/skin.rb.  Key Ruby→Python translations:

  Adyen::Admin::Skin             → Skin  (pydantic model)
  Skin.all / all_local           → SkinManager.all() / all_local()
  Skin.all_remote                → SkinManager.all_remote()
  Skin.find(code)                → SkinManager.find(code)
  skin.download                  → SkinManager.download(skin)
  skin.upload                    → SkinManager.upload(path)
  skin.publish                   → SkinManager.publish(skin)
  Skin.purge_cache               → SkinManager.purge_cache()
  Skin.compress / decompress     → _compress_skin / _decompress_skin
  rubyzip                        → stdlib zipfile

HTML scraping uses basic string / BeautifulSoup-lite patterns since
Adyen's admin panel emits simple HTML tables for the skin list.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (mirror Ruby SKINS constant)
# ---------------------------------------------------------------------------

_SKINS_PATH = "/ca/ca/skin/overview.shtml"
_SKIN_DOWNLOAD_PATH = "/ca/ca/skin/download.shtml"
_SKIN_UPLOAD_PATH = "/ca/ca/skin/upload.shtml"
_SKIN_DEPLOY_PATH = "/ca/ca/skin/deploy.shtml"
_TEST_HPP_URL = "https://test.adyen.com/hpp/pay.shtml"

# Files and directories excluded from zip upload (mirrors Ruby gem patterns)
_EXCLUDE_PATTERNS = frozenset([
    ".DS_Store",
    "Thumbs.db",
    ".gitignore",
    "skin.yml",       # metadata file; not part of the uploaded skin
])


# ---------------------------------------------------------------------------
# Skin model
# ---------------------------------------------------------------------------


class Skin(BaseModel):
    """
    Represents an Adyen HPP skin.

    Mirrors the Ruby Skin struct.  A skin may be local-only (has a path on
    disk) or remote-only (fetched from the Adyen admin panel), or both.
    """

    code: str
    name: str
    path: Optional[Path] = None

    # populated from skin.yml when the skin is loaded from disk
    parent_skin_code: Optional[str] = None
    version: int = 0
    live_version: int = 0

    model_config = {"arbitrary_types_allowed": True}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Skin):
            return NotImplemented
        return self.code == other.code

    def __hash__(self) -> int:
        return hash(self.code)

    def __repr__(self) -> str:
        return f"Skin(code={self.code!r}, name={self.name!r})"

    @property
    def test_url(self) -> str:
        """URL for testing this skin on Adyen's test HPP."""
        return f"{_TEST_HPP_URL}?skinCode={self.code}"

    @classmethod
    def from_path(cls, path: Path) -> Skin:
        """
        Instantiate a Skin from a local skin directory.

        Reads skin.yml for metadata if present.  The directory name is
        expected to follow the pattern ``<name>-<8-char-code>`` (e.g.
        ``brand-qaJKoAMQ``).
        """
        code, name = _parse_skin_dir(path)
        meta = _read_skin_yml(path)
        return cls(
            code=code,
            name=name,
            path=path,
            parent_skin_code=meta.get("parent_skin_code"),
            version=int(meta.get("version", 0)),
            live_version=int(meta.get("live_version", 0)),
        )


# ---------------------------------------------------------------------------
# Skin manager
# ---------------------------------------------------------------------------


class SkinManager:
    """
    CRUD operations for Adyen skins.

    Manages a local skin directory (default_path) and communicates with the
    Adyen admin panel via the injected AdyenAdminClient.
    """

    def __init__(
        self,
        client: Any,  # AdyenAdminClient — avoid circular import with Any
        default_path: Path | str = ".",
    ) -> None:
        from adyen_admin.client import AdyenAdminClient  # local to avoid cycle
        self._client: AdyenAdminClient = client
        self.default_path = Path(default_path)
        self._remote_cache: Optional[list[Skin]] = None

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def purge_cache(self) -> None:
        self._remote_cache = None

    # ------------------------------------------------------------------
    # Listing skins
    # ------------------------------------------------------------------

    def all_local(self) -> list[Skin]:
        """Return all skins found under self.default_path."""
        skins = []
        for entry in sorted(self.default_path.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                try:
                    skins.append(Skin.from_path(entry))
                except ValueError:
                    logger.debug("Skipping non-skin directory: %s", entry)
        return skins

    def all_remote(self) -> list[Skin]:
        """
        Return all skins registered in the Adyen admin panel.

        Scrapes the skin overview HTML table.  Results are cached until
        purge_cache() is called.
        """
        if self._remote_cache is not None:
            return self._remote_cache

        resp = self._client.get(_SKINS_PATH)
        skins = _parse_skins_table(resp.text, self.default_path)
        self._remote_cache = skins
        return skins

    def all(self) -> list[Skin]:
        """
        Return the union of local and remote skins.

        Local-only skins (no remote counterpart) are frozen to prevent
        accidental upload — mirrors the Ruby gem behaviour.
        """
        local = {s.code: s for s in self.all_local()}
        remote = {s.code: s for s in self.all_remote()}

        result: list[Skin] = []
        for code, skin in {**local, **remote}.items():
            if code in local and code not in remote:
                # local-only: mark as frozen (set live_version = -1 sentinel)
                frozen = skin.model_copy(update={"live_version": -1})
                result.append(frozen)
            elif code in remote:
                merged = remote[code].model_copy(
                    update={"path": local[code].path} if code in local else {}
                )
                result.append(merged)
        return result

    def find(self, code: str) -> Optional[Skin]:
        """Return the skin with the given code from the remote list, or None."""
        return next(
            (s for s in self.all_remote() if s.code == code),
            None,
        )

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download(self, skin: Skin, dest_dir: Path | str | None = None) -> Path:
        """
        Download a skin ZIP from the Adyen admin panel.

        Returns the path to the downloaded ZIP file.  Mirrors skin.download
        from the Ruby gem.
        """
        dest = Path(dest_dir) if dest_dir else Path.cwd()
        zip_path = dest / f"{skin.code}.zip"

        resp = self._client.get(_SKIN_DOWNLOAD_PATH, skinCode=skin.code)
        zip_path.write_bytes(resp.content)
        logger.info("Downloaded skin %s → %s", skin.code, zip_path)
        return zip_path

    def decompress(self, zip_path: Path, dest_dir: Path | str | None = None) -> Path:
        """
        Decompress a skin ZIP into a directory.

        Mirrors Skin.decompile from Ruby gem.
        """
        dest = Path(dest_dir) if dest_dir else zip_path.parent
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest)
            # Determine the top-level directory name inside the ZIP
            top_dirs = {Path(name).parts[0] for name in zf.namelist() if name}
        skin_dir = dest / top_dirs.pop() if top_dirs else dest
        logger.info("Decompressed %s → %s", zip_path, skin_dir)
        return skin_dir

    def download_and_extract(
        self, skin: Skin, dest_dir: Path | str | None = None
    ) -> Path:
        """Download and immediately decompress a skin."""
        zip_path = self.download(skin, dest_dir)
        skin_dir = self.decompress(zip_path, dest_dir)
        zip_path.unlink()
        return skin_dir

    # ------------------------------------------------------------------
    # Compress and upload
    # ------------------------------------------------------------------

    def compress(self, skin_path: Path | str) -> Path:
        """
        Compress a local skin directory into a ZIP suitable for Adyen upload.

        Replicates Skin.compress from the Ruby gem, including:
        - Parent skin inheritance: parent files are included as a base layer;
          child files override them.
        - File exclusion: .DS_Store, skin.yml, etc. are omitted.

        Returns the path to the created ZIP file.
        """
        skin_path = Path(skin_path)
        skin = Skin.from_path(skin_path)
        zip_path = Path.cwd() / f"{skin.code}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # If there is a parent skin, include its files first (lower priority)
            if skin.parent_skin_code:
                parent_path = path_from_code(skin.parent_skin_code, self.default_path)
                if parent_path and parent_path.exists():
                    _add_skin_dir_to_zip(zf, parent_path, skin.code)

            # Add (or override with) the child skin's own files
            _add_skin_dir_to_zip(zf, skin_path, skin.code)

        logger.info("Compressed %s → %s", skin_path, zip_path)
        return zip_path

    def upload(self, skin_path: Path | str) -> Skin:
        """
        Compress and upload a skin directory to the Adyen admin panel.

        After a successful upload, increments the version in skin.yml and
        refreshes the remote cache.  Mirrors skin.upload from Ruby.
        """
        skin_path = Path(skin_path)
        skin = Skin.from_path(skin_path)
        zip_path = self.compress(skin_path)

        try:
            with zip_path.open("rb") as fh:
                self._client.post(
                    _SKIN_UPLOAD_PATH,
                    data={"skinCode": skin.code},
                    files={"skinZip": (zip_path.name, fh, "application/zip")},
                )
        finally:
            zip_path.unlink(missing_ok=True)

        # Bump version and persist to skin.yml
        new_version = skin.version + 1
        _write_skin_yml(skin_path, {"version": new_version})
        self.purge_cache()

        logger.info("Uploaded skin %s (version %d)", skin.code, new_version)
        return skin.model_copy(update={"version": new_version})

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(self, skin: Skin) -> None:
        """
        Promote the test version of a skin to live.

        Mirrors skin.publish from Ruby.
        """
        self._client.post(_SKIN_DEPLOY_PATH, data={"skinCode": skin.code})
        self.purge_cache()
        logger.info("Published skin %s to live", skin.code)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def path_from_code(
    code: str, base_path: Path | str = "."
) -> Optional[Path]:
    """
    Return the local path for a skin code by scanning base_path.

    Mirrors Skin.path_from_code from the Ruby gem.
    """
    base = Path(base_path)
    for entry in base.iterdir():
        if entry.is_dir() and entry.name.endswith(f"-{code}"):
            return entry
        if entry.is_dir() and entry.name == code:
            return entry
    return None


def _parse_skin_dir(path: Path) -> tuple[str, str]:
    """
    Extract (code, name) from a skin directory name.

    Expects ``<name>-<code>`` where code is 8 alphanumeric chars, or just
    ``<code>`` as a fallback.
    """
    name = path.name
    # Pattern: something-ABCD1234
    m = re.match(r"^(.+)-([A-Za-z0-9]{8})$", name)
    if m:
        return m.group(2), m.group(1)
    # Fallback: treat the whole name as the code
    return name, name


def _read_skin_yml(path: Path) -> dict[str, Any]:
    yml_path = path / "skin.yml"
    if yml_path.exists():
        return yaml.safe_load(yml_path.read_text()) or {}
    return {}


def _write_skin_yml(skin_path: Path, data: dict[str, Any]) -> None:
    yml_path = skin_path / "skin.yml"
    existing = _read_skin_yml(skin_path)
    existing.update(data)
    yml_path.write_text(yaml.dump(existing, default_flow_style=False))


def _add_skin_dir_to_zip(
    zf: zipfile.ZipFile, skin_path: Path, archive_root: str
) -> None:
    """
    Add all files from skin_path into the ZIP under the archive_root prefix.

    Files matching _EXCLUDE_PATTERNS are omitted.
    """
    for file_path in sorted(skin_path.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.name in _EXCLUDE_PATTERNS:
            continue
        rel = file_path.relative_to(skin_path)
        archive_name = str(Path(archive_root) / rel)
        zf.write(file_path, archive_name)


def _parse_skins_table(html: str, default_path: Path) -> list[Skin]:
    """
    Parse the Adyen skin overview HTML table into a list of Skin objects.

    The table rows have the pattern:
      <td>SKIN_CODE</td><td>SKIN_NAME</td><td>...</td>
    This is a minimal scraper — no external HTML parser required.
    """
    skins: list[Skin] = []
    # Match rows that contain an 8-char alphanumeric skin code
    row_re = re.compile(
        r"<tr[^>]*>.*?<td[^>]*>\s*([A-Za-z0-9]{8})\s*</td>"
        r"\s*<td[^>]*>\s*([^<]+?)\s*</td>",
        re.DOTALL,
    )
    for m in row_re.finditer(html):
        code = m.group(1).strip()
        name = m.group(2).strip()
        local_path = path_from_code(code, default_path)
        skins.append(Skin(code=code, name=name, path=local_path))

    logger.debug("Parsed %d skins from admin HTML", len(skins))
    return skins
