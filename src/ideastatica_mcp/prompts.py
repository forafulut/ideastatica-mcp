"""Reusable workflow prompts."""

from __future__ import annotations


def register(server) -> None:
    @server.prompt(title="Calculate a project and report the results")
    def calculate_and_report(project_file: str, report_format: str = "pdf") -> str:
        """Open a project, calculate every connection, summarize the checks and save a report."""
        return (
            f"Open the IDEA StatiCa Connection project at {project_file} with open_project, list its "
            "connections, run calculate for all of them and then explain the results: for every "
            "connection state whether it passed and list the governing checks with their utilization in "
            "percent (skip entries marked skipped). Highlight anything above 100 %. Then call "
            f"generate_report with format '{report_format}' for all connections and report where the file "
            "was saved. Finally save the calculated project with save_project."
        )

    @server.prompt(title="Parametric study")
    def parametric_study(project_file: str, parameter_key: str, values: str) -> str:
        """Sweep a design parameter over several values and compare the check results."""
        return (
            f"Open {project_file} with open_project and read its parameters with list_parameters. "
            f"For each value in [{values}] set the parameter '{parameter_key}' with update_parameters "
            "(expressions are strings in SI units; read resource ideastatica://docs/parameter-reference if "
            "you need the syntax), run calculate, and record the overall pass/fail and the maximum "
            "utilization per check group. Present the comparison as a table and recommend the most "
            "economical value that still passes. Restore the original parameter value at the end unless "
            "asked otherwise."
        )

    @server.prompt(title="Design a connection from the Connection Library")
    def design_from_library(project_file: str, connection_id: str = "") -> str:
        """Propose library designs for a bare connection, apply the best one and verify it."""
        target = f" connection {connection_id}" if connection_id else " the connection"
        return (
            f"Open {project_file} with open_project. Inspect{target} with list_members and list_operations. "
            "Call propose_library_designs to get candidate design items, pick the most suitable one "
            "(explain why) and apply it with apply_library_design. Check the returned issues, then "
            "calculate the connection and summarize the checks. If it fails, look at the parameters "
            "(list_parameters) and try adjusting the governing dimensions with update_parameters."
        )
