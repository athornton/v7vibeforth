"""nox configuration for v7vibeforth."""

import nox
from nox_uv import session

# Default sessions.
nox.options.sessions = ["lint", "typing", "test"]

# Other nox defaults.
nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True


@session(uv_only_groups=["lint"], uv_no_install_project=True)
def lint(session: nox.Session) -> None:
    """Run pre-commit hooks."""
    session.run("prek", "run", "--all-files", *session.posargs)


@session(uv_groups=["nox", "typing"])
def typing(session: nox.Session) -> None:
    """Run mypy."""
    session.run(
        "mypy",
        *session.posargs,
        "noxfile.py",
        "src",
    )


@session(uv_groups=["dev", "nox"])
def test(session: nox.Session) -> None:
    """Run tests."""
    session.run("pytest", "-vvv", *session.posargs)
