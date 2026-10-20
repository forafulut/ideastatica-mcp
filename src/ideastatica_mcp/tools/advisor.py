"""Tools: rule-based design advice, multi-parameter optimisation, load combinations, parameter diagnosis."""

from __future__ import annotations

import itertools
import re
import time
from typing import Any

import anyio
from ideastatica_connection_api.models.con_load_effect import ConLoadEffect
from ideastatica_connection_api.models.con_load_effect_member_load import ConLoadEffectMemberLoad
from ideastatica_connection_api.models.con_load_effect_position_enum import ConLoadEffectPositionEnum
from ideastatica_connection_api.models.con_load_effect_section_load import ConLoadEffectSectionLoad
from ideastatica_connection_api.models.idea_parameter_update import IdeaParameterUpdate
from mcp.server.mcpserver import Context

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import compact_parameter, to_jsonable
from ideastatica_mcp.tools.common import READ, WRITE, to_expression
from ideastatica_mcp.tools.workflows import run_calculation, summarize_connection_result

FORCE_KEYS = ("n", "vy", "vz", "mx", "my", "mz")

# Which parameter keys/descriptions typically drive which check group (heuristics, case-insensitive).
PARAMETER_HINTS = {
    "Plates": [r"thick", r"\bpt\b", r"\bt\b", r"plate"],
    "Bolts": [r"bolt", r"\bnt\b", r"\bnb\b", r"row", r"diam", r"\bm\d\d"],
    "Welds": [r"weld", r"\baw\b", r"throat", r"\ba\b"],
    "Anchors": [r"anchor", r"embed"],
    "Concrete block": [r"concrete", r"block", r"grout"],
}

ADVICE = {
    "Plates": "increase the governing plate thickness one step (e.g. 15 -> 20 mm), add stiffeners, or use higher steel grade",
    "Bolts": "use a bigger bolt assembly (M20 -> M24) or a higher grade (8.8 -> 10.9), add a bolt row, or increase edge/lever distances",
    "Welds": "increase the weld throat size (auto_weld_sizing sizes them to full strength), switch fillet -> double fillet / butt weld, or lengthen the weld",
    "Anchors": "increase anchor diameter/embedment, add anchors, add shear lug or washer plates",
    "Concrete block": "enlarge the base plate or foundation block, increase concrete grade or grout quality",
    "Analysis": "the analysis did not converge or load percentage was limited - check load equilibrium and member model settings",
    "Buckling": "add stiffeners / reduce slender plate widths; check that buckling factor exceeds the code limit",
}


def _progress(ctx: Context | None, value: float, message: str) -> None:
    if ctx is None:
        return
    try:
        anyio.from_thread.run(ctx.report_progress, float(value), 100.0, message)
    except Exception:  # noqa: BLE001
        pass


def _last_results(session, pid: str, cid: int) -> dict:
    raw = session.invoke("calculation", "get_results", pid, [cid]) or []
    if not raw:
        raise IdeaError("no results yet - run calculate first")
    data = to_jsonable(raw[0]) or {}
    checks = [
        {"name": c.get("name"), "utilization_percent": c.get("checkValue"), "passed": c.get("checkStatus"), "skipped": c.get("skipped"), "load_case_id": c.get("loadCaseId")}
        for c in data.get("checkResSummary") or []
    ]
    return {"connection_id": cid, "passed": all(c["passed"] for c in checks if not c.get("skipped")), "checks": checks, "raw": data}


def _candidate_parameters(params: list[Any], group: str) -> list[dict]:
    patterns = [re.compile(p, re.IGNORECASE) for p in PARAMETER_HINTS.get(group, [])]
    out = []
    for p in params:
        text = f"{p.key or ''} {p.description or ''}"
        if any(pat.search(text) for pat in patterns):
            out.append(compact_parameter(p))
    return out


def _bump(expression: str, factor: float) -> str | None:
    try:
        value = float(expression)
    except (TypeError, ValueError):
        return None
    if value == int(value) and abs(value) >= 1:
        return str(int(round(value * factor)) if factor > 1 else int(value))
    return f"{value * factor:.4f}".rstrip("0").rstrip(".")


def register(server, session) -> None:
    @server.tool(annotations=WRITE)
    def design_advisor(
        ctx: Context,
        connection_id: int | None = None,
        project_id: str | None = None,
        target_utilization_percent: float = 90.0,
        apply: bool = False,
        step_factor: float = 1.25,
    ) -> dict:
        """Read the last results and propose concrete changes for the governing / failing checks.

        Suggestions are rule based (plate thickness, bolt assembly/rows, weld size, anchors,
        concrete block) and are linked to the connection's parameters when their key or
        description matches. apply=true increases the first matching numeric parameter of the
        governing check by step_factor (or runs auto weld sizing for welds), recalculates and
        reports the new state. Use target_utilization_percent to also flag over-designed checks.
        """
        with tool_errors("design_advisor"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            result = _last_results(session, pid, cid)
            params = session.invoke("parameter", "get_parameters", pid, cid, include_hidden=None) or []
            checks = [c for c in result["checks"] if not c.get("skipped") and c.get("name") != "Analysis"]
            problems = sorted(
                [c for c in checks if (c.get("utilization_percent") or 0) > target_utilization_percent or c.get("passed") is False],
                key=lambda c: c.get("utilization_percent") or 0,
                reverse=True,
            )
            over_designed = [c for c in checks if (c.get("utilization_percent") or 0) < 40 and c.get("name") in ("Plates", "Bolts", "Welds")]
            advice = []
            for c in problems:
                advice.append(
                    {
                        "check": c["name"],
                        "utilization_percent": c.get("utilization_percent"),
                        "passed": c.get("passed"),
                        "advice": ADVICE.get(c["name"], "review the governing component"),
                        "matching_parameters": _candidate_parameters(params, c["name"]),
                        "action_tools": ["auto_weld_sizing"] if c["name"] == "Welds" else ["update_parameters", "optimize_parameter", "apply_library_design"],
                    }
                )
            applied = None
            if apply and problems:
                top = problems[0]
                _progress(ctx, 30, f"applying change for {top['name']}")
                if top["name"] == "Welds":
                    session.invoke("operation", "pre_design_welds", pid, cid, design_type=None)
                    applied = {"check": "Welds", "action": "pre_design_welds (full strength)"}
                else:
                    candidates = _candidate_parameters(params, top["name"])
                    numeric = next((p for p in candidates if _bump(str(p.get("expression")), step_factor)), None)
                    if numeric is None:
                        applied = {"check": top["name"], "action": "no numeric parameter matched - change the geometry via a template or parameters manually"}
                    else:
                        new = _bump(str(numeric["expression"]), step_factor)
                        session.invoke("parameter", "update", pid, cid, idea_parameter_update=[IdeaParameterUpdate(key=numeric["key"], expression=new)])
                        applied = {"check": top["name"], "parameter": numeric["key"], "from": numeric["expression"], "to": new}
                _progress(ctx, 60, "recalculating")
                new_result = run_calculation(session, pid, [cid])[0]
                applied["result"] = summarize_connection_result(new_result)
            return {
                "connection_id": cid,
                "passed": result["passed"],
                "problems": advice,
                "over_designed": [{"check": c["name"], "utilization_percent": c.get("utilization_percent")} for c in over_designed],
                "applied": applied,
            }

    @server.tool(annotations=WRITE)
    def optimize_design(
        ctx: Context,
        parameters: dict[str, list],
        connection_id: int | None = None,
        project_id: str | None = None,
        objective: str = "cost",
        max_runs: int = 40,
        restore: bool = True,
    ) -> dict:
        """Grid search over several parameters at once: every combination is applied, calculated and scored.

        parameters: {"PT": [0.012, 0.015, 0.02], "NT": [2, 3]} (SI units / expressions).
        objective: "cost" (production cost estimate, lowest passing wins), "utilization" (highest
        passing max utilization, i.e. least over-design) or "margin" (lowest max utilization).
        Stops after max_runs combinations. Restores the original expressions unless restore=false.
        Returns all rows plus the best passing combination.
        """
        with tool_errors("optimize_design"):
            if not parameters:
                raise IdeaError("parameters must contain at least one key with a list of values")
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            current = {p.key: p.expression for p in (session.invoke("parameter", "get_parameters", pid, cid, include_hidden=True) or [])}
            missing = [k for k in parameters if k not in current]
            if missing:
                raise IdeaError(f"unknown parameters: {missing}; available: {sorted(current)}")
            keys = list(parameters)
            combos = list(itertools.product(*[list(v) for v in parameters.values()]))[: max(1, int(max_runs))]
            rows: list[dict] = []
            best = None
            started = time.monotonic()
            try:
                for index, combo in enumerate(combos):
                    values = {k: to_expression(v) for k, v in zip(keys, combo)}
                    _progress(ctx, 100.0 * index / len(combos), ", ".join(f"{k}={v}" for k, v in values.items()))
                    response = session.invoke("parameter", "update", pid, cid, idea_parameter_update=[IdeaParameterUpdate(key=k, expression=v) for k, v in values.items()])
                    failed = to_jsonable(getattr(response, "failed_validations", None)) or []
                    row: dict = {"values": values}
                    if failed:
                        row["error"] = failed[0].get("message") or "validation failed"
                        rows.append(row)
                        continue
                    result = summarize_connection_result(run_calculation(session, pid, [cid])[0])
                    row.update(result)
                    if objective == "cost":
                        try:
                            cost = session.invoke("connection", "get_production_cost", pid, cid)
                            row["cost"] = getattr(cost, "total_estimated_cost", None)
                        except Exception:  # noqa: BLE001
                            row["cost"] = None
                    rows.append(row)
                    if not row.get("passed"):
                        continue
                    if objective == "cost":
                        key = row.get("cost") if row.get("cost") is not None else float("inf")
                        better = best is None or key < best[0]
                    elif objective == "margin":
                        key = row.get("max_utilization_percent") or 0
                        better = best is None or key < best[0]
                    else:
                        key = row.get("max_utilization_percent") or 0
                        better = best is None or key > best[0]
                    if better:
                        best = (key, row)
            finally:
                if restore:
                    session.invoke("parameter", "update", pid, cid, idea_parameter_update=[IdeaParameterUpdate(key=k, expression=current[k]) for k in keys if current[k] is not None])
            return {
                "connection_id": cid,
                "objective": objective,
                "runs": len(rows),
                "duration_seconds": round(time.monotonic() - started, 1),
                "best": best[1] if best else None,
                "rows": rows,
                "restored": restore,
            }

    @server.tool(annotations=WRITE)
    def generate_load_combinations(
        characteristic_loads: list[dict],
        connection_id: int | None = None,
        project_id: str | None = None,
        code: str = "ec",
        replace_existing: bool = False,
    ) -> dict:
        """Create ULS load effects from characteristic loads using code combination factors.

        characteristic_loads: [{"name": "G", "category": "permanent"|"variable"|"wind"|"snow",
        "member_loads": [{"member_id": 2, "position": "End", "n": 0, "vy": 0, "vz": -50000, "mx": 0,
        "my": 30000, "mz": 0}]}] in N / Nm. code "ec" (Eurocode EN 1990 eq. 6.10: 1.35 G + 1.5 Q_lead
        + 1.5 psi0 Q_other, psi0 = 0.7 imposed / 0.6 wind / 0.5 snow) or "aisc" (1.4 D; 1.2 D + 1.6 L
        + 0.5 (W or S); 1.2 D + 1.0 W + L). One load effect is added per combination; existing
        effects are kept unless replace_existing=true.
        """
        with tool_errors("generate_load_combinations"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            loads = []
            for item in characteristic_loads:
                cat = str(item.get("category", "variable")).lower()
                loads.append({"name": item.get("name") or cat, "category": cat, "member_loads": item.get("member_loads") or []})
            if not loads:
                raise IdeaError("characteristic_loads is empty")
            perm = [l for l in loads if l["category"] in ("permanent", "dead", "g", "d")]
            var = [l for l in loads if l not in perm]
            combos: list[tuple[str, dict[str, float]]] = []
            code = code.lower()
            if code.startswith("ec"):
                psi0 = {"wind": 0.6, "snow": 0.5}
                if not var:
                    combos.append(("1.35G", {l["name"]: 1.35 for l in perm}))
                for lead in var:
                    factors = {l["name"]: 1.35 for l in perm}
                    factors[lead["name"]] = 1.5
                    for other in var:
                        if other is not lead:
                            factors[other["name"]] = 1.5 * psi0.get(other["category"], 0.7)
                    combos.append((f"1.35G+1.5{lead['name']}", factors))
            elif code.startswith("aisc") or code.startswith("us"):
                combos.append(("1.4D", {l["name"]: 1.4 for l in perm}))
                live = [l for l in var if l["category"] in ("variable", "live", "l", "imposed")]
                env = [l for l in var if l["category"] in ("wind", "snow", "w", "s")]
                if live:
                    f = {l["name"]: 1.2 for l in perm}
                    f.update({l["name"]: 1.6 for l in live})
                    f.update({l["name"]: 0.5 for l in env})
                    combos.append(("1.2D+1.6L+0.5(W/S)", f))
                for e in env:
                    f = {l["name"]: 1.2 for l in perm}
                    f[e["name"]] = 1.0 if e["category"] in ("wind", "w") else 1.6
                    f.update({l["name"]: 1.0 for l in live})
                    combos.append((f"1.2D+{e['name']}+L", f))
            else:
                raise IdeaError("code must be 'ec' or 'aisc'")
            if replace_existing:
                for le in session.invoke("load_effect", "get_load_effects", pid, cid) or []:
                    session.invoke("load_effect", "delete_load_effect", pid, cid, int(le.id))
            created = []
            for name, factors in combos:
                per_member: dict[tuple[int, str], dict[str, float]] = {}
                for load in loads:
                    factor = factors.get(load["name"], 0.0)
                    if not factor:
                        continue
                    for ml in load["member_loads"]:
                        key = (int(ml.get("member_id", ml.get("memberId"))), str(ml.get("position", "End")))
                        acc = per_member.setdefault(key, {k: 0.0 for k in FORCE_KEYS})
                        for k in FORCE_KEYS:
                            acc[k] += factor * float(ml.get(k, 0.0) or 0.0)
                effect = ConLoadEffect(
                    name=name,
                    active=True,
                    is_percentage=False,
                    member_loadings=[
                        ConLoadEffectMemberLoad(member_id=mid, position=ConLoadEffectPositionEnum(pos.capitalize()), section_load=ConLoadEffectSectionLoad(**vals))
                        for (mid, pos), vals in per_member.items()
                    ],
                )
                result = session.invoke("load_effect", "add_load_effect", pid, cid, con_load_effect=effect)
                created.append({"id": getattr(result, "id", None), "name": name, "factors": factors, "member_loads": [{"member_id": mid, "position": pos, **vals} for (mid, pos), vals in per_member.items()]})
            return {"connection_id": cid, "code": code, "created": created}

    @server.tool(annotations=READ)
    def diagnose_parameters(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Explain parameter problems: lists every parameter (hidden ones too) with its validation status, evaluates each expression and reports bound violations and errors."""
        with tool_errors("diagnose_parameters"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            params = session.invoke("parameter", "get_parameters", pid, cid, include_hidden=True) or []
            rows = []
            problems = []
            for p in params:
                row = compact_parameter(p)
                evaluated = None
                try:
                    evaluated = session.invoke("parameter", "evaluate_expression", pid, cid, body=p.expression or "")
                except Exception as exc:  # noqa: BLE001
                    row["evaluation_error"] = str(exc)[:200]
                row["evaluated"] = evaluated
                validation = to_jsonable(getattr(p, "parameter_validation", None)) or {}
                for bound, key in (("lower", "lowerBoundEvaluated"), ("upper", "upperBoundEvaluated")):
                    limit = validation.get(key)
                    try:
                        value = float(p.value) if p.value is not None else None
                    except (TypeError, ValueError):
                        value = None
                    if limit is not None and value is not None and ((bound == "lower" and value < limit) or (bound == "upper" and value > limit)):
                        problems.append({"key": p.key, "issue": f"value {value} violates {bound} bound {limit}", "bound_expression": validation.get(f"{bound}Bound")})
                if validation.get("validationExpression") and validation.get("validationExpressionEvaluated") is False:
                    problems.append({"key": p.key, "issue": "validation expression is false", "expression": validation.get("validationExpression"), "message": validation.get("message")})
                if (row.get("validation_status") or "").lower() == "error" and not any(x["key"] == p.key for x in problems):
                    problems.append({"key": p.key, "issue": "status Error", "message": validation.get("message") or row.get("validation_message") or "(no message from the service)"})
                rows.append(row)
            return {"connection_id": cid, "count": len(rows), "problems": problems, "parameters": rows}
