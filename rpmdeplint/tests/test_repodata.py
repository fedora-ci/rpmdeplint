# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import platform
import shutil
from pathlib import Path

import pytest
from rpmfluff import SimpleRpmBuild
from rpmfluff.yumrepobuild import YumRepoBuild

from rpmdeplint.repodata import Repo, RepoDownloadError


@pytest.fixture()
def yumdir(tmpdir, monkeypatch):
    tmpdir.join("yum.conf").write("[main]\n")
    monkeypatch.setattr(Repo, "yum_main_config_path", str(tmpdir.join("yum.conf")))
    monkeypatch.setattr(
        Repo, "yum_repos_config_glob", str(tmpdir.join("yum.repos.d", "*.repo"))
    )
    return tmpdir


def test_loads_system_yum_repo_with_baseurl(yumdir):
    yumdir.join("yum.repos.d", "dummy.repo").write(
        "[dummy]\nname=Dummy\nbaseurl=http://example.invalid/dummy\n", ensure=True
    )

    repos = list(Repo.from_yum_config())
    assert len(repos) == 1
    assert repos[0].name == "dummy"
    assert repos[0].baseurl == "http://example.invalid/dummy"
    assert repos[0].metalink is None


def test_loads_system_yum_repo_with_metalink(yumdir):
    yumdir.join("yum.repos.d", "dummy.repo").write(
        "[dummy]\nname=Dummy\nmetalink=http://example.invalid/dummy\n", ensure=True
    )

    repos = list(Repo.from_yum_config())
    assert len(repos) == 1
    assert repos[0].name == "dummy"
    assert repos[0].baseurl is None
    assert repos[0].metalink == "http://example.invalid/dummy"


def test_loads_system_yum_repo_with_mirrorlist(yumdir):
    yumdir.join("yum.repos.d", "dummy.repo").write(
        "[dummy]\nname=Dummy\nmirrorlist=http://example.invalid/dummy\n", ensure=True
    )

    repos = list(Repo.from_yum_config())
    assert len(repos) == 1
    assert repos[0].name == "dummy"
    assert repos[0].baseurl is None
    assert repos[0].metalink == "http://example.invalid/dummy"


def test_loads_system_yum_repo_local(yumdir):
    local_repo = yumdir.join("local_repo").mkdir()
    yumdir.join("yum.repos.d", "dummy.repo").write(
        f"[dummy]\nname=Dummy\nbaseurl=file://{local_repo}\n", ensure=True
    )

    repos = list(Repo.from_yum_config())
    assert len(repos) == 1
    assert repos[0].name == "dummy"
    assert repos[0].baseurl == str(local_repo)
    assert repos[0].is_local
    assert repos[0].metalink is None


def test_skips_disabled_system_yum_repo(yumdir):
    yumdir.join("yum.repos.d", "dummy.repo").write(
        "[dummy]\nname=Dummy\nbaseurl=http://example.invalid/dummy\nenabled=0\n",
        ensure=True,
    )

    repos = list(Repo.from_yum_config())
    assert not repos


def test_loads_system_yum_repo_with_substitutions(yumdir, monkeypatch):
    yumdir.join("yum.repos.d", "dummy.repo").write(
        "[dummy]\nname=Dummy\nbaseurl=http://example.invalid/$releasever/$basearch/\n",
        ensure=True,
    )
    monkeypatch.setattr(
        "rpmdeplint.repodata.Repo.get_yumvars",
        lambda: {
            "releasever": "21",
            "basearch": "s390x",
        },
    )

    repos = list(Repo.from_yum_config())
    assert len(repos) == 1
    assert repos[0].name == "dummy"
    assert repos[0].baseurl == "http://example.invalid/21/s390x/"


def test_yumvars():
    # The expected values are dependent on the system where we are running, and
    # also will be different in mock for example (where neither yum nor dnf are
    # present). So the best we can do is touch the code path and makes sure it
    # gives back some values.
    yumvars = Repo.get_yumvars()
    if Path("/usr/bin/dnf").is_file():
        # The common case on developer's machines
        assert yumvars["arch"] == platform.machine()
        assert yumvars["basearch"] == platform.machine()
        assert int(yumvars["releasever"])
    else:
        # Everywhere else, just assume it's fine
        assert "arch" in yumvars
        assert "basearch" in yumvars
        assert "releasever" in yumvars


def test_bad_repo_url_raises_error(yumdir):
    yumdir.join("yum.repos.d", "dummy.repo").write(
        "[dummy]\nname=Dummy\nbaseurl=http://example.invalid/dummy\nenabled=1\n",
        ensure=True,
    )

    repos = list(Repo.from_yum_config())
    assert len(repos) == 1
    with pytest.raises(RepoDownloadError) as rde:
        repos[0].download_repodata()
    assert "Cannot download repomd.xml" in str(rde.value)
    assert "repo_name='dummy'" in str(rde.value)


def test_skip_if_unavailable_is_obeyed(yumdir):
    yumdir.join("yum.repos.d", "dummy.repo").write(
        "[dummy]\nname=Dummy\nbaseurl=http://example.invalid/dummy\n"
        "enabled=1\nskip_if_unavailable=1\n",
        ensure=True,
    )

    repos = list(Repo.from_yum_config())
    assert len(repos) == 1
    assert repos[0].name == "dummy"
    assert repos[0].skip_if_unavailable is True


def test_download_repodata_from_local_repo(request):
    satori = SimpleRpmBuild("satori", "1", "3", ["noarch"])
    repobuild = YumRepoBuild([satori])
    repobuild.make("noarch")

    def cleanUp():
        shutil.rmtree(repobuild.repoDir)
        satori.clean()

    request.addfinalizer(cleanUp)

    repo = Repo(repo_name="dummy", baseurl=repobuild.repoDir)
    assert repo.is_local
    repo.download_repodata()
    assert repo.repomd
    assert repo.primary_checksum
    assert repo.primary_url
    assert repo.primary
    assert repo.filelists_checksum
    assert repo.filelists_url
    assert repo.filelists
