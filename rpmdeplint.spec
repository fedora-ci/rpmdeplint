Name:           rpmdeplint
Version:        2.0
Release:        %autorelease
Summary:        Tool to find errors in RPM packages in the context of their dependency graph
License:        GPL-2.0-or-later
URL:            https://github.com/fedora-ci/rpmdeplint
Source0:        %{pypi_source rpmdeplint}
BuildArch:      noarch

# The base package is just the CLI, which pulls in the rpmdeplint
# Python modules to do the real work.
Requires:       python3-rpmdeplint = %{version}-%{release}

%description
Rpmdeplint is a tool to find errors in RPM packages in the context of their
dependency graph.


%package -n python3-rpmdeplint
%{?python_provide:%python_provide python3-rpmdeplint}
Summary:        %{summary}
BuildRequires:  python3-devel
BuildRequires:  make
# These rpms don't provide python3.11dist(librepo|solv)
# https://bugzilla.redhat.com/show_bug.cgi?id=2237481
BuildRequires:  python3-librepo
BuildRequires:  python3-solv
Requires:       python3-librepo
Requires:       python3-solv

%description -n python3-rpmdeplint
Rpmdeplint is a tool to find errors in RPM packages in the context of their
dependency graph.

This package provides a Python 3 API for performing the checks.

%prep
%autosetup -n rpmdeplint-%{version}

%generate_buildrequires
# The -w flag is required for EPEL 9's older hatchling
%pyproject_buildrequires %{?el9:-w} -x docs -x tests

%build
%pyproject_wheel
make -C docs man

%install
%pyproject_install

mkdir -p %{buildroot}%{_mandir}/man1/
mv docs/_build/man/rpmdeplint.1 %{buildroot}%{_mandir}/man1/

%pyproject_save_files rpmdeplint

%check
%pytest tests/unit/ -k "not TestDependencyAnalyzer"
# Acceptance tests do not work in mock because they require .i686 packages.

%files
%{_bindir}/rpmdeplint
%{_mandir}/man1/rpmdeplint.1.*

%files -n python3-rpmdeplint -f %{pyproject_files}
%license COPYING
%doc README.md

%changelog
%autochangelog
