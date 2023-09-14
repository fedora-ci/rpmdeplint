# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import shutil
import time
from pathlib import Path

from data_setup import run_rpmdeplint
from rpmfluff import SimpleRpmBuild, SourceFile
from rpmfluff.yumrepobuild import YumRepoBuild

from rpmdeplint.repodata import cache_entry_path


def expected_cache_path(repodir: str, name: str, old=False) -> Path:
    """
    For the test repo located in *repodir*, return the path within the
    rpmdeplint cache where we expect the metadata file with given suffix
    to appear after rpmdeplint has downloaded it.
    """
    file = next(Path(repodir, "repodata").glob(f"*-{name}.*"))
    checksum = file.name.split("-", 1)[0]
    return cache_entry_path(checksum) / file.name if old else cache_entry_path(checksum)


def test_finds_all_problems(request, dir_server):
    p_newer = SimpleRpmBuild("a", "5.0", "1", ["i386"])
    p_with_content = SimpleRpmBuild("b", "0.1", "1", ["i386"])
    p_with_content.add_installed_file(
        installPath="usr/share/thing",
        sourceFile=SourceFile("thing", "content\n"),
    )
    p_old_soname = SimpleRpmBuild("c", "0.1", "1", ["i386"])
    p_old_soname.add_provides("libfoo.so.4")
    p_depending = SimpleRpmBuild("d", "0.1", "1", ["i386"])
    p_depending.add_requires("libfoo.so.4")
    repo_packages = [p_newer, p_with_content, p_old_soname, p_depending]
    baserepo = YumRepoBuild(repo_packages)
    baserepo.make("i386")
    dir_server.basepath = baserepo.repoDir

    p_older = SimpleRpmBuild("a", "4.0", "1", ["i386"])
    p_older.make()
    p_broken = SimpleRpmBuild("e", "1.0", "1", ["i386"])
    p_broken.add_requires("doesnotexist")
    p_broken.make()
    p_with_different_content = SimpleRpmBuild("f", "0.1", "1", ["i386"])
    p_with_different_content.add_installed_file(
        installPath="usr/share/thing",
        sourceFile=SourceFile("thing", "different content\n"),
    )
    p_with_different_content.make()
    p_soname_changed = SimpleRpmBuild("c", "0.2", "1", ["i386"])
    p_soname_changed.add_provides("libfoo.so.5")
    p_soname_changed.make()
    test_packages = [p_older, p_broken, p_with_different_content, p_soname_changed]

    def cleanUp():
        shutil.rmtree(baserepo.repoDir)
        for p in repo_packages + test_packages:
            shutil.rmtree(p.get_base_dir())

    request.addfinalizer(cleanUp)

    exitcode, out, err = run_rpmdeplint(
        ["rpmdeplint", "check", f"--repo=base,{dir_server.url}"]
        + [p.get_built_rpm("i386") for p in test_packages]
    )
    assert exitcode == 3
    assert err == (
        "Problems with dependency set:\n"
        "nothing provides doesnotexist needed by e-1.0-1.i386\n"
        "Dependency problems with repos:\n"
        "package d-0.1-1.i386 requires libfoo.so.4, but none of the providers can be installed\n"
        "Undeclared file conflicts:\n"
        "f-0.1-1.i386 provides /usr/share/thing which is also provided by b-0.1-1.i386\n"
        "Upgrade problems:\n"
        "a-4.0-1.i386 would be upgraded by a-5.0-1.i386 from repo base\n"
    )


def test_guesses_arch_when_combined_with_noarch_package(request, dir_server):
    # A more realistic case is an archful package with a noarch subpackage,
    # but rpmfluff currently can't produce that.
    p_noarch = SimpleRpmBuild("a", "0.1", "1", ["noarch"])
    p_noarch.add_requires("libfoo.so.4")
    p_noarch.make()
    p_archful = SimpleRpmBuild("b", "0.1", "1", ["i386"])
    p_archful.add_requires("libfoo.so.4")
    p_archful.make()

    baserepo = YumRepoBuild([])
    baserepo.make("i386")
    dir_server.basepath = baserepo.repoDir

    def cleanUp():
        shutil.rmtree(baserepo.repoDir)
        shutil.rmtree(p_noarch.get_base_dir())
        shutil.rmtree(p_archful.get_base_dir())

    request.addfinalizer(cleanUp)

    exitcode, out, err = run_rpmdeplint(
        [
            "rpmdeplint",
            "check",
            f"--repo=base,{dir_server.url}",
            p_noarch.get_built_rpm("noarch"),
            p_archful.get_built_rpm("i386"),
        ]
    )
    assert exitcode == 3, err
    assert err == (
        "Problems with dependency set:\n"
        "nothing provides libfoo.so.4 needed by a-0.1-1.noarch\n"
        "nothing provides libfoo.so.4 needed by b-0.1-1.i386\n"
    )


def test_cache_is_used_when_available(request, dir_server):
    p1 = SimpleRpmBuild("a", "0.1", "1", ["i386"])
    baserepo = YumRepoBuild((p1,))
    baserepo.make("i386")
    dir_server.basepath = baserepo.repoDir

    def cleanUp():
        shutil.rmtree(baserepo.repoDir)
        shutil.rmtree(p1.get_base_dir())

    request.addfinalizer(cleanUp)

    # Assuming cache is cleaned first
    assert dir_server.num_requests == 0

    run_rpmdeplint(
        [
            "rpmdeplint",
            "check",
            f"--repo=base,{dir_server.url}",
            p1.get_built_rpm("i386"),
        ]
    )

    cache_path = expected_cache_path(baserepo.repoDir, "primary.xml")
    assert cache_path.exists()
    original_cache_mtime = cache_path.stat().st_mtime

    # A single run of rpmdeplint with a clean cache should expect network
    # requests for - repomd.xml, primary.xml.gz and filelists.xml.gz. Requiring
    # a total of 3
    assert dir_server.num_requests == 3

    run_rpmdeplint(
        [
            "rpmdeplint",
            "check",
            f"--repo=base,{dir_server.url}",
            p1.get_built_rpm("i386"),
        ]
    )

    new_cache_mtime = cache_path.stat().st_mtime
    assert new_cache_mtime > original_cache_mtime

    # Executing 2 subprocesses should expect 4 requests if repodata cache is
    # functioning correctly. A single request for each file in the repo
    # - repomd.xml, primary.xml.gz, filelists.xml.gz, with an additional
    # request from the second process checking metadata. The additional
    # single request shows that the files are skipped in the second process
    assert dir_server.num_requests == 4


def test_cache_doesnt_grow_unboundedly(request, dir_server):
    os.environ["RPMDEPLINT_EXPIRY_SECONDS"] = "1"

    p1 = SimpleRpmBuild("a", "0.1", "1", ["i386"])
    firstrepo = YumRepoBuild((p1,))
    firstrepo.make("i386")
    dir_server.basepath = firstrepo.repoDir

    def cleanup():
        shutil.rmtree(firstrepo.repoDir)
        shutil.rmtree(p1.get_base_dir())

    request.addfinalizer(cleanup)

    exitcode, out, err = run_rpmdeplint(
        [
            "rpmdeplint",
            "check",
            f"--repo=base,{dir_server.url}",
            p1.get_built_rpm("i386"),
        ]
    )
    assert exitcode == 0

    first_primary_cache_path = expected_cache_path(firstrepo.repoDir, "primary.xml")
    first_filelists_cache_path = expected_cache_path(firstrepo.repoDir, "filelists.xml")

    assert first_primary_cache_path.exists()
    assert first_filelists_cache_path.exists()

    p2 = SimpleRpmBuild("b", "0.1", "1", ["i386"])
    secondrepo = YumRepoBuild((p2,))
    secondrepo.make("i386")
    dir_server.basepath = secondrepo.repoDir

    def cleanup2():
        shutil.rmtree(secondrepo.repoDir)
        shutil.rmtree(p2.get_base_dir())

    request.addfinalizer(cleanup2)

    # ensure time period of cache has expired
    time.sleep(2)

    exitcode, out, err = run_rpmdeplint(
        [
            "rpmdeplint",
            "check",
            f"--repo=base,{dir_server.url}",
            p2.get_built_rpm("i386"),
        ]
    )
    assert exitcode == 0

    second_primary_cache_path = expected_cache_path(secondrepo.repoDir, "primary.xml")
    second_filelists_cache_path = expected_cache_path(
        secondrepo.repoDir, "filelists.xml"
    )

    # Ensure the cache only has files from the second one
    assert not first_primary_cache_path.exists()
    assert not first_filelists_cache_path.exists()
    assert second_primary_cache_path.exists()
    assert second_filelists_cache_path.exists()


def test_migrates_old_cache_layout(request, dir_server):
    p1 = SimpleRpmBuild("a", "0.1", "1", ["i386"])
    repo = YumRepoBuild([p1])
    repo.make("i386")
    dir_server.basepath = repo.repoDir

    def cleanUp():
        shutil.rmtree(repo.repoDir)
        shutil.rmtree(p1.get_base_dir())

    request.addfinalizer(cleanUp)

    old_cache_path = expected_cache_path(repo.repoDir, "primary.xml", old=True)
    new_cache_path = expected_cache_path(repo.repoDir, "primary.xml")

    # Simulate the old cache path left over from an older version of rpmdeplint
    old_cache_path.parent.mkdir(parents=True)
    old_cache_path.write_text("lol\n")

    exitcode, out, err = run_rpmdeplint(
        [
            "rpmdeplint",
            "check",
            f"--repo=base,{dir_server.url}",
            p1.get_built_rpm("i386"),
        ]
    )
    assert exitcode == 0
    assert err == ""
    assert not old_cache_path.exists()
    assert new_cache_path.is_file()


def test_prints_error_on_repo_download_failure(request):
    # Specifically we don't want an unhandled exception, because that triggers abrt.
    test_tool_rpm = SimpleRpmBuild("test-tool", "10", "3.el6", ["x86_64"])
    test_tool_rpm.make()

    def cleanUp():
        shutil.rmtree(test_tool_rpm.get_base_dir())

    request.addfinalizer(cleanUp)

    exitcode, out, err = run_rpmdeplint(
        [
            "rpmdeplint",
            "check",
            "--repo=broken,http://notexist.example/",
            test_tool_rpm.get_built_rpm("x86_64"),
        ]
    )

    assert exitcode == 1
    assert err.startswith("Failed to download repodata")
    assert "Traceback" not in err


def test_prints_error_on_repodata_file_download_failure(request, dir_server):
    # Similar to the above, but in this case repomd.xml works but
    # primary.xml.gz is broken. We test this case specifically, because the
    # code paths for fetching repomd.xml and the other repodata files are
    # separate.
    p1 = SimpleRpmBuild("test-tool", "10", "3.el6", ["x86_64"])
    p1.add_requires("unsatisfied")
    repo = YumRepoBuild([p1])
    repo.make("x86_64")
    for repodata_filename in Path(repo.repoDir, "repodata").glob("*primary*"):
        repodata_filename.unlink()
    dir_server.basepath = repo.repoDir

    def cleanUp():
        shutil.rmtree(repo.repoDir)
        shutil.rmtree(p1.get_base_dir())

    request.addfinalizer(cleanUp)

    exitcode, out, err = run_rpmdeplint(
        [
            "rpmdeplint",
            "check",
            f"--repo=base,{dir_server.url}",
            p1.get_built_rpm("x86_64"),
        ]
    )

    assert exitcode == 1
    assert err.startswith("Failed to download repodata")
    assert "404" in err
    assert "Traceback" not in err
