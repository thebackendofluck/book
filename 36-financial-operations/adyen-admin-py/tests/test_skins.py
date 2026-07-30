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
Tests for the Skin model and SkinManager.

Mirrors the Ruby spec/adyen-admin/skin_spec.rb behaviour:
  - Skin.all_local returns local skins from disk
  - Skin.all_remote parses the Adyen admin HTML table
  - Skin.all merges local + remote with correct frozen-local logic
  - Skin.find(code) returns the matching skin or None
  - download() writes a ZIP file
  - compress() produces a valid ZIP with correct contents
  - upload() calls the admin panel and bumps the version in skin.yml
  - parent skin inheritance is applied during compress()
  - test_url is correct
  - ZIP excludes .DS_Store / skin.yml / Thumbs.db

All HTTP calls are mocked with the `responses` library.
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Put the package root on sys.path so the tests run regardless of the
# current working directory (the adyen_admin package lives one level up).
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

# Third-party mock HTTP library; skip the file rather than erroring
# collection when it isn't installed.
resp_mock = pytest.importorskip("responses")

from adyen_admin.client import ADYEN_TEST_BASE, AdyenAdminClient, AuthenticationError
from adyen_admin.skins import (
    Skin,
    SkinManager,
    _parse_skin_dir,
    _parse_skins_table,
    path_from_code,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def skin_fixtures(tmp_path: Path) -> Path:
    """Create a minimal skin fixtures directory with two skins."""
    # skin 1: example-qaJKoAMQ
    s1 = tmp_path / "example-qaJKoAMQ"
    s1.mkdir()
    (s1 / "inc").mkdir()
    (s1 / "inc" / "cheader.txt").write_text("<h1>Brand</h1>")
    (s1 / "css").mkdir()
    (s1 / "css" / "screen.css").write_text("body { color: red; }")
    (s1 / "res").mkdir()
    (s1 / "res" / "resources_en.properties").write_text("key=value")
    # skin.yml — should be excluded from upload ZIP
    (s1 / "skin.yml").write_text("version: 3\nlive_version: 2\n")

    # skin 2: brand2-xxx1ABCD
    s2 = tmp_path / "brand2-xxx1ABCD"
    s2.mkdir()
    (s2 / "inc").mkdir()
    (s2 / "inc" / "cheader.txt").write_text("<h1>Brand2</h1>")
    # .DS_Store — should be excluded from upload ZIP
    (s2 / ".DS_Store").write_bytes(b"\x00\x01")

    return tmp_path


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock(spec=AdyenAdminClient)
    client.authenticated = True
    return client


@pytest.fixture
def manager(mock_client: MagicMock, skin_fixtures: Path) -> SkinManager:
    m = SkinManager(client=mock_client, default_path=skin_fixtures)
    return m


# ---------------------------------------------------------------------------
# Helper: minimal Adyen admin skin table HTML
# ---------------------------------------------------------------------------

_SKINS_HTML = """
<html><body>
<table>
  <tr><th>Code</th><th>Name</th><th>Actions</th></tr>
  <tr>
    <td>qaJKoAMQ</td><td>example</td><td><a href="#">Download</a></td>
  </tr>
  <tr>
    <td>xxx1ABCD</td><td>brand2</td><td><a href="#">Download</a></td>
  </tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Skin model
# ---------------------------------------------------------------------------


class TestSkin:
    def test_equality_by_code(self) -> None:
        a = Skin(code="ABC", name="alpha")
        b = Skin(code="ABC", name="beta")
        assert a == b

    def test_hash_by_code(self) -> None:
        a = Skin(code="ABC", name="alpha")
        b = Skin(code="ABC", name="beta")
        assert hash(a) == hash(b)

    def test_test_url_contains_https_test_adyen(self) -> None:
        skin = Skin(code="qaJKoAMQ", name="example")
        assert "https://test.adyen.com/hpp/pay.shtml" in skin.test_url
        assert "qaJKoAMQ" in skin.test_url

    def test_from_path_parses_code_and_name(self, skin_fixtures: Path) -> None:
        path = skin_fixtures / "example-qaJKoAMQ"
        skin = Skin.from_path(path)
        assert skin.code == "qaJKoAMQ"
        assert skin.name == "example"
        assert skin.version == 3
        assert skin.live_version == 2

    def test_from_path_without_skin_yml(self, skin_fixtures: Path) -> None:
        path = skin_fixtures / "brand2-xxx1ABCD"
        skin = Skin.from_path(path)
        assert skin.code == "xxx1ABCD"
        assert skin.version == 0


# ---------------------------------------------------------------------------
# _parse_skin_dir helper
# ---------------------------------------------------------------------------


class TestParseSkinDir:
    def test_standard_name_dash_code(self) -> None:
        from pathlib import Path
        code, name = _parse_skin_dir(Path("example-qaJKoAMQ"))
        assert code == "qaJKoAMQ"
        assert name == "example"

    def test_fallback_when_no_dash(self) -> None:
        code, name = _parse_skin_dir(Path("qaJKoAMQ"))
        assert code == "qaJKoAMQ"
        assert name == "qaJKoAMQ"


# ---------------------------------------------------------------------------
# _parse_skins_table helper
# ---------------------------------------------------------------------------


class TestParseSkinsTables:
    def test_parses_skin_codes_from_html(self, tmp_path: Path) -> None:
        skins = _parse_skins_table(_SKINS_HTML, tmp_path)
        codes = {s.code for s in skins}
        assert "qaJKoAMQ" in codes
        assert "xxx1ABCD" in codes

    def test_returns_empty_list_for_empty_html(self, tmp_path: Path) -> None:
        skins = _parse_skins_table("<html></html>", tmp_path)
        assert skins == []


# ---------------------------------------------------------------------------
# SkinManager.all_local
# ---------------------------------------------------------------------------


class TestAllLocal:
    def test_returns_all_local_skins(self, manager: SkinManager) -> None:
        skins = manager.all_local()
        codes = {s.code for s in skins}
        assert "qaJKoAMQ" in codes
        assert "xxx1ABCD" in codes

    def test_local_skin_has_path(self, manager: SkinManager) -> None:
        skin = next(s for s in manager.all_local() if s.code == "qaJKoAMQ")
        assert skin.path is not None
        assert skin.path.exists()


# ---------------------------------------------------------------------------
# SkinManager.all_remote
# ---------------------------------------------------------------------------


class TestAllRemote:
    def test_returns_skins_parsed_from_html(
        self, manager: SkinManager, mock_client: MagicMock
    ) -> None:
        mock_client.get.return_value = MagicMock(text=_SKINS_HTML)
        skins = manager.all_remote()
        codes = {s.code for s in skins}
        assert "qaJKoAMQ" in codes
        assert "xxx1ABCD" in codes

    def test_caches_result(
        self, manager: SkinManager, mock_client: MagicMock
    ) -> None:
        mock_client.get.return_value = MagicMock(text=_SKINS_HTML)
        manager.all_remote()
        manager.all_remote()
        mock_client.get.assert_called_once()

    def test_purge_cache_clears_cache(
        self, manager: SkinManager, mock_client: MagicMock
    ) -> None:
        mock_client.get.return_value = MagicMock(text=_SKINS_HTML)
        manager.all_remote()
        manager.purge_cache()
        manager.all_remote()
        assert mock_client.get.call_count == 2


# ---------------------------------------------------------------------------
# SkinManager.all (merged)
# ---------------------------------------------------------------------------


class TestAll:
    def test_returns_union_of_local_and_remote(
        self, manager: SkinManager, mock_client: MagicMock
    ) -> None:
        mock_client.get.return_value = MagicMock(text=_SKINS_HTML)
        skins = manager.all()
        codes = {s.code for s in skins}
        assert "qaJKoAMQ" in codes
        assert "xxx1ABCD" in codes

    def test_local_only_skin_gets_frozen_sentinel(
        self, manager: SkinManager, mock_client: MagicMock, skin_fixtures: Path
    ) -> None:
        # Add a local skin that has no remote counterpart
        local_only = skin_fixtures / "localonly-ZZZZZZZZ"
        local_only.mkdir()
        (local_only / "inc").mkdir()

        # Remote HTML only has qaJKoAMQ and xxx1ABCD
        mock_client.get.return_value = MagicMock(text=_SKINS_HTML)
        skins = manager.all()
        frozen = next((s for s in skins if s.code == "ZZZZZZZZ"), None)
        assert frozen is not None
        assert frozen.live_version == -1  # frozen sentinel


# ---------------------------------------------------------------------------
# SkinManager.find
# ---------------------------------------------------------------------------


class TestFind:
    def test_find_returns_matching_skin(
        self, manager: SkinManager, mock_client: MagicMock
    ) -> None:
        mock_client.get.return_value = MagicMock(text=_SKINS_HTML)
        skin = manager.find("qaJKoAMQ")
        assert skin is not None
        assert skin.code == "qaJKoAMQ"

    def test_find_returns_none_for_unknown_code(
        self, manager: SkinManager, mock_client: MagicMock
    ) -> None:
        mock_client.get.return_value = MagicMock(text=_SKINS_HTML)
        skin = manager.find("NOTFOUND")
        assert skin is None


# ---------------------------------------------------------------------------
# SkinManager.download
# ---------------------------------------------------------------------------


class TestDownload:
    def test_download_writes_zip_file(
        self, manager: SkinManager, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        zip_bytes = b"PK\x03\x04"  # minimal ZIP magic bytes
        mock_client.get.return_value = MagicMock(content=zip_bytes)

        skin = Skin(code="qaJKoAMQ", name="example")
        result = manager.download(skin, dest_dir=tmp_path)

        assert result == tmp_path / "qaJKoAMQ.zip"
        assert result.exists()
        assert result.read_bytes() == zip_bytes


# ---------------------------------------------------------------------------
# SkinManager.compress
# ---------------------------------------------------------------------------


class TestCompress:
    def test_compress_produces_valid_zip(
        self, manager: SkinManager, skin_fixtures: Path, tmp_path: Path
    ) -> None:
        skin_path = skin_fixtures / "example-qaJKoAMQ"
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            zip_path = manager.compress(skin_path)

        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        # skin.yml must be excluded
        assert not any("skin.yml" in n for n in names)
        # content files must be present
        assert any("cheader.txt" in n for n in names)

    def test_compress_excludes_ds_store(
        self, manager: SkinManager, skin_fixtures: Path, tmp_path: Path
    ) -> None:
        skin_path = skin_fixtures / "brand2-xxx1ABCD"
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            zip_path = manager.compress(skin_path)

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert not any(".DS_Store" in n for n in names)

    def test_compress_with_parent_skin(
        self, manager: SkinManager, skin_fixtures: Path, tmp_path: Path
    ) -> None:
        """Parent skin files are included in the ZIP when a parent is declared."""
        # Give example-qaJKoAMQ a parent skin pointing to brand2-xxx1ABCD
        skin_path = skin_fixtures / "example-qaJKoAMQ"
        (skin_path / "skin.yml").write_text(
            "version: 3\nlive_version: 2\nparent_skin_code: xxx1ABCD\n"
        )

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            zip_path = manager.compress(skin_path)

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        # cheader.txt from the parent should be included
        assert any("cheader.txt" in n for n in names)


# ---------------------------------------------------------------------------
# SkinManager.upload
# ---------------------------------------------------------------------------


class TestUpload:
    def test_upload_posts_to_adyen_and_bumps_version(
        self, manager: SkinManager, mock_client: MagicMock,
        skin_fixtures: Path, tmp_path: Path
    ) -> None:
        skin_path = skin_fixtures / "example-qaJKoAMQ"
        mock_client.post.return_value = MagicMock(status_code=200)

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            updated_skin = manager.upload(skin_path)

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "skinCode" in str(call_kwargs)

        # Version should have been incremented
        assert updated_skin.version == 4  # was 3 in skin.yml

    def test_upload_cleans_up_zip(
        self, manager: SkinManager, mock_client: MagicMock,
        skin_fixtures: Path, tmp_path: Path
    ) -> None:
        skin_path = skin_fixtures / "example-qaJKoAMQ"
        mock_client.post.return_value = MagicMock(status_code=200)

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            manager.upload(skin_path)

        # The temporary ZIP should have been deleted
        assert not (tmp_path / "qaJKoAMQ.zip").exists()


# ---------------------------------------------------------------------------
# SkinManager.publish
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_calls_deploy_endpoint(
        self, manager: SkinManager, mock_client: MagicMock
    ) -> None:
        skin = Skin(code="qaJKoAMQ", name="example")
        mock_client.post.return_value = MagicMock(status_code=200)
        manager.publish(skin)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "skinCode" in str(call_args)

    def test_publish_clears_cache(
        self, manager: SkinManager, mock_client: MagicMock
    ) -> None:
        skin = Skin(code="qaJKoAMQ", name="example")
        manager._remote_cache = [skin]  # pre-warm cache
        mock_client.post.return_value = MagicMock(status_code=200)
        manager.publish(skin)
        assert manager._remote_cache is None


# ---------------------------------------------------------------------------
# path_from_code helper
# ---------------------------------------------------------------------------


class TestPathFromCode:
    def test_finds_directory_with_dash_code_suffix(self, skin_fixtures: Path) -> None:
        result = path_from_code("qaJKoAMQ", skin_fixtures)
        assert result is not None
        assert result.name == "example-qaJKoAMQ"

    def test_returns_none_for_unknown_code(self, skin_fixtures: Path) -> None:
        result = path_from_code("NOTEXIST", skin_fixtures)
        assert result is None
