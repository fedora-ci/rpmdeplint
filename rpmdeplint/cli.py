# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.


import argparse
import logging
import sys
from collections.abc import Callable
from enum import IntEnum
from importlib import metadata

from rpmdeplint import DependencyAnalyzer, UnreadablePackageError
from rpmdeplint.repodata import PackageDownloadError, Repo, RepoDownloadError

logger = logging.getLogger(__name__)

version = metadata.version("rpmdeplint")


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    FAILED = 3


def log_problems(message: str, _problems: list[str]) -> ExitCode:
    sys.stderr.write(f"{message}:\n")
    sys.stderr.write("\n".join(_problems) + "\n")
    return ExitCode.FAILED


def cmd_check(args) -> ExitCode:
    """
    Performs all checks on the given packages.
    """

    exit_code = ExitCode.OK
    with dependency_analyzer_from_args(args) as analyzer:
        logger.debug("Performing satisfiability check (check-sat)")
        dependency_set = analyzer.try_to_install_all()
        if not dependency_set.is_ok:
            exit_code = log_problems(
                "Problems with dependency set", dependency_set.overall_problems
            )
        logger.debug("Performing repoclosure check (check-repoclosure)")
        if problems := analyzer.find_repoclosure_problems():
            exit_code = log_problems("Dependency problems with repos", problems)
        logger.debug("Performing file conflict check (check-conflicts)")
        if conflicts := analyzer.find_conflicts():
            exit_code = log_problems("Undeclared file conflicts", conflicts)
        logger.debug("Performing upgrade check (check-upgrade)")
        if problems := analyzer.find_upgrade_problems():
            exit_code = log_problems("Upgrade problems", problems)
    return exit_code


def cmd_check_sat(args) -> ExitCode:
    """
    Checks that all dependencies needed to install the given packages
    can be satisfied using the given repos.
    """
    with dependency_analyzer_from_args(args) as analyzer:
        dependency_set = analyzer.try_to_install_all()
        if not dependency_set.is_ok:
            return log_problems(
                "Problems with dependency set", dependency_set.overall_problems
            )
    return ExitCode.OK


def cmd_check_repoclosure(args) -> ExitCode:
    """
    Checks that all dependencies of all packages in the given repos can still
    be satisfied, when the given packages are included.
    """
    with dependency_analyzer_from_args(args) as analyzer:
        if problems := analyzer.find_repoclosure_problems():
            return log_problems("Dependency problems with repos", problems)
    return ExitCode.OK


def cmd_check_conflicts(args) -> ExitCode:
    """
    Checks for undeclared file conflicts in the given packages.
    """
    with dependency_analyzer_from_args(args) as analyzer:
        if conflicts := analyzer.find_conflicts():
            return log_problems("Undeclared file conflicts", conflicts)
    return ExitCode.OK


def cmd_check_upgrade(args) -> ExitCode:
    """
    Checks that the given packages are not older than any other existing
    package in the repos.
    """
    with dependency_analyzer_from_args(args) as analyzer:
        if problems := analyzer.find_upgrade_problems():
            return log_problems("Upgrade problems", problems)
    return ExitCode.OK


def cmd_list_deps(args) -> ExitCode:
    """
    Lists all (transitive) dependencies of the given packages -- that is,
    the complete set of dependent packages which are needed
    in order to install the packages under test.
    """
    exit_code = ExitCode.OK
    with dependency_analyzer_from_args(args) as analyzer:
        dependency_set = analyzer.try_to_install_all()
        if not dependency_set.is_ok:
            exit_code = log_problems(
                "Problems with dependency set", dependency_set.overall_problems
            )

    package_deps = dependency_set.package_dependencies
    for pkg, deps in package_deps.items():
        _deps = deps["dependencies"]
        sys.stdout.write(f"{pkg} has {len(_deps)} dependencies:\n")
        sys.stdout.write("\n".join(["\t" + x for x in _deps]))
        sys.stdout.write("\n\n")
    return exit_code


def log_to_stream(stream, level=logging.WARNING):
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    logging.getLogger().handlers = [stream_handler]


def dependency_analyzer_from_args(args):
    repos = []
    if args.repos_from_system:
        repos.extend(Repo.from_yum_config())
    repos.extend(args.repos)

    return DependencyAnalyzer(repos, list(args.rpms), arch=args.arch)


def comma_separated_repo(value: str) -> Repo:
    if "," not in value:
        raise argparse.ArgumentTypeError(
            f"Repo {value!r} is not in the form <name>,<path>"
        )
    repo_name, baseurl = value.split(",", 1)
    return Repo(repo_name, baseurl)


def add_common_dependency_analyzer_args(parser):
    parser.add_argument(
        "rpms",
        metavar="RPMPATH",
        nargs="+",
        help="Path to an RPM package to be checked",
    )
    parser.add_argument(
        "-r",
        "--repo",
        metavar="NAME,URL",
        type=comma_separated_repo,
        action="append",
        dest="repos",
        default=[],
        help="Name and URL of a repo to test against",
    )
    parser.add_argument(
        "-R",
        "--repos-from-system",
        action="store_true",
        help="Test against system repos from /etc/yum.repos.d/",
    )
    parser.add_argument(
        "-a",
        "--arch",
        dest="arch",
        default=None,
        help="Limit dependency resolution to ARCH packages [default: any arch]",
    )


def validate_common_dependency_analyzer_args(parser, args):
    if not args.repos and not args.repos_from_system:
        parser.error(
            "no repos specified to test against\n"
            "Use the --repo option to test against specific repository URLs,\n"
            "or use the --repos-from-system option to load the "
            "system-wide repos from /etc/yum.repos.d/."
        )


def main():
    def add_subparser(
        subcommand: str, _help: str, subcommand_func: Callable[..., ExitCode]
    ):
        parser_check = subparsers.add_parser(
            subcommand, help=help, description=subcommand_func.__doc__
        )
        add_common_dependency_analyzer_args(parser_check)
        parser_check.set_defaults(func=subcommand_func)

    parser = argparse.ArgumentParser(
        description="Checks for errors in "
        "RPM packages in the context of their dependency graph.",
        prog="rpmdeplint",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Show detailed progress messages"
    )
    parser.add_argument("--quiet", action="store_true", help="Show only errors")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version}")

    subparsers = parser.add_subparsers(dest="subcommand", title="subcommands")
    subparsers.required = True

    add_subparser("check", "Perform all checks", cmd_check)
    add_subparser(
        "check-sat",
        "Check that dependencies can be satisfied",
        cmd_check_sat,
    )
    add_subparser(
        "check-repoclosure",
        "Check that repo dependencies can still be satisfied",
        cmd_check_repoclosure,
    )
    add_subparser(
        "check-conflicts",
        "Check for undeclared file conflicts",
        cmd_check_conflicts,
    )
    add_subparser(
        "check-upgrade",
        "Check package is an upgrade",
        cmd_check_upgrade,
    )
    add_subparser(
        "list-deps",
        "List all packages needed to satisfy dependencies",
        cmd_list_deps,
    )
    args = parser.parse_args()
    logging.getLogger().setLevel(logging.DEBUG)
    log_to_stream(
        sys.stderr,
        level=logging.DEBUG
        if args.debug
        else logging.ERROR
        if args.quiet
        else logging.WARNING,
    )

    validate_common_dependency_analyzer_args(parser, args)

    try:
        return args.func(args)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    except (UnreadablePackageError, RepoDownloadError, PackageDownloadError) as exc:
        sys.stderr.write("%s\n" % exc)
        return ExitCode.ERROR


if __name__ == "__main__":
    sys.exit(main())
