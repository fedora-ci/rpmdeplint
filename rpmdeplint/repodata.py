# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.


import os
from collections.abc import Iterator
from configparser import ConfigParser
from contextlib import suppress
from logging import getLogger
from os import getenv
from pathlib import Path
from tempfile import mkdtemp, mkstemp
from time import time
from typing import Any, BinaryIO, Optional, Union

import librepo
import rpm
from requests import Session

logger = getLogger(__name__)
requests_session = Session()


REPO_CACHE_DIR = "/var/tmp"
REPO_CACHE_NAME_PREFIX = "rpmdeplint-"


class PackageDownloadError(Exception):
    """
    Raised if a package is being downloaded for further analysis but the download fails.
    """


class RepoDownloadError(Exception):
    """
    Raised if an error occurs downloading repodata
    """


class Cache:
    @staticmethod
    def base_path() -> Path:
        default_cache_home = Path.home() / ".cache"
        return Path(getenv("XDG_CACHE_HOME", default_cache_home)) / "rpmdeplint"

    @staticmethod
    def entry_path(checksum: str) -> Path:
        return Cache.base_path() / checksum[:1] / checksum[1:]

    @staticmethod
    def clean():
        expiry_time = time() - float(getenv("RPMDEPLINT_EXPIRY_SECONDS", 604800))
        if not Cache.base_path().is_dir():
            return  # nothing to do
        for subdir in Cache.base_path().iterdir():
            # Should be a subdirectory named after the first checksum letter
            if not subdir.is_dir():
                continue
            for entry in subdir.iterdir():
                if not entry.is_file():
                    continue
                if entry.stat().st_mtime < expiry_time:
                    logger.debug("Purging expired cache file %s", entry)
                    entry.unlink()

    @staticmethod
    def download_repodata_file(checksum: str, urls: list[str]) -> BinaryIO:
        """
        Each created file in cache becomes immutable, and is referenced in
        the directory tree within XDG_CACHE_HOME as
        $XDG_CACHE_HOME/rpmdeplint/<checksum-first-letter>/<rest-of-checksum>

        Both metadata and the files to be cached are written to a tempdir first
        then renamed to the cache dir atomically to avoid them potentially being
        accessed before written to cache.
        """
        if not urls:
            raise ValueError("No urls specified to download repodata from")

        filepath_in_cache: Path = Cache.entry_path(checksum)
        if filepath_in_cache.is_file():
            f = filepath_in_cache.open(mode="rb")
            logger.debug("Using cached file %s for %s", filepath_in_cache, urls[0])
            # Bump the modtime on the cache file we are using,
            # since our cache expiry is LRU based on modtime.
            os.utime(f.fileno())
            return f

        filepath_in_cache.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = mkstemp(dir=filepath_in_cache.parent, text=False)
        try:
            f = os.fdopen(fd, "wb+")
        except Exception:
            os.close(fd)
            raise
        try:
            for index, url in enumerate(urls):
                try:
                    logger.debug("Downloading %s", url)
                    response = requests_session.get(url, stream=True)
                    response.raise_for_status()
                    for chunk in response.raw.stream(decode_content=False):
                        f.write(chunk)
                    response.close()
                    break
                except OSError as e:
                    msg = f"Failed to download repodata file {url}, {e}"
                    if index == len(urls) - 1:  # last url in the list
                        raise RepoDownloadError(msg) from e
                    logger.debug(msg)
            f.flush()
            f.seek(0)
            os.fchmod(f.fileno(), 0o644)
            os.rename(temp_path, filepath_in_cache)
            logger.debug("Cached as %s", filepath_in_cache)
            return f
        except Exception:
            f.close()
            os.unlink(temp_path)
            raise


class Repo:
    """
    Represents a Yum ("repomd") package repository to test dependencies against.
    """

    yum_repos_d = Path("/etc/yum.repos.d/")

    @staticmethod
    def get_yumvars() -> dict[str, str]:
        with suppress(ModuleNotFoundError):
            import dnf.conf
            import dnf.rpm

            subst = dnf.conf.Conf().substitutions
            subst["releasever"] = dnf.rpm.detect_releasever(installroot="")
            return subst

        # Probably not going to work but there's not much else we can do...
        return {
            "arch": "$arch",
            "basearch": "$basearch",
            "releasever": "$releasever",
        }

    @classmethod
    def from_yum_config(cls, name: str = "") -> Iterator["Repo"]:
        """
        Yields Repo instances loaded from the system-wide
        configuration in :file:`/etc/yum.repos.d/`.
        """

        def substitute_yumvars(s: str, _yumvars: dict[str, str]) -> str:
            for name, value in _yumvars.items():
                s = s.replace(f"${name}", value)
            return s

        yumvars = cls.get_yumvars()
        config = ConfigParser()
        config.read(cls.yum_repos_d.glob("*.repo"))
        for section in config.sections():
            if name and section != name:
                continue
            if config.has_option(section, "enabled") and not config.getboolean(
                section, "enabled"
            ):
                continue
            skip_if_unavailable = False
            if config.has_option(section, "skip_if_unavailable"):
                skip_if_unavailable = config.getboolean(section, "skip_if_unavailable")
            if config.has_option(section, "baseurl"):
                baseurl = substitute_yumvars(config.get(section, "baseurl"), yumvars)
                yield cls(
                    section, baseurl=baseurl, skip_if_unavailable=skip_if_unavailable
                )
            elif config.has_option(section, "metalink"):
                metalink = substitute_yumvars(config.get(section, "metalink"), yumvars)
                yield cls(
                    section, metalink=metalink, skip_if_unavailable=skip_if_unavailable
                )
            elif config.has_option(section, "mirrorlist"):
                mirrorlist = substitute_yumvars(
                    config.get(section, "mirrorlist"), yumvars
                )
                yield cls(
                    section,
                    metalink=mirrorlist,
                    skip_if_unavailable=skip_if_unavailable,
                )
            else:
                raise ValueError(
                    "Yum config section %s has no "
                    "baseurl or metalink or mirrorlist" % section
                )

    def __init__(
        self,
        name: str,
        baseurl: Optional[str] = None,
        metalink: Optional[str] = None,
        skip_if_unavailable: bool = False,
    ):
        """
        :param name: Name of the repository, for example "fedora-updates"
                          (used in problems and error messages)
        :param baseurl: URL or filesystem path to the base of the repository
                        (there should be a repodata subdirectory under this)
        :param metalink: URL to a Metalink file describing mirrors where
                         the repository can be found
        :param skip_if_unavailable: If True, suppress errors downloading
                                    repodata from the repository

        Exactly one of the *baseurl* or *metalink* parameters must be supplied.
        """
        self.name = name
        if not baseurl and not metalink:
            raise ValueError("Must specify either baseurl or metalink for repo")
        if baseurl and not baseurl.startswith("http"):
            baseurl = baseurl.removeprefix("file://")
            if not Path(baseurl).is_dir():
                raise ValueError(f"baseurl {baseurl!r} is not a local directory")
        self.urls = [baseurl] if baseurl else []
        self.metalink = metalink
        self.skip_if_unavailable = skip_if_unavailable

        self.is_local = baseurl is not None and Path(baseurl).is_dir()
        self._librepo_handle = librepo.Handle()
        self._rpmmd_repomd: Optional[dict[str, Any]] = None
        self.primary: Optional[BinaryIO] = None
        self.filelists: Optional[BinaryIO] = None

    def download_repodata(self):
        Cache.clean()
        self._download_metadata_result()
        if self.is_local:
            self.primary = open(self.primary_urls[0], "rb")
            self.filelists = open(self.filelists_urls[0], "rb")
        else:
            self.primary = Cache.download_repodata_file(
                self.primary_checksum, self.primary_urls
            )
            self.filelists = Cache.download_repodata_file(
                self.filelists_checksum, self.filelists_urls
            )

    def _download_metadata_result(self) -> None:
        def perform() -> librepo.Result:
            try:
                return self._librepo_handle.perform()
            except librepo.LibrepoException as ex:
                raise RepoDownloadError(
                    f"Failed to download repo metadata for {self!r}: {ex.args[1]}"
                ) from ex

        logger.debug(
            "Loading repodata for repo %r from %s",
            self.name,
            self.urls or self.metalink,
        )

        h = self._librepo_handle
        h.repotype = librepo.LR_YUMREPO
        if self.urls:
            h.urls = self.urls
        if self.metalink:
            h.metalinkurl = self.metalink
        if self.is_local:
            # no files will be downloaded
            h.local = True
        else:
            # tempdir for repomd.xml (metadata) and downloaded rpms
            h.destdir = mkdtemp(
                self.name, prefix=REPO_CACHE_NAME_PREFIX, dir=REPO_CACHE_DIR
            )
        h.interruptible = True  # Download is interruptible
        h.yumdlist = []  # Download only repomd.xml from repodata/

        result = perform()
        self._rpmmd_repomd = result.rpmmd_repomd

        if self.metalink:
            # Only download & parse metalink/mirrorlist
            h.fetchmirrors = True
            perform()
            self.urls.extend(m for m in h.mirrors if m.startswith("http"))

    def _is_header_complete(self, local_path: Union[Path, str]) -> bool:
        """
        Returns `True` if the RPM file `local_path` has complete RPM header.
        """
        try:
            with open(local_path, "rb") as f:
                try:
                    ts = rpm.TransactionSet()
                    ts.setVSFlags(rpm._RPMVSF_NOSIGNATURES)

                    # Supress the RPM error message printed to stderr in case
                    # the header is not complete. Set the log verbosity to CRIT
                    # to achieve that. This way the critical errors are still
                    # logged, but the expected "bad header" error is not.
                    rpm.setVerbosity(rpm.RPMLOG_CRIT)
                    ts.hdrFromFdno(f)
                    return True
                except rpm.error:
                    return False
                finally:
                    # Revert back to RPMLOG_ERR.
                    rpm.setVerbosity(rpm.RPMLOG_ERR)
        except FileNotFoundError:
            return False

    def download_package_header(self, location: str, baseurl: str) -> str:
        """
        Downloads the package header, so it can be parsed by `hdrFromFdno`.

        There is no function provided by the Python `rpm` module which would
        return the size of RPM header. This method therefore tries to download
        first N bytes of the RPM file and checks if the header is complete or
        not using the `hdrFromFdno` RPM funtion.

        As the header size can be very different from package to package, it
        tries to download first 100KB and if header is not complete, it
        fallbacks to 1MB and 5MB. If that is not enough, the final fallback
        downloads whole RPM file.

        This strategy still wastes some bandwidth, because we are downloading
        first N bytes repeatedly, but because header of typical RPM fits
        into first 100KB usually and because the RPM data is much bigger than
        what we download repeatedly, it saves a lot of time and bandwidth overall.

        Checksums cannot be checked by this method, because checksums work
        only when complete RPM file is downloaded.
        """
        local_path = os.path.join(self._root_path, os.path.basename(location))
        if self.is_local:
            logger.debug("Using package %s from local filesystem directly", local_path)
            return local_path

        # Check if we already downloaded this file and return it if so.
        if self._is_header_complete(local_path):
            logger.debug("Using already downloaded package from %s", local_path)
            return local_path

        logger.debug("Loading package %s from repo %s", location, self.name)
        for byterangeend in [100000, 1000000, 5000000, 0]:
            target = librepo.PackageTarget(
                location,
                base_url=baseurl,
                dest=self._root_path,
                handle=self._librepo_handle,
                byterangestart=0,
                byterangeend=byterangeend,
            )

            if byterangeend:
                logger.debug("Download first %s bytes of %s", byterangeend, location)
            else:
                logger.debug("Download %s", location)
            librepo.download_packages([target])
            if target.err and target.err != "Already downloaded":
                raise PackageDownloadError(
                    f"Failed to download {location} from repo {self.name}: {target.err}"
                )
            if self._is_header_complete(target.local_path):
                break

        logger.debug("Saved as %s", target.local_path)
        return target.local_path

    @property
    def _root_path(self) -> str:
        # Path to the local repo dir or
        # tempdir with downloaded repomd.xml and rpms
        return self.urls[0] if self.is_local else self._librepo_handle.destdir

    @property
    def repomd(self) -> dict[str, Any]:
        if not self._rpmmd_repomd:
            raise RuntimeError("_rpmmd_repomd is not set")
        return self._rpmmd_repomd

    @property
    def primary_checksum(self) -> str:
        return self.repomd["records"]["primary"]["checksum"]

    @property
    def primary_urls(self) -> list[str]:
        location_href = self.repomd["records"]["primary"]["location_href"]
        return [os.path.join(url, location_href) for url in self.urls]

    @property
    def filelists_checksum(self) -> str:
        return self.repomd["records"]["filelists"]["checksum"]

    @property
    def filelists_urls(self) -> list[str]:
        location_href = self.repomd["records"]["filelists"]["location_href"]
        return [os.path.join(url, location_href) for url in self.urls]

    def __repr__(self):
        return (
            "Repo("
            f"name={self.name!r}, "
            f"urls={self.urls!r}, "
            f"metalink={self.metalink!r}, "
            f"skip_if_unavailable={self.skip_if_unavailable!r}, "
            f"is_local={self.is_local!r})"
        )
