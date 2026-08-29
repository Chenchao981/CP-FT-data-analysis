from __future__ import annotations

import os
from collections.abc import Mapping

_ALLOWED_INHERITED_NAMES = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432",
        "COMSPEC",
        "CONDA_DEFAULT_ENV",
        "CONDA_EXE",
        "CONDA_PREFIX",
        "CONDA_PROMPT_MODIFIER",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROCESSOR_LEVEL",
        "PROCESSOR_REVISION",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)


def isolated_child_environment(
    overrides: Mapping[str, str],
) -> dict[str, str]:
    """Build a functional Windows child environment without TMS/app secrets."""

    inherited = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _ALLOWED_INHERITED_NAMES
    }
    inherited.update({str(key): str(value) for key, value in overrides.items()})
    return inherited
