"""Makes the shared guard-test fixtures visible to every test module here."""

from hooktest import repo_fixture

__all__ = ["repo_fixture"]
