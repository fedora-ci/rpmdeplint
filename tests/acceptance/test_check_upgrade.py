# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import shutil

from data_setup import run_rpmdeplint
from rpmfluff import SimpleRpmBuild
from rpmfluff.yumrepobuild import YumRepoBuild


def test_finds_newer_version_in_repo(request, dir_server):
    p2 = SimpleRpmBuild("anaconda", "19.31.123", "1.el7", ["noarch"])
    p2.add_subpackage("user-help")
    baserepo = YumRepoBuild([p2])
    baserepo.make("noarch")
    dir_server.basepath = baserepo.repoDir

    p1 = SimpleRpmBuild("anaconda-user-help", "7.2.2", "1.el7", ["noarch"])
    p1.make()

    def cleanUp():
        shutil.rmtree(baserepo.repoDir)
        p2.clean()
        p1.clean()

    request.addfinalizer(cleanUp)

    exitcode, out, err = run_rpmdeplint(
        [
            "rpmdeplint",
            "check-upgrade",
            f"--repo=base,{dir_server.url}",
            "--arch=x86_64",
            p1.get_built_rpm("noarch"),
        ]
    )
    assert exitcode == 3
    assert err == (
        "Upgrade problems:\n"
        "anaconda-user-help-7.2.2-1.el7.noarch would be upgraded by "
        "anaconda-user-help-19.31.123-1.el7.noarch from repo base\n"
    )


def test_finds_obsoleting_package_in_repo(request, dir_server):
    p2 = SimpleRpmBuild("b", "0.1", "2", ["i386"])
    p2.add_obsoletes("a < 0.1-2")
    baserepo = YumRepoBuild([p2])
    baserepo.make("i386")
    dir_server.basepath = baserepo.repoDir

    p1 = SimpleRpmBuild("a", "0.1", "1", ["i386"])
    p1.make()

    def cleanUp():
        shutil.rmtree(baserepo.repoDir)
        p2.clean()
        p1.clean()

    request.addfinalizer(cleanUp)

    exitcode, out, err = run_rpmdeplint(
        [
            "rpmdeplint",
            "check-upgrade",
            f"--repo=base,{dir_server.url}",
            p1.get_built_rpm("i386"),
        ]
    )
    assert exitcode == 3
    assert err == (
        "Upgrade problems:\n"
        "a-0.1-1.i386 would be obsoleted by b-0.1-2.i386 from repo base\n"
    )


def test_epoch(request, dir_server):
    p2 = SimpleRpmBuild("anaconda", "19.31.123", "1.el7", ["noarch"])
    p2.add_subpackage("user-help")
    baserepo = YumRepoBuild([p2])
    baserepo.make("noarch")
    dir_server.basepath = baserepo.repoDir

    p1 = SimpleRpmBuild("anaconda-user-help", "7.3.2", "1.el7", ["noarch"])
    p1.epoch = 1
    p1.make()

    def cleanUp():
        shutil.rmtree(baserepo.repoDir)
        p2.clean()
        p1.clean()

    request.addfinalizer(cleanUp)

    exitcode, out, err = run_rpmdeplint(
        [
            "rpmdeplint",
            "check-upgrade",
            f"--repo=base,{dir_server.url}",
            "--arch=x86_64",
            p1.get_built_rpm("noarch"),
        ]
    )
    assert exitcode == 0
    assert err == ""
