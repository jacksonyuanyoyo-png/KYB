"""`python -m fcc_api.jobs`：对已写出的任务模块各调用一次 run_once。"""

import importlib
import importlib.util

_JOB_MODULES = (
    "fcc_api.jobs.slots",
    "fcc_api.jobs.scan",
    "fcc_api.jobs.audit_export",
    "fcc_api.jobs.retention",
    "fcc_api.jobs.relations",
)


def _run_one(module_name: str) -> None:
    try:
        spec = importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        return
    if spec is None:
        return
    module = importlib.import_module(module_name)
    run_once = getattr(module, "run_once", None)
    if callable(run_once):
        run_once()


def main() -> None:
    for module_name in _JOB_MODULES:
        _run_one(module_name)


if __name__ == "__main__":
    main()
