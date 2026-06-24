import os
import os.path

from setuptools import setup, find_packages

from ncrc.version import version_str

def read(*paths):
    """Build a file path from *paths* and return the contents."""
    with open(os.path.join(*paths), 'r') as f:
        return f.read()

setup(
    name = "ncrc",
    version = version_str,

    author = "Logan Harbour",
    author_email = "logan.harbour@inl.gov",
    description = "Installs protected applications from the INL NCRC",
    long_description = read("README.md"),
    license = "BSD-3-Clause",
    keywords = "RSA SecurID token MFA 2FA channel",
    url = "https://github.com/idaholab/ncrc-client",
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Operating System :: MacOS :: MacOS X',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: POSIX :: Linux',
        'Intended Audience :: Developers',
        'Topic :: Scientific/Engineering',
    ],

    # files
    packages = find_packages(),
    entry_points = {
        'console_scripts': [
            'ncrc=ncrc.__main__:main',
        ],
    },
    install_requires = [],
    package_data = {'': ['LICENSE.txt']},
    include_package_data = True,
)
