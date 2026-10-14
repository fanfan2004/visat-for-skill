"""Fixed task definitions and disclosure triggers for the 014/015 extension."""

TASKS = {
    "task-014": {
        "benchmark": "energy-unit-commitment",
        "inputs": ["network.json"],
        "output_files": ["report.json"],
        "imports": ["pytest", "numpy", "scipy.optimize", "yaml"],
        "modules": {
            "unit-commitment-data-modeling": {
                "description": "Parse resource fields, array shapes, periods, and report conventions.",
                "triggers": ["uc_schema", "uc_missing_output"],
            },
            "unit-commitment-operating-rules": {
                "description": "Formulate transitions, reserve, ramping, initial conditions, and operating costs.",
                "triggers": ["uc_operating_rules"],
            },
            "milp-solver-workflow": {
                "description": "Build sparse MILP constraints, extract incumbents, and validate solver results.",
                "triggers": ["uc_solver_or_cost"],
            },
        },
    },
    "task-015": {
        "benchmark": "xlsx-recover-data",
        "inputs": ["nasa_budget_incomplete.xlsx"],
        "output_files": ["nasa_budget_recovered.xlsx"],
        "imports": ["pytest", "openpyxl", "pandas", "yaml"],
        "modules": {
            "data-reconciliation": {
                "description": "Recover missing cells from totals, shares, YoY, CAGR, and cross-sheet dependencies.",
                "triggers": ["xlsx_value_or_consistency"],
            },
            "xlsx": {
                "description": "Read, edit, preserve, recalculate, and check Excel workbooks.",
                "triggers": ["xlsx_file_or_type"],
            },
        },
    },
}

CONDITIONS = ("noskill", "flat", "pd", "pd-m", "noskill-multi", "vista")
PROTOCOL = "sj4-extension-v1"
