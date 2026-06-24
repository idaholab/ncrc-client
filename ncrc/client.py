"""Implement the Client."""

import os
import sys
import getpass
import argparse
import re
import pickle
import platform
from typing import Optional
from pathlib import Path
from importlib.util import find_spec


def error(*message):
    """Emit an error message and exit."""
    print(f"ERROR: {'\n'.join(message)}", file=sys.stderr)
    sys.exit(1)


if not find_spec("conda"):
    CONDA_DEFAULT_ENV = os.environ.get("CONDA_DEFAULT_ENV")
    if CONDA_DEFAULT_ENV is not None and CONDA_DEFAULT_ENV != "base":
        error(
            "Not in the base Conda environment.",
            "Switch environments with `conda deactivate` and install and run `ncrc` there instead.",
        )
    error("Unable to import Conda", "Please install conda: `conda install conda`")

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from conda.api import SubdirData
from conda.cli.main import main_subshell as conda_main_subshell
from conda.gateways.connection.session import CondaSession


class WrapCondaSessionCookies:
    """Wrap a Conda session with the given cookies."""

    def __init__(self, cookies):
        self.cookies = cookies
        """The cookies to inject."""
        self.original_conda_session_init = None
        """The original method for CondaSession.__init__, which will be replaced."""

    def __enter__(self):
        original_conda_session_init = CondaSession.__init__
        cookies = self.cookies
        self.original_conda_session_init = CondaSession.__init__

        def patched_conda_session_init(self, *args, **kwargs):
            original_conda_session_init(self, *args, **kwargs)
            self.cookies.update(cookies)

        CondaSession.__init__ = patched_conda_session_init

    def __exit__(self, exc_type, exc_val, exc_tb):
        CondaSession.__init__ = self.original_conda_session_init


CACHE_DIR = os.path.join(Path.home(), ".cache", "ncrc")
"""The cache directory for this application."""


class Client:
    """NCRC Client class responsible for creating the connection using Conda API"""

    def __init__(self, args):
        self.args = args
        """The parsed arguments."""

        self.session = requests.Session()
        """The session, used to store the RSA cookies for accessing the host."""

        self.server = "conda.software.inl.gov"
        """The server to connect to."""

        self.channel_url = f"https://{self.server}/ncrc-{self.args.application}"
        """The URL to the channel for this application."""

        self.package_name = f"ncrc-{self.args.application}"
        """The name of the package for this application."""

        self.insecure: bool = getattr(self.args, "insecure", False)
        """Whether or not to use insecure access."""

        if self.insecure:
            requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

        self.setup_session()

    def wrap_conda_session_cookies(self) -> WrapCondaSessionCookies:
        """Wrap a Conda session with the cookies from the RSA session."""
        return WrapCondaSessionCookies(self.session.cookies)

    def conda_run(self, *args, **kwargs) -> Optional[str]:
        """Run the given command with conda."""
        if self.insecure:
            args = [*args, "--insecure"]
        ret = conda_main_subshell(*args)

    def get_cookie_cache_path(self) -> str:
        """The path to the cookie cache for the current server."""
        return os.path.join(CACHE_DIR, f"cookies_{self.server}")

    @staticmethod
    def get_conda_arch() -> str:
        """Get conda arch for this host."""
        if sys.platform == "win32":
            error(
                "Packages are not distributed for Windows; use WSL within Windows instead."
            )
        elif sys.platform == "darwin":
            if platform.machine() == "x86_64":
                error("Packages are not distributed for Intel Macs.")
            return "osx-arm64"
        elif sys.platform.startswith("linux"):
            if platform.machine() == "arm64":
                error("Packages are not distributed for ARM64 on Linux.")
            return "linux-64"
        error("This host architecture is not supported.")
        return ""

    def setup_session(self):
        """Setup the session for making requests to the RSA secured host."""
        cookie_file = self.get_cookie_cache_path()
        verify = not self.insecure
        url = f"https://{self.server}"

        # Load previous cookies to see if they work
        if os.path.exists(cookie_file):
            with open(cookie_file, "rb") as file_o:
                self.session.cookies.update(pickle.load(file_o))

            response = self.session.get(
                f"{self.channel_url}/channeldata.json", verify=verify
            )

            # They worked, we're good to exit
            if response.status_code == 200 and "application" in response.headers.get(
                "Content-Type", ""
            ):
                return

        # Get the csrftoken
        response = self.session.get(f"{url}/webauthentication", verify=verify)
        if response.status_code != 200:
            error(f"Could not connect to {url}")

        token = re.findall(r'name="csrftoken" value="(\w+)', response.text)
        username = input("INL HPC Username: ")
        passcode = getpass.getpass("INL HPC PIN+TOKEN: ")

        # Need to generate new cookies for accessing through RSA
        try:
            response = self.session.post(
                f"{url}/webauthentication",
                verify=verify,
                data={
                    "csrftoken": token[0],
                    "username": username,
                    "passcode": passcode,
                },
            )

        except requests.exceptions.ConnectTimeout:
            error(f"Unable to establish a connection to: {url}")
        except (
            requests.exceptions.ProxyError,
            urllib3.exceptions.ProxySchemeUnknown,
            urllib3.exceptions.NewConnectionError,
        ):
            error(f'Proxy information incorrect: {os.getenv("https_proxy")}')
        except requests.exceptions.SSLError:
            error(
                "Unable to establish a secure connection.",
                "If you trust this server, you can use --insecure",
            )
        except ValueError:
            error(
                "Unable to determine SOCKS version from https_proxy",
                "environment variable",
            )
        except requests.exceptions.ConnectionError:
            error(f"General error connecting to server {url}")
        except Exception:
            raise

        # Final checking on the response
        if response.status_code != 200:
            error(f"Could not authenticate to {url}")
        elif not re.search("Authentication Succeeded", response.text):
            error("Invalid credentials; try again.")

        # Update the cookie file
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cookie_file, "wb") as file_o:
            pickle.dump(self.session.cookies, file_o)

    def get_channel_versions(self) -> list[str]:
        """Get the package versions available on the current channel."""
        with self.wrap_conda_session_cookies():
            results = SubdirData.query_all(
                self.package_name,
                channels=[self.channel_url],
                subdirs=[self.get_conda_arch()],
            )
        return sorted([v.version for v in results])

    def _action_install(self):
        """Perform the install action."""

        app = self.args.application
        environment_name = self.args.name
        if environment_name is None:
            environment_name = app

        versions = self.get_channel_versions()

        version = versions[-1] if self.args.version is None else self.args.version
        if version not in versions:
            error(
                f"NCRC application {app} version '{version}' does not exist",
                f"Use `ncrc list {app}` to see available versions.",
            )

        channels = [
            self.channel_url,
            "https://conda.software.inl.gov/public",
            "conda-forge",
        ]
        command = ["create", "-n", environment_name, f"{self.package_name}=={version}"]
        for channel in channels:
            command.extend(["--channel", channel])

        with self.wrap_conda_session_cookies():
            self.conda_run(*command)

        message = f"""
Installation complete.

To use {app}, activate the environment:

    > conda activate {environment_name}

Documentation is locally available by activating this environment and pointing
your web browser to the file denoted by echoing the following variable:

    > echo ${app}_DOCS

Additional usage information is also available at:

    https://mooseframework.inl.gov/ncrc/applications/ncrc_conda_{app}.html
        """
        print(message)

    def _action_list(self):
        """Perform the list action."""
        [print(v) for v in self.get_channel_versions()]

    def main(self):
        """Perform the action."""
        getattr(self, f"_action_{self.args.action}")()

    @staticmethod
    def parse_args(argv):
        """Parse arguments with argparser"""
        parser = argparse.ArgumentParser(
            description="Install and search NCRC applications."
        )
        parent = argparse.ArgumentParser(add_help=False)

        def add_common_args(parser: argparse.ArgumentParser):
            parser.add_argument(
                "application",
                choices=[
                    "bison",
                    "bluecrab",
                    "direwolf",
                    "griffin",
                    "marmot",
                    "pronghorn",
                    "relap7",
                    "sabertooth",
                    "sockeye",
                ],
            )
            parser.add_argument(
                "-k",
                "--insecure",
                action="store_true",
                default=False,
                help="Allow untrusted connections.",
            )

        action_parser = parser.add_subparsers(
            dest="action", help="The action to perform."
        )
        action_parser.required = True

        install_parser = action_parser.add_parser(
            "install", parents=[parent], help="Install an application."
        )
        add_common_args(install_parser)
        install_parser.add_argument(
            "-n",
            "--name",
            type=str,
            help="The name of the Conda environment to install into; defaults to the application name.",
        )
        install_parser.add_argument(
            "--version",
            type=str,
            help="A specific version of the application to install.",
        )

        list_parser = action_parser.add_parser(
            "list",
            parents=[parent],
            help="List the available versions for the application.",
        )
        add_common_args(list_parser)

        return parser.parse_args(argv)
