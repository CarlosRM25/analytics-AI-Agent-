"""M2: the committed EDA notebook is valid, executed, and error-free.

Doesn't re-run it (that needs analytics.db) — just guards against committing a
broken or un-executed notebook.
"""

from pathlib import Path

import nbformat

_NB = Path(__file__).resolve().parents[1] / "notebooks" / "01_eda.ipynb"


def test_eda_notebook_executed_and_clean():
    nb = nbformat.read(_NB, as_version=4)
    nbformat.validate(nb)

    code_cells = [c for c in nb.cells if c.cell_type == "code"]
    assert code_cells, "no code cells"
    for i, cell in enumerate(code_cells):
        assert cell.execution_count is not None, f"code cell {i} was not executed before commit"
        errors = [o for o in cell.outputs if o.output_type == "error"]
        assert not errors, f"code cell {i} has an error output: {errors[0].get('ename')}"
