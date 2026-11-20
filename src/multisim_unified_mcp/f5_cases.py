"""Run a truth table against the actual Multisim F5 session."""

import asyncio
from math import isfinite
from typing import Literal

from anyio import CancelScope
from anyio.lowlevel import checkpoint
from mcp import types
from pydantic import BaseModel, Field, model_validator

from .backend_calls import backend_result
from .backends import Backend


class F5Case(BaseModel):
    name: str = Field(min_length=1)
    inputs: dict[str, Literal[0, 1]] = Field(min_length=1)
    expected: dict[str, Literal[0, 1]] = Field(min_length=1)


class F5Arguments(BaseModel):
    cases: list[F5Case] = Field(min_length=1)
    settle_time_s: float = Field(default=0.001, gt=0, allow_inf_nan=False)
    settle_timeout_s: float = Field(default=5, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_references(self):
        inputs = set(self.cases[0].inputs)
        indicators = set(self.cases[0].expected)
        if inputs & indicators:
            raise ValueError("Input and indicator references must be distinct")
        names = set()
        for case in self.cases:
            if case.name in names:
                raise ValueError(f"Duplicate case name: {case.name}")
            names.add(case.name)
            if set(case.inputs) != inputs or set(case.expected) != indicators:
                raise ValueError(f"{case.name}: every row must use the same input and indicator references")
        return self


VERIFY_F5_TOOL = types.Tool(
    name="native_verify_f5_cases",
    description=(
        "Verify a truth table in the active native Multisim project using F5 switches and red logic indicators. "
        "Compares one sample after the requested simulated interval. This does not detect stability. "
        "Starts F5 if stopped, restores the original switches, and stops F5 if it started it."
    ),
    input_schema=F5Arguments.model_json_schema(),
)


async def f5_snapshot(native: Backend, *, inputs=(), changes=None, indicators=()) -> dict:
    return await backend_result(
        native, "native_f5_logic_snapshot", input_refs=list(inputs),
        changes=changes or {}, indicator_refs=list(indicators),
    )


def simulation_clock(snapshot):
    clock = snapshot.get("simulation_time_s")
    if "simulation_time_s" not in snapshot or (clock is not None and (
        type(clock) not in (int, float) or not isfinite(clock) or clock < 0
    )):
        raise RuntimeError("F5 returned an invalid simulation clock")
    return clock


def input_states(snapshot, refs):
    states = snapshot.get("inputs")
    if not isinstance(states, dict) or set(states) != set(refs) or any(
        type(value) is not int or value not in (0, 1) for value in states.values()
    ):
        raise RuntimeError("F5 did not report valid states for every input switch")
    return states


async def settled_sample(native: Backend, indicators, baseline, settle_s, timeout_s):
    previous = baseline
    try:
        async with asyncio.timeout(timeout_s):
            while True:
                sample = await f5_snapshot(native, indicators=indicators)
                clock = simulation_clock(sample)
                if clock is not None:
                    if previous is not None and clock < previous:
                        raise RuntimeError("F5 simulation time moved backwards")
                    previous = clock
                    if baseline is None:
                        baseline = clock
                    elif clock >= baseline + settle_s:
                        return baseline, sample
                await asyncio.sleep(0.04)
    except TimeoutError:
        return baseline, None


async def verify_f5_cases(native: Backend, arguments: dict) -> dict:
    request = F5Arguments.model_validate(arguments)
    inputs = list(request.cases[0].inputs)
    indicators = list(request.cases[0].expected)
    rows = []
    restore_errors = []
    original_states = None
    initial_run_state = None
    started_here = False
    error = None
    try:
        status = await backend_result(native, "native_interactive_status")
        initial_run_state = status.get("state_name")
        if initial_run_state in ("stopped", "unprepared"):
            started_here = True
            with CancelScope(shield=True):
                started = await backend_result(native, "native_control_interactive", action="start", wait_timeout=10)
            if started.get("transition_confirmed") is not True:
                raise RuntimeError("F5 simulation did not start")
        elif initial_run_state != "running":
            raise RuntimeError(f"F5 must be stopped or running, currently {initial_run_state}")

        await checkpoint()
        initial = await f5_snapshot(native, inputs=inputs, indicators=indicators)
        original_states = input_states(initial, inputs)
        previous_clock = simulation_clock(initial)
        for case in request.cases:
            await checkpoint()
            with CancelScope(shield=True):
                applied = await f5_snapshot(native, inputs=inputs, changes=case.inputs)
            await checkpoint()
            if input_states(applied, inputs) != case.inputs:
                raise RuntimeError("F5 input switches did not reach the requested combination")
            applied_clock = simulation_clock(applied)
            if previous_clock is not None and applied_clock is not None and applied_clock < previous_clock:
                raise RuntimeError("F5 simulation time moved backwards")
            baseline, sample = await settled_sample(
                native, indicators, applied_clock,
                request.settle_time_s, request.settle_timeout_s,
            )
            observed = dict.fromkeys(indicators)
            status = "timeout"
            if sample is not None:
                previous_clock = simulation_clock(sample)
                readings = sample.get("indicators")
                if not isinstance(readings, list) or any(
                    not isinstance(item, dict) or not isinstance(item.get("ref"), str) for item in readings
                ):
                    raise RuntimeError("F5 returned invalid indicator readings")
                for ref in indicators:
                    states = [item.get("state") for item in readings if item["ref"] == ref]
                    if len(states) == 1 and type(states[0]) is int and states[0] in (0, 1):
                        observed[ref] = states[0]
                status = "indeterminate" if None in observed.values() else "measured"
            rows.append({
                "name": case.name, "inputs": case.inputs, "expected": case.expected,
                "observed": observed, "passed": status == "measured" and observed == case.expected,
                "status": status,
                "settle_from_s": baseline,
                "sampled_at_s": None if sample is None else sample["simulation_time_s"],
            })
            if sample is None:
                break
    except RuntimeError as failure:
        error = str(failure)
    finally:
        # Cancellation must not leave a test vector applied or an owned run active.
        with CancelScope(shield=True):
            if original_states is not None:
                try:
                    restored = await f5_snapshot(native, inputs=inputs, changes=original_states)
                    if input_states(restored, inputs) != original_states:
                        raise RuntimeError("switches did not return to their original states")
                except RuntimeError as failure:
                    restore_errors.append(f"Could not restore inputs: {failure}")
            if started_here:
                try:
                    stopped = await backend_result(native, "native_control_interactive", action="stop", wait_timeout=10)
                    if stopped.get("transition_confirmed") is not True:
                        restore_errors.append("Could not confirm that F5 stopped")
                except RuntimeError as failure:
                    restore_errors.append(f"Could not stop F5: {failure}")

    await checkpoint()
    passed_count = sum(row["passed"] for row in rows)
    result = {
        "passed": error is None and not restore_errors and passed_count == len(request.cases),
        "case_count": len(request.cases), "passed_count": passed_count,
        "settle_time_s": request.settle_time_s, "cases": rows,
        "initial_simulation_state": initial_run_state, "started_here": started_here,
        "restore_errors": restore_errors,
    }
    if error is not None:
        result["error"] = error
    return result
