"""Answers the device's GitHub requests (raw files and tree listings) from
local checkouts instead, so the Apps page shows unpushed changes exactly as
it would after a push. Everything else goes out to the real network.
"""

import errno
import io
import json
import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

RAW_HOST = "raw.githubusercontent.com"
API_HOST = "api.github.com"

# Matches the device's own default when settings.txt has no repository_url.
DEFAULT_REPOSITORY_URL = (
    "https://raw.githubusercontent.com/MatrixBOX-dev/matrixbox/refs/heads/main/"
)

# Captured before the path sandbox replaces them, which would otherwise
# redirect reads of the real checkout into the staged copy.
_REAL_OPEN = io.open
_REAL_STAT = os.stat

_SKIPPED_DIRECTORY_NAMES = {"__pycache__"}


def repository_identity(repository_url: str) -> tuple[str, str] | None:
    """The (owner, repo) in a raw.githubusercontent.com URL, taken the same
    way the device does: its 2nd and 3rd path segments."""
    parts = repository_url.replace("https://", "").split("/")
    if len(parts) < 3 or not parts[1] or not parts[2]:
        return None

    return parts[1], parts[2]


def repository_key(owner: str, repo: str) -> str:
    # GitHub is case-insensitive here, and the device rewrites the owner's
    # case on load, so all comparisons go through this.
    return f"{owner}/{repo}".lower()


class LocalResponse:
    """The subset of an `adafruit_requests.Response` the device uses."""

    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)

    def close(self) -> None:
        pass


class LocalRepository:
    """One local directory, served the way GitHub would serve it if the
    working tree were committed and pushed."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def file_paths(self) -> list[str]:
        paths = self._git_file_paths()
        if paths is None:
            paths = self._walked_file_paths()

        # Deleted but not yet committed files still show up in git's index.
        return sorted(path for path in set(paths) if self._is_file(path))

    def read(self, path: str) -> bytes | None:
        # Only what a push would publish: no ignored or stray files.
        if path not in self.file_paths():
            return None

        with _REAL_OPEN(self.root / path, "rb") as file:
            return file.read()

    def tree(self) -> dict[str, Any]:
        blobs = self.file_paths()
        directories = {
            "/".join(path.split("/")[:depth])
            for path in blobs
            for depth in range(1, path.count("/") + 1)
        }
        entries = [
            {
                "path": path,
                "type": "blob",
                "mode": "100644",
            }
            for path in blobs
        ]
        entries += [
            {
                "path": path,
                "type": "tree",
                "mode": "040000",
            }
            for path in directories
        ]
        entries.sort(key=lambda entry: entry["path"])

        return {
            "sha": "local",
            "truncated": False,
            "tree": entries,
        }

    def _git_file_paths(self) -> list[str] | None:
        try:
            result = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=self.root,
                capture_output=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None

        return [path for path in result.stdout.decode("utf-8").split("\0") if path]

    def _walked_file_paths(self) -> list[str]:
        paths = []
        for directory, directory_names, file_names in os.walk(self.root):
            directory_names[:] = [
                name
                for name in directory_names
                if not name.startswith(".") and name not in _SKIPPED_DIRECTORY_NAMES
            ]
            relative_directory = Path(directory).relative_to(self.root)
            for name in file_names:
                if not name.startswith("."):
                    paths.append((relative_directory / name).as_posix())

        return paths

    def _is_file(self, path: str) -> bool:
        try:
            return stat.S_ISREG(_REAL_STAT(str(self.root / path)).st_mode)
        except OSError:
            return False


class GitHubInterceptor:
    """Routes GitHub raw and tree requests to local repositories.

    The main repo is whatever the staged settings.txt's `repository_url`
    names, re-read on every request since the web UI can change it."""

    def __init__(
        self,
        settings_path: Path,
        main_repository: LocalRepository | None,
        mapped_repositories: dict[str, LocalRepository],
        is_online: Callable[[], bool],
    ) -> None:
        self.settings_path = settings_path
        self.main_repository = main_repository
        self.mapped_repositories = mapped_repositories
        self.is_online = is_online

    def repository_url(self) -> str:
        try:
            with _REAL_OPEN(self.settings_path) as file:
                url = json.loads(file.read()).get("repository_url")
        except (OSError, ValueError, AttributeError):
            url = None

        return url if isinstance(url, str) and url else DEFAULT_REPOSITORY_URL

    def respond(self, method: str, url: str) -> LocalResponse | None:
        """A local answer for this request, or None to let it go out to the
        real network."""
        if method.upper() != "GET":
            return None

        parts = urlsplit(url)
        segments = parts.path.lstrip("/").split("/")
        if parts.hostname == RAW_HOST and len(segments) >= 3:
            return self._respond_raw(url, segments)

        if (
            parts.hostname == API_HOST
            and len(segments) >= 6
            and segments[0] == "repos"
            and segments[3:5] == ["git", "trees"]
        ):
            return self._respond_tree(segments[1], segments[2])

        return None

    def _repository_for(self, owner: str, repo: str) -> LocalRepository | None:
        key = repository_key(owner, repo)
        if key in self.mapped_repositories:
            return self.mapped_repositories[key]

        main_identity = repository_identity(self.repository_url())
        if main_identity is not None and key == repository_key(*main_identity):
            return self.main_repository

        return None

    def _respond_raw(self, url: str, segments: list[str]) -> LocalResponse | None:
        repository = self._repository_for(segments[0], segments[1])
        if repository is None:
            return None

        self._require_online()
        path = self._strip_main_prefix(url) or _strip_ref(segments[2:])
        content = repository.read(path) if path else None
        if content is None:
            # GitHub's raw host answers a missing file with exactly this body.
            return LocalResponse(404, b"404: Not Found")

        return LocalResponse(200, content)

    def _respond_tree(self, owner: str, repo: str) -> LocalResponse | None:
        repository = self._repository_for(owner, repo)
        if repository is None:
            return None

        self._require_online()

        return LocalResponse(200, json.dumps(repository.tree()).encode("utf-8"))

    def _strip_main_prefix(self, url: str) -> str | None:
        # Matching repository_url literally is the only way to know where a
        # branch name containing "/" ends.
        prefix = self.repository_url().rstrip("/") + "/"

        request_path = url.split("?")[0]
        if request_path.lower().startswith(prefix.lower()):
            return request_path[len(prefix) :]

        return None

    def _require_online(self) -> None:
        # Same error the socket stub raises, so offline handling is identical.
        if not self.is_online():
            raise OSError(errno.ENETUNREACH, "Network is unreachable")

    def install(self, session_class: type) -> None:
        """Patches `request`, which every get/post/... goes through."""
        real_request = getattr(session_class, "request")

        def request(
            session: object,
            method: str,
            url: str,
            *args: Any,
            **kwargs: Any,
        ) -> object:
            response = self.respond(method, url)
            if response is None:
                return real_request(session, method, url, *args, **kwargs)

            return response

        setattr(session_class, "request", request)


def _strip_ref(segments: list[str]) -> str:
    # refs/heads/<branch>/..., <branch>/... or HEAD/... ; a branch with "/"
    # in it can't be told apart from a directory here.
    if segments[:2] == ["refs", "heads"]:
        segments = segments[2:]

    return "/".join(segments[1:])
