from glob import glob
from setuptools import setup
from distutils.command.build import build as _build


# Customization of standard build step: Automatically build the docs as well in
# one build step: python setup.py build
class build(_build):
    sub_commands = _build.sub_commands + [('build_sphinx', lambda self: True)]


# Note that we have some scripts which programmatically change the version
# declared here. Do not adjust the formatting.
name = 'rpmdeplint'
version = '1.5'
release = version

setup(name='rpmdeplint',
      version=version,
      description='Tool to find errors in RPM packages in the context of their dependency graph',
      long_description=open('README.rst').read(),
      url='https://github.com/fedora-ci/rpmdeplint',
      author='Red Hat, Inc.',
      author_email='qa-devel@lists.fedoraproject.org',
      classifiers=[
          'License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)',
          'Programming Language :: Python :: 3',
      ],
      python_requires='>=3.8',
      packages=['rpmdeplint', 'rpmdeplint.tests'],
      setup_requires=["setuptools", "sphinx"],
      install_requires=["six", "rpm", "rpmfluff"],
      # These rpms don't provide python3.11dist(hawkey|librepo|solv)
      # https://bugzilla.redhat.com/show_bug.cgi?id=2237481
      # install_requires+=["hawkey", "librepo", "solv"]
      tests_require=['pytest'],
      data_files = [
          ('/usr/share/man/man1', glob('build/sphinx/man/*.1')),
      ],
      cmdclass = {
          'build': build,
      },
      command_options={
          'build_sphinx': {
              'builder': ('setup.py', 'man'),
              'project': ('setup.py', name),
              'version': ('setup.py', version),
              'release': ('setup.py', release),
          }
      },
      entry_points={
          'console_scripts': [
              'rpmdeplint = rpmdeplint.cli:main',
          ]
      },
)
