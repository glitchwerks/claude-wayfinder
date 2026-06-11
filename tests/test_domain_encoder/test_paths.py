"""Tests for spikes.domain_encoder._paths — pure path/repo-id helpers.

No model2vec dependency; these tests run in CI (ubuntu, no spike extra).
This is deliberate: the platform-independence of _is_hf_repo_id is exactly
what CI must validate because Bug 1 only bites on Linux/Mac.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# _is_hf_repo_id — platform-independent repo-id detection
# ---------------------------------------------------------------------------


class TestIsHfRepoId:
    """Unit tests for _is_hf_repo_id.

    Cases mirror the bug report exactly so the fix is directly traceable.
    """

    @pytest.fixture(autouse=True)
    def _import_helper(self) -> None:
        """Import once per test; module must be importable without model2vec."""
        from spikes.domain_encoder._paths import _is_hf_repo_id

        self._fn = _is_hf_repo_id

    # -----------------------------------------------------------------------
    # True cases — should be recognised as HuggingFace repo ids
    # -----------------------------------------------------------------------

    def test_standard_org_name_pair(self) -> None:
        """'minishlab/potion-base-8M' is a canonical HF repo id."""
        assert self._fn("minishlab/potion-base-8M") is True

    def test_simple_user_model_pair(self) -> None:
        """'user/model' with a single slash is a valid repo id."""
        assert self._fn("user/model") is True

    def test_dashes_and_digits_in_name(self) -> None:
        """Names with dashes and digits are valid repo ids."""
        assert self._fn("org-123/my-model-v2") is True

    # -----------------------------------------------------------------------
    # False cases — local paths that must NOT be sent to snapshot_download
    # -----------------------------------------------------------------------

    def test_windows_absolute_forward_slash(self) -> None:
        """'C:/models/foo' has more than one slash — not a repo id."""
        assert self._fn("C:/models/foo") is False

    def test_relative_dot_slash(self) -> None:
        """'./local/model' starts with '.' — local relative path."""
        assert self._fn("./local/model") is False

    def test_relative_dot_only(self) -> None:
        """'.' is a relative path reference."""
        assert self._fn(".") is False

    def test_relative_dot_dot(self) -> None:
        """'..' is a relative path reference."""
        assert self._fn("..") is False

    def test_backslash_path(self) -> None:
        r"""'models\\local' contains a backslash — a Windows path."""
        assert self._fn("models\\local") is False

    def test_multiple_forward_slashes(self) -> None:
        """Paths with more than one '/' are not valid org/name pairs."""
        assert self._fn("a/b/c") is False

    def test_no_slash_at_all(self) -> None:
        """A bare name with no slash is not a valid org/name HF repo id."""
        assert self._fn("modelname") is False

    def test_empty_string(self) -> None:
        """Empty string is not a repo id."""
        assert self._fn("") is False

    def test_leading_slash(self) -> None:
        """'/absolute/path' has a leading slash — not a repo id."""
        assert self._fn("/absolute/path") is False

    # -----------------------------------------------------------------------
    # Platform-independence assertion: no os.path.sep / os.sep usage
    # -----------------------------------------------------------------------

    def test_implementation_does_not_reference_os_path_sep(self) -> None:
        """_is_hf_repo_id must not use os.path.sep or os.sep.

        This is the anti-regression guard for Bug 1: os.path.sep=='/' on
        POSIX, which caused 'minishlab/potion-base-8M' to be treated as a
        local path on Linux/Mac, silently skipping the revision pin.
        """
        import inspect

        from spikes.domain_encoder._paths import _is_hf_repo_id

        src = inspect.getsource(_is_hf_repo_id)
        assert "os.path.sep" not in src, (
            "_is_hf_repo_id must not use os.path.sep (platform-dependent)"
        )
        assert "os.sep" not in src, (
            "_is_hf_repo_id must not use os.sep (platform-dependent)"
        )
