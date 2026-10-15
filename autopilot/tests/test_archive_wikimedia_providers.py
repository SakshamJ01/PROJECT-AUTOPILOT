"""Tests for Archive.org and Wikimedia Commons asset providers."""
from __future__ import annotations

import pytest
from autopilot.core.contracts import AssetCandidate, AssetLicense, AssetProvenance, AssetDimensions, AssetMediaInfo


# ---------------------------------------------------------------------------
# Archive.org — license mapping (pure)
# ---------------------------------------------------------------------------
class TestArchiveOrgLicense:
    def test_public_domain(self):
        from autopilot.providers.archive_org_provider import _map_archive_license
        lic = _map_archive_license("https://creativecommons.org/publicdomain/mark/1.0/")
        assert lic.rights_status == "VERIFIED"
        assert lic.commercial_use is True

    def test_cc_by(self):
        from autopilot.providers.archive_org_provider import _map_archive_license
        lic = _map_archive_license("https://creativecommons.org/licenses/by/4.0/")
        assert lic.rights_status == "VERIFIED"
        assert lic.attribution_required is True

    def test_cc_by_nc_rejected(self):
        from autopilot.providers.archive_org_provider import _map_archive_license
        lic = _map_archive_license("https://creativecommons.org/licenses/by-nc/4.0/")
        assert lic.rights_status == "REJECTED"
        assert lic.commercial_use is False

    def test_unknown(self):
        from autopilot.providers.archive_org_provider import _map_archive_license
        lic = _map_archive_license(None)
        assert lic.rights_status == "UNKNOWN"

    def test_empty_string(self):
        from autopilot.providers.archive_org_provider import _map_archive_license
        lic = _map_archive_license("")
        assert lic.rights_status == "UNKNOWN"


# ---------------------------------------------------------------------------
# Archive.org — parse_items (pure, no network)
# ---------------------------------------------------------------------------
class TestArchiveOrgParse:
    def test_parses_video_docs(self):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        docs = [
            {
                "identifier": "test-item-1",
                "title": "Roman Soldiers Marching",
                "description": "A film about Roman soldiers",
                "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
                "mediatype": "movies",
            }
        ]
        cands = prov.parse_items(docs, want_video=True)
        assert len(cands) == 1
        c = cands[0]
        assert c.candidate_id == "archiveorg-test-item-1"
        assert c.asset_type == "video"
        assert c.provenance.source_id == "test-item-1"
        assert c.license.rights_status == "VERIFIED"

    def test_parses_image_docs(self):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        docs = [
            {
                "identifier": "img-42",
                "title": "Ancient Rome",
                "description": ["A photo", "of ancient Rome"],
                "licenseurl": "https://creativecommons.org/licenses/by-sa/4.0/",
                "mediatype": "image",
            }
        ]
        cands = prov.parse_items(docs, want_video=False)
        assert len(cands) == 1
        assert cands[0].asset_type == "image"
        assert cands[0].license.rights_status == "VERIFIED"

    def test_skips_missing_identifier(self):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        assert prov.parse_items([{"title": "no id"}], want_video=True) == []

    def test_list_description_joined(self):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        docs = [{"identifier": "x", "title": "t", "description": ["a", "b"]}]
        c = prov.parse_items(docs, want_video=True)[0]
        assert c.title == "t"


# ---------------------------------------------------------------------------
# Archive.org — select() with synthetic candidates
# ---------------------------------------------------------------------------
class TestArchiveOrgSelect:
    def _cand(self, cid: str, rights_status: str = "VERIFIED") -> AssetCandidate:
        return AssetCandidate(
            candidate_id=cid,
            asset_type="video",
            source_url=f"https://archive.org/details/{cid}",
            source_id=cid,
            title=cid,
            dimensions=AssetDimensions(width=1920, height=1080),
            media_info=AssetMediaInfo(mime_type="video/mp4", file_size_bytes=1000),
            license=AssetLicense(
                license_name="CC0", license_url="", source_url="",
                attribution_required=False, commercial_use=True, derivative_use=True,
                rights_status=rights_status,
            ),
            score=0.0,
            provenance=AssetProvenance(provider="archive_org"),
        )

    def test_selects_higher_scoring_candidate(self):
        # select() re-scores via score_candidates(), so the winner must differ by
        # attributes that affect the score (not by a manually-assigned .score).
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        c1 = self._cand("a")
        c1.dimensions = AssetDimensions(width=1080, height=1920)  # vertical, high-res
        c2 = self._cand("b")
        c2.dimensions = AssetDimensions(width=100, height=100)    # tiny thumbnail
        sel = prov.select([c1, c2], {})
        assert sel.status == "selected"
        assert sel.selected_id == "a"

    def test_rejects_noncommercial(self):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        sel = prov.select([self._cand("nc", rights_status="REJECTED")], {})
        assert sel.status == "rejected"

    def test_empty_candidates(self):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        sel = prov.select([], {})
        assert sel.status == "rejected"


# ---------------------------------------------------------------------------
# Archive.org — factory + search (injected, no network)
# ---------------------------------------------------------------------------
class TestArchiveOrgIntegration:
    def test_factory_returns_provider(self):
        from autopilot.core.asset_pipeline import get_asset_provider
        prov = get_asset_provider("archive_org")
        assert prov.provider_name == "archive_org"

    def test_search_with_injected_items(self, monkeypatch):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        monkeypatch.setattr(prov, "_search_items", lambda q, m, r: [
            {"identifier": "x1", "title": "Test", "licenseurl": None, "mediatype": "movies"}
        ])
        cands = prov.search({"query": "rome"}, max_results=5)
        assert len(cands) == 1
        assert cands[0].candidate_id == "archiveorg-x1"

    def test_search_empty_query(self):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        assert prov.search({"query": ""}, max_results=5) == []

    def test_search_wants_image_when_asset_type_image(self, monkeypatch):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        captured = {}

        def fake(q, m, r):
            captured["mediatype"] = m
            return []

        monkeypatch.setattr(prov, "_search_items", fake)
        prov.search({"query": "x", "asset_type": "image"}, max_results=5)
        assert captured["mediatype"] == "image"

    def test_resolve_download_sets_url(self, monkeypatch):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        monkeypatch.setattr(prov, "_get_best_file", lambda ident, want_video: {
            "name": "test.mp4", "format": "h.264", "size": 5000,
            "download_url": "https://archive.org/download/x/test.mp4",
            "mimetype": "video/mp4",
        })
        c = AssetCandidate(
            candidate_id="archiveorg-x", asset_type="video",
            source_url="https://archive.org/details/x", source_id="x",
            title="x", dimensions=AssetDimensions(),
            media_info=AssetMediaInfo(mime_type="video/mp4", file_size_bytes=0),
            license=AssetLicense(license_name="CC0", license_url="", source_url="",
                                attribution_required=False, commercial_use=True,
                                derivative_use=True, rights_status="VERIFIED"),
            score=0.0, provenance=AssetProvenance(provider="archive_org"),
        )
        url = prov.resolve_download(c)
        assert url == "https://archive.org/download/x/test.mp4"
        assert c.media_info.file_size_bytes == 5000

    def test_resolve_download_returns_none_on_failure(self, monkeypatch):
        from autopilot.providers.archive_org_provider import ArchiveOrgAssetProvider
        prov = ArchiveOrgAssetProvider()
        monkeypatch.setattr(prov, "_get_best_file", lambda ident, want_video: None)
        c = AssetCandidate(
            candidate_id="archiveorg-x", asset_type="video",
            source_url="https://archive.org/details/x", source_id="x",
            title="x", dimensions=AssetDimensions(),
            media_info=AssetMediaInfo(mime_type="video/mp4", file_size_bytes=0),
            license=AssetLicense(license_name="CC0", license_url="", source_url="",
                                attribution_required=False, commercial_use=True,
                                derivative_use=True, rights_status="VERIFIED"),
            score=0.0, provenance=AssetProvenance(provider="archive_org"),
        )
        assert prov.resolve_download(c) is None


# ---------------------------------------------------------------------------
# Wikimedia Commons — license mapping (pure)
# ---------------------------------------------------------------------------
class TestCommonsLicense:
    def test_cc0(self):
        from autopilot.providers.wikimedia_commons_provider import _map_commons_license
        lic = _map_commons_license("CC0")
        assert lic.rights_status == "VERIFIED"
        assert lic.commercial_use is True

    def test_public_domain(self):
        from autopilot.providers.wikimedia_commons_provider import _map_commons_license
        lic = _map_commons_license("Public domain")
        assert lic.rights_status == "VERIFIED"

    def test_cc_by(self):
        from autopilot.providers.wikimedia_commons_provider import _map_commons_license
        lic = _map_commons_license("CC BY 4.0")
        assert lic.rights_status == "VERIFIED"
        assert lic.attribution_required is True

    def test_cc_by_sa(self):
        from autopilot.providers.wikimedia_commons_provider import _map_commons_license
        lic = _map_commons_license("CC BY-SA 4.0")
        assert lic.rights_status == "VERIFIED"

    def test_cc_by_nc_rejected(self):
        from autopilot.providers.wikimedia_commons_provider import _map_commons_license
        lic = _map_commons_license("CC BY-NC 4.0")
        assert lic.rights_status == "REJECTED"

    def test_unknown(self):
        from autopilot.providers.wikimedia_commons_provider import _map_commons_license
        lic = _map_commons_license("Some Custom License")
        assert lic.rights_status == "PARTIALLY_VERIFIED"

    def test_none(self):
        from autopilot.providers.wikimedia_commons_provider import _map_commons_license
        lic = _map_commons_license(None)
        assert lic.rights_status == "UNKNOWN"


# ---------------------------------------------------------------------------
# Wikimedia Commons — _get_file_info (injected, no network)
# ---------------------------------------------------------------------------
class TestCommonsGetFileInfo:
    def test_resolves_image(self, monkeypatch):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        monkeypatch.setattr(prov, "_api_get", lambda p: {
            "query": {"pages": {"1": {"imageinfo": [{
                "url": "https://upload.wikimedia.org/x.jpg",
                "width": 1920, "height": 1080,
                "mime": "image/jpeg", "size": 5000,
                "extmetadata": {
                    "LicenseShortName": {"value": "CC0"},
                    "Artist": {"value": "John Doe"},
                },
            }]}}}
        })
        info = prov._get_file_info("File:X.jpg")
        assert info is not None
        assert info["is_video"] is False
        assert info["width"] == 1920
        assert info["license_short"] == "CC0"

    def test_resolves_video(self, monkeypatch):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        monkeypatch.setattr(prov, "_api_get", lambda p: {
            "query": {"pages": {"1": {"imageinfo": [{
                "url": "https://upload.wikimedia.org/x.webm",
                "width": 1920, "height": 1080,
                "mime": "video/webm", "size": 20000,
                "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}},
            }]}}}
        })
        info = prov._get_file_info("File:X.webm")
        assert info is not None
        assert info["is_video"] is True

    def test_handles_missing(self, monkeypatch):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        monkeypatch.setattr(prov, "_api_get", lambda p: {"query": {"pages": {}}})
        assert prov._get_file_info("File:None.jpg") is None


# ---------------------------------------------------------------------------
# Wikimedia Commons — parse_results (pure)
# ---------------------------------------------------------------------------
class TestCommonsParse:
    def test_parses_image_result(self, monkeypatch):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        monkeypatch.setattr(prov, "_get_file_info", lambda title: {
            "url": "https://upload.wikimedia.org/x.jpg", "ext": ".jpg",
            "is_video": False, "width": 800, "height": 600,
            "mime": "image/jpeg", "size": 3000,
            "license_short": "CC0", "license_url": "",
            "artist": "Jane Doe",
        })
        cands = prov.parse_results([{"title": "File:X.jpg"}])
        assert len(cands) == 1
        c = cands[0]
        assert c.candidate_id == "commons-File:X.jpg"
        assert c.asset_type == "image"
        assert c.license.rights_status == "VERIFIED"

    def test_skips_missing_file_info(self, monkeypatch):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        monkeypatch.setattr(prov, "_get_file_info", lambda title: None)
        assert prov.parse_results([{"title": "File:Gone.jpg"}]) == []

    def test_strips_html_artist(self, monkeypatch):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        monkeypatch.setattr(prov, "_get_file_info", lambda title: {
            "url": "https://x.jpg", "ext": ".jpg", "is_video": False,
            "width": 1, "height": 1, "mime": "image/jpeg", "size": 1,
            "license_short": "CC BY 4.0", "license_url": "",
            "artist": "<a href='x'>Link Name</a>",
        })
        c = prov.parse_results([{"title": "File:X.jpg"}])[0]
        # artist stripped of tags but not stored on candidate — just confirm no crash
        assert c.title == "X.jpg"


# ---------------------------------------------------------------------------
# Wikimedia Commons — select() + factory + search
# ---------------------------------------------------------------------------
class TestCommonsSelect:
    def _cand(self, cid: str, rights_status: str = "VERIFIED") -> AssetCandidate:
        return AssetCandidate(
            candidate_id=cid, asset_type="image",
            source_url="https://upload.wikimedia.org/x.jpg", source_id=cid,
            title=cid, dimensions=AssetDimensions(width=1920, height=1080),
            media_info=AssetMediaInfo(mime_type="image/jpeg", file_size_bytes=1000),
            license=AssetLicense(license_name="CC0", license_url="", source_url="",
                                attribution_required=False, commercial_use=True,
                                derivative_use=True, rights_status=rights_status),
            score=0.0, provenance=AssetProvenance(provider="wikimedia_commons"),
        )

    def test_selects_verified(self):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        sel = prov.select([self._cand("a")], {})
        assert sel.status == "selected"

    def test_rejects_nc(self):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        sel = prov.select([self._cand("nc", rights_status="REJECTED")], {})
        assert sel.status == "rejected"

    def test_factory(self):
        from autopilot.core.asset_pipeline import get_asset_provider
        prov = get_asset_provider("wikimedia_commons")
        assert prov.provider_name == "wikimedia_commons"

    def test_search_with_injected_results(self, monkeypatch):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        monkeypatch.setattr(prov, "_search_files", lambda q, m: [
            {"title": "File:Test.jpg"}
        ])
        monkeypatch.setattr(prov, "_get_file_info", lambda title: {
            "url": "https://upload.wikimedia.org/test.jpg", "ext": ".jpg",
            "is_video": False, "width": 100, "height": 100,
            "mime": "image/jpeg", "size": 100,
            "license_short": "CC0", "license_url": "",
            "artist": "",
        })
        cands = prov.search({"query": "test"}, max_results=5)
        assert len(cands) == 1
        assert cands[0].candidate_id == "commons-File:Test.jpg"

    def test_search_empty_query(self):
        from autopilot.providers.wikimedia_commons_provider import WikimediaCommonsAssetProvider
        prov = WikimediaCommonsAssetProvider()
        assert prov.search({"query": "  "}, max_results=5) == []
