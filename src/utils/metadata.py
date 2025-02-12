"""Utilities for creating a metadata file."""

import datetime
import json
import os
import sys
from contextlib import contextmanager
from subprocess import CalledProcessError, check_output

import sphinx.util

from sphinx_bluebrain_theme.utils._importlib import metadata as importlib_metadata

logger = sphinx.util.logging.getLogger(__name__)


def _parse_item(item):
    """Given a string with 2 items separated by comma, return a tuple (key, value)."""
    key, value = item.split(",", 1)
    return key.strip(), value.strip()


@contextmanager
def change_cwd(temp_cwd):
    """Temporarily change the current working directory."""
    original_cwd = os.getcwd()
    try:
        os.chdir(temp_cwd)
        yield
    finally:
        os.chdir(original_cwd)


@contextmanager
def add_to_path(temp_path):
    """Temporarily add a path to the system PATH."""
    # use list() to copy
    path_original = list(sys.path)
    try:
        sys.path.insert(0, temp_path)
        yield
    finally:
        sys.path = path_original


def write_metadata(metadata, output_dir):
    """Write a metadata dict to a directory."""
    metadata_path = os.path.join(output_dir, "metadata.md")
    with open(metadata_path, "w", encoding="utf8") as fd:
        fd.write("---\n")
        for k, v in metadata.items():
            fd.write(str(k) + ": " + str(v) + "\n")
        fd.write("---\n")

    metadata_json_path = os.path.join(output_dir, "metadata.json")
    with open(metadata_json_path, "w", encoding="utf8") as fd:
        fd.write(json.dumps(metadata))

    return metadata_path


def get_metadata_from_json(json_path):
    """Get metadata from a json file.

    The file must follow the standard structure of package.json files.
    """
    with open(json_path, encoding="utf8") as fd:
        metadata = json.load(fd)

    output = {
        "name": metadata["name"],
        "version": metadata["version"],
        "description": metadata["description"],
        "homepage": metadata["homepage"],
        "repository": metadata["repository"]["url"],
        "issuesurl": metadata["bugs"]["url"],
        "license": metadata["license"],
        "maintainers": metadata["author"],
    }

    # contributors is a special case where they might be
    # specified in the conf.py so don't raise an error
    output["contributors"] = ",".join(metadata.get("contributors", ""))

    return output


def build_metadata_from_setuptools_dict(metadata):
    """Build and return metadata from a setuptools dict.

    This is typically from package metadata.
    """
    # based on warehouse and virtualenv examples
    # https://github.com/pypa/warehouse/blob/master/warehouse/templates/packaging/detail.html
    # https://github.com/pypa/virtualenv/blob/master/setup.cfg
    # and on PEP621 for projects that use pyproject.toml files
    # https://peps.python.org/pep-0621/#example
    # https://peps.python.org/pep-0621/#have-a-separate-url-home-page-field
    project_urls = metadata["project_urls"]
    if "Source" in project_urls:
        repo_url = project_urls["Source"]
    else:
        repo_url = project_urls["Repository"]
    if "url" in metadata:
        homepage_url = metadata["url"]
    else:
        homepage_url = project_urls["Homepage"]
    if "author" in metadata:
        author = metadata["author"]
    else:
        author = metadata["author_email"]
    output = {
        "name": metadata["name"],
        "version": metadata["version"],
        "description": metadata["description"],
        "homepage": homepage_url,
        "license": metadata["license"],
        "maintainers": author,
        "repository": repo_url,
        "issuesurl": project_urls["Tracker"],
    }

    # contributors is a special case where they might be
    # specified in the conf.py so don't raise an error
    output["contributors"] = metadata.get("contributors", None)

    return output


def get_metadata_from_distribution(distribution_name):
    """Get the metadata from a distribution."""
    metadata = {}
    # useful information: https://packaging.python.org/specifications/core-metadata/
    # the keys are all lower case and using _ as a separator
    metadata_json = importlib_metadata.metadata(distribution_name).json
    for key, value in metadata_json.items():
        # treat UNKNOWN as no value, this the setuptools metadata
        # equivalent of None
        if not value or value == "UNKNOWN":
            continue
        # handle special cases
        if key == "project_url":
            key = "project_urls"
            value = dict(_parse_item(item) for item in value)
        elif isinstance(value, list):
            key = f"{key}s"
        metadata[key] = value

    # allow summary as the description
    if "description" not in metadata:
        metadata["description"] = metadata["summary"]

    # home-page needs to be used for the url
    if "home_page" in metadata:
        metadata["url"] = metadata["home_page"]

    return build_metadata_from_setuptools_dict(metadata)


def build_metadata(metadata_file=None, metadata_overrides=None, distribution_name=None):
    """Read metadata from sources."""
    metadata = {}

    # load metadata from file if specified
    if distribution_name is not None:
        metadata_package = get_metadata_from_distribution(distribution_name)
        metadata.update(metadata_package)
    elif metadata_file:
        if not os.path.isfile(metadata_file):
            raise FileNotFoundError(
                f"{metadata_file}: specified metadata_file does not exist"
            )

        _, ext = os.path.splitext(metadata_file)
        if ext != ".json":
            raise ValueError(f"{metadata_file}: expected a .json file")

        metadata_package = get_metadata_from_json(metadata_file)
        metadata.update(metadata_package)

    if metadata_overrides:
        metadata.update(metadata_overrides)

    # early return if no metadata
    if not metadata:
        return None

    # add updated key if not there already
    if "updated" not in metadata:
        metadata["updated"] = datetime.datetime.now().strftime("%d/%m/%y")

    return metadata


def write_metadata_sphinx(app, exception):  # pylint: disable=unused-argument
    """Write the metadata to the output directory."""
    # get the metadata file from context variable
    metadata_file = app.config["html_theme_options"].get("metadata_file", None)
    metadata_dist = app.config["html_theme_options"].get("metadata_distribution", None)

    if metadata_dist is not None:
        if metadata_file:
            raise ValueError("only one of metadata_file and metadata_dist is allowed")

        metadata_file = None

    if metadata_file is not None and not os.path.isabs(metadata_file):
        metadata_file = os.path.abspath(os.path.join(app.confdir, metadata_file))

    # load extra metadata from the given context variable
    metadata_overrides = app.config["html_theme_options"].get("metadata_overrides", {})

    # get the path to the git repo if given
    repo_path = app.config["html_theme_options"].get("repo_path", None)

    metadata = build_metadata(metadata_file, metadata_overrides, metadata_dist)

    if not metadata:
        return

    # confirm that versions match between metadata and sphinx
    # if using a distribution for metadata, it will catch the case
    # that another version of the same package has been imported
    if os.environ.get("SPHINX_BLUE_BRAIN_THEME_CHECK_VERSIONS", "True").lower() in [
        "true",
        "1",
    ]:
        assert metadata["version"] == app.config["version"], (
            f"conf.py version ({app.config['version']}) and metadata version "
            f"({metadata['version']}) do not match"
        )

    contributors = metadata.get("contributors", None)

    # if contributors is a git repo, get the top five
    if contributors is None:
        clone_path = app.confdir
        if repo_path is not None:
            if not os.path.isabs(repo_path):
                repo_path = os.path.abspath(os.path.join(app.confdir, repo_path))
            if not (os.path.isdir(repo_path) or os.path.isfile(repo_path)):
                raise IOError(f"{repo_path}: not a git repository")
            clone_path = os.path.dirname(repo_path)

        # check if it is a git repo
        try:
            cmd = ["git", "-C", clone_path, "rev-parse", "--is-inside-work-tree"]
            is_in_git_repo = check_output(cmd).decode("utf-8") == "true"
        except CalledProcessError:
            is_in_git_repo = False

        if is_in_git_repo:
            cmd = ["git", "-C", clone_path, "shortlog", "-sne", "origin/master"]

            contributors = check_output(cmd).decode("utf-8").splitlines()
            contributors = [line.split("\t", 2)[1] for line in contributors]
            has_etal = len(contributors) > 5

            # get top five contributors and account for any additional
            contributors = ", ".join(contributors[:5]) + " et al." if has_etal else ""
        elif not repo_path:
            contributors = "None"

        metadata["contributors"] = contributors

    # don't check for None here, as we also want to catch an empty string
    if not metadata.get("contributors", None):
        raise ValueError("contributors must be provided or repo_path given")

    # write the metadata
    write_metadata(metadata=metadata, output_dir=app.outdir)
