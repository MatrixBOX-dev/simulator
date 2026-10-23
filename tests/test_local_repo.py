import json
from pathlib import Path

import pytest

from matrixbox_simulator.device import local_repo

RAW = "https://raw.githubusercontent.com/MatrixBOX-dev/matrixbox"


@pytest.mark.parametrize(
    ("repository_url", "request_url"),
    [
        (f"{RAW}/refs/heads/main/", f"{RAW}/refs/heads/main/apps/clock/code.py"),
        (f"{RAW}/refs/heads/main/", f"{RAW}/main/apps/clock/code.py"),
        (f"{RAW}/refs/heads/main/", f"{RAW}/HEAD/apps/clock/code.py"),
        (
            f"{RAW}/refs/heads/feat/store/",
            f"{RAW}/refs/heads/feat/store/apps/clock/code.py",
        ),
    ],
    ids=["refs-heads", "branch", "head", "branch-with-slash"],
)
def test_raw_url_serves_the_working_tree_file(
    tmp_path: Path, repository_url: str, request_url: str
) -> None:
    checkout = tmp_path / "matrixbox"
    (checkout / "apps" / "clock").mkdir(parents=True)
    (checkout / "apps" / "clock" / "code.py").write_text("print('clock')\n")
    settings_path = tmp_path / "settings.txt"
    settings_path.write_text(json.dumps({"repository_url": repository_url}))
    interceptor = local_repo.GitHubInterceptor(
        settings_path,
        local_repo.LocalRepository(checkout),
        {},
        lambda: True,
    )

    response = interceptor.respond("GET", request_url)

    assert response is not None
    assert response.status_code == 200
    assert response.text == "print('clock')\n"
