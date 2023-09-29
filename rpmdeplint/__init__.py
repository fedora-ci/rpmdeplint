# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.


from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version

from rpmdeplint.analyzer import DependencyAnalyzer, DependencySet
from rpmdeplint.repodata import Repo

with suppress(PackageNotFoundError):
    __version__ = version("rpmdeplint")


__all__ = [
    DependencyAnalyzer.__name__,
    DependencySet.__name__,
    Repo.__name__,
]
