"""Verify logic outputs from real saved Multisim circuits."""

from math import isclose, isfinite
from pathlib import Path
from typing import Literal

from anyio import CancelScope
from anyio.lowlevel import checkpoint
from mcp import types
from pydantic import BaseModel, Field

from .backend_calls import backend_result
from .backends import Backend


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
        "Verify saved .ms14 combinational cases with constant inputs. Run com_connect first; use "
        "com_enum_outputs to find voltage probes. Compare the final samples from one transient run per "
        "case using TTL thresholds by default. Choose a duration that covers propagation time. "
        "The result does not verify timing, initial conditions or output stability."
    ),
    input_schema=LogicArguments.model_json_schema(),
)


def last_sample(analysis, duration):
    # COM reports readiness on the result as a whole. Individual channel
    # records from the multi-output method only contain data and metadata.
    rows = analysis.get("rows") if isinstance(analysis, dict) else None
    if not isinstance(rows, list) or len(rows) != 2:
        raise RuntimeError("no transient data")
    times, values = rows
    if not isinstance(times, list) or not isinstance(values, list) or not times or len(times) != len(values):
        raise RuntimeError("invalid transient sample arrays")
    if any(type(item) not in (int, float) or not isfinite(item) for item in (*times, *values)):
        raise RuntimeError("non-finite or non-numeric transient sample")
    if times[0] < 0 or any(left >= right for left, right in zip(times, times[1:])):
        raise RuntimeError("invalid transient timestamps")
    # Ten uniformly requested samples may start at t=0 and end one interval before duration.
    if times[-1] < 0.9 * duration and not isclose(times[-1], 0.9 * duration, rel_tol=1e-6):
        raise RuntimeError("transient samples end before the final sampling interval")
    return times[-1], values[-1]


async def verify_logic_cases(session: Backend, arguments: dict) -> dict:
    request = LogicArguments.model_validate(arguments)
    if request.low_max_v >= request.high_min_v:
        raise ValueError("low_max_v must be less than high_min_v")
    sample_rate = 10 / request.duration_s
    if not isfinite(sample_rate):
        raise ValueError("duration_s is too small for the requested sample count")

    for case in request.cases:
        path = Path(case.path)
        if not path.is_absolute() or path.suffix.lower() != ".ms14":
            raise ValueError(f"{case.name}: path must be an absolute .ms14 filename")

    cases = []
    for case in request.cases:
        await checkpoint()
        row = {"name": case.name, "path": case.path, "outputs": {}, "passed": False}
        cases.append(row)
        try:
            multi_available = "run_transient_outputs" in session.tools
            signals = list(case.expected)
            if len(signals) > 1 and not multi_available:
                raise RuntimeError("This COM backend cannot sample multiple channels in one run")
            if not multi_available and "run_transient" not in session.tools:
                raise RuntimeError("This COM backend does not offer transient analysis")
            with CancelScope(shield=True):
                opened = await backend_result(session, "open_circuit", path=case.path)
            filename = opened.get("file")
            if not isinstance(filename, str) or Path(filename).resolve() != Path(case.path).resolve():
                raise RuntimeError("COM did not confirm opening the requested circuit")
            sampling = dict(duration=request.duration_s, sample_rate=sample_rate, num_samples=10, max_points=10)
            await checkpoint()
            with CancelScope(shield=True):
                if multi_available:
                    wave = await backend_result(session, "run_transient_outputs", output_names=signals, **sampling)
                else:
                    wave = await backend_result(session, "run_transient", output_name=signals[0], **sampling)
            channels = {signals[0]: wave} if len(signals) == 1 else wave.get("results")
            if wave.get("ready") is not True or not isinstance(channels, dict):
                raise RuntimeError("COM transient did not return all requested outputs")

            timestamp = None
            for signal, expected in case.expected.items():
                try:
                    time_s, volts = last_sample(channels.get(signal), request.duration_s)
                except RuntimeError as error:
                    raise RuntimeError(f"{signal}: {error}") from error
                if timestamp is not None and not isclose(time_s, timestamp, rel_tol=1e-6):
                    raise RuntimeError(f"{signal}: final sample times differ across outputs")
                timestamp = time_s
                observed = None
                if volts <= request.low_max_v:
                    observed = 0
                elif volts >= request.high_min_v:
                    observed = 1
                row["outputs"][signal] = {
                    "expected": expected, "observed": observed, "volts": volts,
                    "time_s": time_s, "passed": observed == expected,
                }
            row["passed"] = all(output["passed"] for output in row["outputs"].values())
        except (OSError, RuntimeError, ValueError) as error:
            row["error"] = str(error)

    await checkpoint()
    passed = sum(row["passed"] for row in cases)
    return {"passed": passed == len(cases), "passed_count": passed, "case_count": len(cases),
            "low_max_v": request.low_max_v, "high_min_v": request.high_min_v, "cases": cases}
