# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.


from collections import defaultdict
from collections.abc import Iterable
from logging import getLogger
from typing import Optional

import rpm
from solv import (
    Dataiterator,
    Job,
    Pool,
    Problem,
    Selection,
    XSolvable,
    xfopen_fd,
)
from solv import (
    Repo as SolvRepo,
)

logger = getLogger(__name__)


installonlypkgs = [
    # The default 'installonlypkgs' from dnf
    # https://github.com/rpm-software-management/dnf/blob/dnf-2.5.1-1/dnf/const.py.in#L28
    "kernel",
    "kernel-PAE",
    "installonlypkg(kernel)",
    "installonlypkg(kernel-module)",
    "installonlypkg(vm)",
    # Additional names which yum 3.4.3 (RHEL7) has in its default 'installonlypkgs'
    # https://github.com/rpm-software-management/yum/blob/cf8a5669165e958d56157abf40d0cdd552c8fbf9/yum/config.py#L650
    "kernel-bigmem",
    "kernel-enterprise",
    "kernel-smp",
    "kernel-modules",
    "kernel-debug",
    "kernel-unsupported",
    "kernel-source",
    "kernel-devel",
    "kernel-PAE-debug",
]


class UnreadablePackageError(Exception):
    """
    Raised if an RPM package cannot be read from disk (it's corrupted, or the
    file is not a valid RPM package, etc).
    """


class DependencySet:
    """
    Contains dependency information from trying to install the packages under test.
    """

    def __init__(self) -> None:
        self._packagedeps: defaultdict = defaultdict(
            lambda: {"dependencies": [], "problems": []}
        )
        self._packages_with_problems: set[str] = set()
        self._overall_problems: set[str] = set()

    def add_package(
        self, pkg: XSolvable, dependencies: Iterable, problems: list[Problem]
    ):
        nevra: str = str(pkg)
        self._packagedeps[nevra]["dependencies"].extend(map(str, dependencies))
        if problems:
            all_problems = []
            # For each problem, find all the problematic RPM "rules" which
            # lead to the problem and also include them in
            # the `overall_problems` description.
            for problem in problems:
                all_problems.append(str(problem))
                all_problems.extend(
                    rule.info().problemstr() for rule in problem.findallproblemrules()
                )
            self._packagedeps[nevra]["problems"].extend(all_problems)
            self._packages_with_problems.add(nevra)
            self._overall_problems.update(all_problems)

    @property
    def is_ok(self) -> bool:
        return len(self._overall_problems) == 0

    @property
    def packages(self) -> list[str]:
        return sorted(self._packagedeps.keys())

    @property
    def overall_problems(self) -> list[str]:
        """
        List of str dependency problems found (if any)
        """
        return sorted(self._overall_problems)

    @property
    def packages_with_problems(self) -> list[str]:
        """
        List of :py:class:`solv.Solvable` which had dependency problems
        """
        return sorted(self._packages_with_problems)

    @property
    def package_dependencies(self) -> dict[str, dict[str, list]]:
        """
        Dict in the form {package: {'dependencies': list of packages, 'problems': list of problems}}
        """
        return dict(self._packagedeps)


class DependencyAnalyzer:
    """An object which checks packages against provided repos
    for dependency satisfiability.

    Construct an instance for a particular set of packages you want to test,
    with the repos you want to test against. Then call the individual checking
    methods to perform each check.
    """

    def __init__(
        self,
        repos: Iterable,
        packages: Iterable[str],
        arch: Optional[str] = None,
    ):
        """
        :param repos: An iterable of :py:class:`rpmdeplint.repodata.Repo` instances
        :param packages: An iterable of RPM package paths to be tested
        """
        # delayed import to avoid circular dependency
        from rpmdeplint.repodata import RepoDownloadError

        self.pool = Pool()
        self.pool.setarch(arch)

        #: List of :py:class:`solv.Solvable` to be tested (corresponding to *packages* parameter)
        self.solvables: list[XSolvable] = []
        self.commandline_repo = self.pool.add_repo("@commandline")
        for rpmpath in packages:
            solvable = self.commandline_repo.add_rpm(rpmpath)
            if solvable is None:
                # pool.errstr is already prefixed with the filename
                raise UnreadablePackageError(
                    f"Failed to read package: {self.pool.errstr}"
                )
            self.solvables.append(solvable)

        self.repos_by_name = (
            {}
        )  #: Mapping of {repo name: :py:class:`rpmdeplint.repodata.Repo`}
        for repo in repos:
            try:
                repo.download_repodata()
            except RepoDownloadError as e:
                if repo.skip_if_unavailable:
                    logger.warning("Skipping repo %s: %s", repo.name, e)
                    continue
                raise
            solv_repo = self.pool.add_repo(repo.name)
            # solv.xfopen does not accept unicode filenames on Python 2
            solv_repo.add_rpmmd(
                xfopen_fd(repo.primary_urls[0], repo.primary.fileno()), None
            )
            solv_repo.add_rpmmd(
                xfopen_fd(repo.filelists_urls[0], repo.filelists.fileno()),
                None,
                SolvRepo.REPO_EXTEND_SOLVABLES,
            )
            self.repos_by_name[repo.name] = repo

        self.pool.addfileprovides()
        self.pool.createwhatprovides()

        # Special handling for "installonly" packages: we create jobs to mark
        # installonly package names as "multiversion" and then set those as
        # pool jobs, which means the jobs are automatically applied whenever we
        # run the solver on this pool.
        multiversion_jobs = []
        for name in installonlypkgs:
            selection = self.pool.select(name, Selection.SELECTION_PROVIDES)
            multiversion_jobs.extend(selection.jobs(Job.SOLVER_MULTIVERSION))
        self.pool.setpooljobs(multiversion_jobs)

    # Context manager protocol is only implemented for backwards compatibility.
    # There are actually no resources to acquire or release.

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        return

    def download_package_header(self, solvable: XSolvable) -> str:
        if solvable in self.solvables:
            # It's a package under test, nothing to download
            return solvable.lookup_location()[0]
        href = solvable.lookup_location()[0]
        baseurl = solvable.lookup_str(self.pool.str2id("solvable:mediabase"))
        repo = self.repos_by_name[solvable.repo.name]
        return repo.download_package_header(href, baseurl)

    def try_to_install_all(self) -> DependencySet:
        """
        Try to solve the goal of installing each of the packages under test,
        starting from an empty package set.

        :return: dependency set
        """
        solver = self.pool.Solver()
        ds = DependencySet()
        for solvable in self.solvables:
            logger.debug("Solving install jobs for %s", solvable)
            jobs = solvable.Selection().jobs(Job.SOLVER_INSTALL)
            if problems := solver.solve(jobs):
                ds.add_package(solvable, [], problems)
            else:
                ds.add_package(solvable, solver.transaction().newsolvables(), [])
        return ds

    def _select_obsoleted_by(self, solvables: Iterable[XSolvable]) -> Selection:
        """
        Returns a Selection matching every solvable which is "obsoleted"
        by some solvable in the given list -- either due to an explicit
        Obsoletes relationship, or because we have a solvable with the same
        name with a higher epoch-version-release.
        """
        # Start with an empty selection.
        sel = self.pool.Selection()
        for solvable in solvables:
            # Select every solvable with the same name and lower EVR.
            # XXX are there some special cases with arch-noarch upgrades which this does not handle?
            sel.add(
                self.pool.select(
                    f"{solvable.name}.{solvable.arch} < {solvable.evr}",
                    Selection.SELECTION_NAME
                    | Selection.SELECTION_DOTARCH
                    | Selection.SELECTION_REL,
                )
            )
            for obsoletes_rel in solvable.lookup_deparray(
                self.pool.str2id("solvable:obsoletes")
            ):
                # Select every solvable matching the obsoletes relationship by name.
                sel.add(obsoletes_rel.Selection_name())
        return sel

    def find_repoclosure_problems(self) -> list[str]:
        """
        Checks for any package in the repos which would have unsatisfied
        dependencies, if the packages under test were added to the repos.

        This applies some extra constraints to prevent the solver from finding
        a solution which involves downgrading or installing an older package,
        which is technically a valid solution but is not expected if the
        packages are supposed to be updates.

        :return: List of str problem descriptions if any problems were found
        """
        problems = []
        solver = self.pool.Solver()
        # This selection matches packages obsoleted by our packages under test.
        obs_sel = self._select_obsoleted_by(self.solvables)
        # This selection matches packages obsoleted by other existing packages in the repo.
        existing_obs_sel = self._select_obsoleted_by(
            s for s in self.pool.solvables if s.repo.name != "@commandline"
        )
        obsoleted = obs_sel.solvables() + existing_obs_sel.solvables()
        logger.debug(
            "Excluding the following obsoleted packages:\n%s",
            "\n".join(f"  {s}" for s in obsoleted),
        )
        for solvable in self.pool.solvables:
            if solvable in self.solvables:
                continue  # checked by check-sat command instead
            if solvable in obsoleted:
                continue  # no reason to check it
            if not self.pool.isknownarch(solvable.archid):
                logger.debug(
                    "Skipping requirements for package %s arch does not match "
                    "Architecture under test",
                    str(solvable),
                )
                continue
            logger.debug("Checking requires for %s", solvable)
            # XXX limit available packages to compatible arches?
            # (use libsolv archpolicies somehow)
            jobs = (
                solvable.Selection().jobs(Job.SOLVER_INSTALL)
                + obs_sel.jobs(Job.SOLVER_ERASE)
                + existing_obs_sel.jobs(Job.SOLVER_ERASE)
            )
            if solver_problems := solver.solve(jobs):
                problem_msgs = [str(p) for p in solver_problems]
                # If it's a pre-existing problem with repos (that is, the
                # problem also exists when the packages under test are
                # excluded) then warn about it here but don't consider it
                # a problem.
                jobs = solvable.Selection().jobs(
                    Job.SOLVER_INSTALL
                ) + existing_obs_sel.jobs(Job.SOLVER_ERASE)
                if existing_problems := solver.solve(jobs):
                    for p in existing_problems:
                        logger.warning(
                            "Ignoring pre-existing repoclosure problem: %s", p
                        )
                else:
                    problems.extend(problem_msgs)
        return problems

    def _files_in_solvable(self, solvable: XSolvable) -> set[str]:
        iterator = solvable.Dataiterator(
            self.pool.str2id("solvable:filelist"),
            None,
            Dataiterator.SEARCH_FILES | Dataiterator.SEARCH_COMPLETE_FILELIST,
        )
        return {match.str for match in iterator}

    def _packages_can_be_installed_together(
        self, left: XSolvable, right: XSolvable
    ) -> bool:
        """
        Returns True if the given packages can be installed together.
        """
        solver = self.pool.Solver()
        left_install_jobs = left.Selection().jobs(Job.SOLVER_INSTALL)
        right_install_jobs = right.Selection().jobs(Job.SOLVER_INSTALL)
        # First check if each one can be installed on its own. If either of
        # these fails it is a warning, because it means we have no way to know
        # if they can be installed together or not.
        if problems := solver.solve(left_install_jobs):
            logger.warning(
                "Ignoring conflict candidate %s "
                "with pre-existing dependency problems: %s",
                left,
                problems[0],
            )
            return False
        if problems := solver.solve(right_install_jobs):
            logger.warning(
                "Ignoring conflict candidate %s "
                "with pre-existing dependency problems: %s",
                right,
                problems[0],
            )
            return False
        if problems := solver.solve(left_install_jobs + right_install_jobs):
            logger.debug(
                "Conflict candidates %s and %s cannot be installed together: %s",
                left,
                right,
                problems[0],
            )
            return False
        return True

    def _file_conflict_is_permitted(
        self, left: XSolvable, right: XSolvable, filename: str
    ) -> bool:
        """
        Returns True if rpm would allow both the given packages to share
        ownership of the given filename.
        """
        ts = rpm.TransactionSet()
        ts.setVSFlags(rpm._RPMVSF_NOSIGNATURES)

        left_hdr = ts.hdrFromFdno(open(left.lookup_location()[0], "rb"))
        right_hdr = ts.hdrFromFdno(open(self.download_package_header(right), "rb"))
        left_files = rpm.files(left_hdr)
        right_files = rpm.files(right_hdr)
        if left_files[filename].matches(right_files[filename]):
            logger.debug(
                "Conflict on %s between %s and %s permitted because files match",
                filename,
                left,
                right,
            )
            return True
        if left_files[filename].color != right_files[filename].color:
            logger.debug(
                "Conflict on %s between %s and %s permitted because colors differ",
                filename,
                left,
                right,
            )
            return True
        return False

    def find_conflicts(self) -> list[str]:
        """
        Find undeclared file conflicts in the packages under test.

        :return: List of str describing each conflict found
                 (or empty list if no conflicts were found)
        """
        # solver = self.pool.Solver()
        problems = []
        for solvable in self.solvables:
            logger.debug("Checking all files in %s for conflicts", solvable)
            filenames = self._files_in_solvable(solvable)
            # In libsolv, iterating all solvables is fast, and listing all
            # files in a solvable is fast, but finding solvables which contain
            # a given file is *very slow* (see bug 1465736).
            # Hence this approach, where we visit each solvable and use Python
            # set operations to look for any overlapping filenames.
            for conflicting in self.pool.solvables:
                # Conflicts cannot happen between identical solvables and also
                # between solvables with the same name - such solvables cannot
                # be installed next to each other.
                if conflicting == solvable or conflicting.name == solvable.name:
                    continue
                conflict_filenames = filenames.intersection(
                    self._files_in_solvable(conflicting)
                )
                if not conflict_filenames:
                    continue
                if not self._packages_can_be_installed_together(solvable, conflicting):
                    continue
                for filename in conflict_filenames:
                    logger.debug(
                        "Considering conflict on %s with %s", filename, conflicting
                    )
                    if not self._file_conflict_is_permitted(
                        solvable, conflicting, filename
                    ):
                        msg = (
                            f"{solvable} provides {filename} "
                            f"which is also provided by {conflicting}"
                        )
                        problems.append(msg)
                    if conflicting not in self.solvables:
                        # For each filename we are checking, we only want to
                        # check at most *one* package from the remote
                        # repositories. This is purely an optimization to save
                        # network bandwidth and time. We *are* potentially
                        # missing some real conflicts by doing this, but the
                        # cost of downloading every package in the distro for
                        # common directories like /usr/lib/debug is too high.
                        # Note however that we do always ensure at least one
                        # *remote* candidate is checked (that is, not from the
                        # set of packages under test) to catch problems like
                        # bug 1502458.
                        logger.debug(
                            "Skipping further checks on %s "
                            "to save network bandwidth",
                            filename,
                        )
                        filenames.remove(filename)
        return sorted(problems)

    def find_upgrade_problems(self) -> list[str]:
        """
        Checks for any package in the repos which would upgrade or obsolete the
        packages under test.

        :return: List of str describing each upgrade problem found (or
                 empty list if no problems were found)
        """
        # Pretend the packages under test are installed, then solve a distupgrade.
        # If any package under test would be erased, then it means some other
        # package in the repos is better than it, and we have a problem.
        self.pool.installed = self.commandline_repo
        try:
            jobs = self.pool.Selection_all().jobs(Job.SOLVER_UPDATE)
            solver = self.pool.Solver()
            solver.set_flag(solver.SOLVER_FLAG_ALLOW_UNINSTALL, True)
            solver_problems = solver.solve(jobs)
            for problem in solver_problems:
                # This is a warning, not an error, because it means there are
                # some *other* problems with existing packages in the
                # repository, not our packages under test. But it means our
                # results here might not be valid.
                logger.warning(
                    "Upgrade candidate has pre-existing dependency problem: %s", problem
                )
            transaction = solver.transaction()
            problems = []
            for solvable in self.solvables:
                action = transaction.steptype(
                    solvable, transaction.SOLVER_TRANSACTION_SHOW_OBSOLETES
                )
                other = transaction.othersolvable(solvable)
                if action == transaction.SOLVER_TRANSACTION_IGNORE:
                    continue  # it's kept, so no problem here
                if action == transaction.SOLVER_TRANSACTION_UPGRADED:
                    problems.append(
                        f"{solvable} would be upgraded by {other} from repo {other.repo.name}"
                    )
                elif action == transaction.SOLVER_TRANSACTION_OBSOLETED:
                    problems.append(
                        f"{solvable} would be obsoleted by {other} from repo {other.repo.name}"
                    )
                else:
                    raise RuntimeError(f"Unrecognised transaction step type {action}")
            return problems
        finally:
            self.pool.installed = None
