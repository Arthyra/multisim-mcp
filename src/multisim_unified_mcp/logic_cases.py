"""Verify logic outputs from real saved Multisim circuits."""

from pathlib import Path
from typing import Literal

from mcp import ClientSession, types
from pydantic import BaseModel, Field

from .backend_calls import backend_result


class LogicCase(BaseModel):
    name: str = Field(min_length=1)
    path: str = Field(description="Absolute path to a saved .ms14 with this input state.")
    expected: dict[str, Literal[0, 1]] = Field(
        min_length=1, description="Multisim voltage output names and their expected logic levels."
    )


class LogicArguments(BaseModel):
    cases: list[LogicCase] = Field(min_length=1)
    duration_s: float = Field(default=1e-6, gt=0, allow_inf_nan=False)
    low_max_v: float = Field(default=0.8, allow_inf_nan=False)
    high_min_v: float = Field(default=2.0, allow_inf_nan=False)


VERIFY_LOGIC_TOOL = types.Tool(
    name="com_verify_logic_cases",
    description=(
        "Verify truth-table cases by opening each saved .ms14 and measuring its voltage outputs "
        "in Multisim. Run com_connect before calling. Use com_enum_outputs to find probe names. "
        "Expected bits are compared against the final transient sample and the specified voltage thresholds."
    ),
    input_schema=LogicArguments.model_json_schema(),
)


async def verify_logic_cases(session: ClientSession, arguments: dict) -> dict:
    request = LogicArguments.model_validate(arguments)
    if request.low_max_v >= request.high_min_v:
        raise ValueError("low_max_v must be less than high_min_v")

    for case in request.cases:
        path = Path(case.path)
        if not path.is_absolute() or path.suffix.lower() != ".ms14":
            raise ValueError(f"{case.name}: path must be an absolute .ms14 filename")

    cases = []
    for case in request.cases:
        row = {"name": case.name, "path": case.path, "outputs": {}, "passed": False}
        cases.append(row)
        try:
            await backend_result(session, "open_circuit", path=case.path)
            for signal, expected in case.expected.items():
                wave = await backend_result(
                    session, "run_transient", output_name=signal,
                    duration=request.duration_s, sample_rate=10 / request.duration_s,
                    num_samples=10, max_points=10,
                )
                rows = wave.get("rows", [])
                if not wave.get("ready") or len(rows) != 2 or not rows[0] or not rows[1]:
                    raise RuntimeError(f"{signal}: no transient data")
                volts = rows[1][-1]
                observed = None
                if volts <= request.low_max_v:
                    observed = 0
                elif volts >= request.high_min_v:
                    observed = 1
                row["outputs"][signal] = {
                    "expected": expected, "observed": observed, "volts": volts,
                    "time_s": rows[0][-1], "passed": observed == expected,
                }
            row["passed"] = all(output["passed"] for output in row["outputs"].values())
        except RuntimeError as error:
            row["error"] = str(error)

    passed = sum(row["passed"] for row in cases)
    return {"passed": passed == len(cases), "passed_count": passed, "case_count": len(cases),
            "low_max_v": request.low_max_v, "high_min_v": request.high_min_v, "cases": cases}
