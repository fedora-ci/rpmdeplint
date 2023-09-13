# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from unittest import TestCase

from rpmfluff import SimpleRpmBuild
from rpmfluff.yumrepobuild import YumRepoBuild

from rpmdeplint import DependencyAnalyzer
from rpmdeplint.repodata import Repo


class TestDependencyAnalyzer(TestCase):
    def test_repos(self):
        lemon = SimpleRpmBuild("lemon", "1", "3", ["noarch"])
        lemon.add_provides("lemon-juice")
        lemon.add_provides("lemon-zest")
        self.addCleanup(lemon.clean)
        peeler = SimpleRpmBuild("peeler", "4", "0", ["x86_64"])
        self.addCleanup(peeler.clean)
        cinnamon = SimpleRpmBuild("cinnamon", "3", "0", ["noarch"])
        self.addCleanup(cinnamon.clean)
        apple_pie = SimpleRpmBuild("apple-pie", "1.9", "1", ["x86_64"])
        apple_pie.add_requires("apple-lib")
        apple_pie.add_requires("lemon-juice")
        apple_pie.add_requires("cinnamon >= 2.0")
        self.addCleanup(apple_pie.clean)
        base_1_repo = YumRepoBuild([lemon, peeler, cinnamon, apple_pie])
        base_1_repo.make("x86_64", "noarch")

        apple = SimpleRpmBuild("apple", "4.9", "3", ["x86_64"])
        apple.add_provides("apple-lib")
        apple.add_requires("peeler")
        apple.add_requires("lemon-juice")
        apple.make()
        self.addCleanup(apple.clean)
        lemon_meringue_pie = SimpleRpmBuild("lemon-meringue-pie", "1", "0", ["x86_64"])
        lemon_meringue_pie.add_requires("lemon-zest")
        lemon_meringue_pie.add_requires("lemon-juice")
        lemon_meringue_pie.add_requires("egg-whites")
        lemon_meringue_pie.add_requires("egg-yolks")
        lemon_meringue_pie.add_requires("sugar")
        lemon_meringue_pie.make()
        self.addCleanup(lemon_meringue_pie.clean)

        da = DependencyAnalyzer(
            repos=[Repo(repo_name="base_1", baseurl=base_1_repo.repoDir)],
            packages=[
                apple.get_built_rpm("x86_64"),
                lemon_meringue_pie.get_built_rpm("x86_64"),
            ],
        )

        dependency_set = da.try_to_install_all()
        assert False is dependency_set.is_ok
        assert len(dependency_set.overall_problems) == 1
        assert [
            "nothing provides egg-whites needed by lemon-meringue-pie-1-0.x86_64",
            "nothing provides egg-whites needed by lemon-meringue-pie-1-0.x86_64",
        ] == dependency_set.package_dependencies["lemon-meringue-pie-1-0.x86_64"][
            "problems"
        ]

        eggs = SimpleRpmBuild("eggs", "1", "3", ["noarch"])
        eggs.add_provides("egg-whites")
        eggs.add_provides("egg-yolks")
        self.addCleanup(eggs.clean)
        sugar = SimpleRpmBuild("sugar", "4", "0", ["x86_64"])
        self.addCleanup(sugar.clean)
        base_2_repo = YumRepoBuild([eggs, sugar])
        base_2_repo.make("x86_64", "noarch")

        da = DependencyAnalyzer(
            repos=[
                Repo(repo_name="base_1", baseurl=base_1_repo.repoDir),
                Repo(repo_name="base_2", baseurl=base_2_repo.repoDir),
            ],
            packages=[
                apple.get_built_rpm("x86_64"),
                lemon_meringue_pie.get_built_rpm("x86_64"),
            ],
        )

        dependency_set = da.try_to_install_all()
        assert True is dependency_set.is_ok
        assert (
            len(
                dependency_set.package_dependencies["lemon-meringue-pie-1-0.x86_64"][
                    "dependencies"
                ]
            )
            == 4
        )
        assert (
            len(
                dependency_set.package_dependencies["apple-4.9-3.x86_64"][
                    "dependencies"
                ]
            )
            == 3
        )
