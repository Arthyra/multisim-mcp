"""Transfer an active native Multisim document to the COM automation session."""

from pathlib import Path

from anyio import CancelScope
from anyio.lowlevel import checkpoint
from mcp import types
from pydantic import BaseModel, Field

from .backend_calls import BackendCallError, backend_result
from .backends import Backend


class HandoffArguments(BaseModel):
    path: str = Field(description="Absolute path to the active native .ms14 source file.")


HANDOFF_TOOL = types.Tool(
    name="handoff_to_com",
    description=(
        "Check the active native document path, stop F5, save the source, release the native "
        "session, and open that saved file in COM. Returns actual paths and the failed stage, "
        "if any. A successful handoff leaves COM connected to the saved document."
    ),
    input_schema=HandoffArguments.model_json_schema(),
)


RECOVERY = {
    "check_backends": "Check gateway_status and restart the gateway after repairing the backend.",
    "validate_native": "Confirm the active Native source and retry with its exact .ms14 path.",
    "stop_native": "Inspect the current Native simulation state before retrying.",
    "save_native": "Inspect the Native document and saved file before retrying or releasing it.",
    "release_native": "Confirm whether Native still owns the saved document before opening it in COM.",
    "connect_com": "Check the COM session. If Native was released, reconnect COM and open the confirmed saved file.",
    "open_com": "Inspect the COM session and reopen the confirmed saved file before running analysis.",
}

HANDOFF_REQUIREMENTS = {
    "native": ("native_handoff_context", "native_control_interactive", "native_save_source", "native_close_copy"),
    "com": ("connect", "open_circuit"),
}


async def handoff_step(receipt, stage, backend, tool, **arguments):
    await checkpoint()
    receipt["stage"] = stage
    # An MCP cancellation must not start the next phase while this one may still be writing.
    with CancelScope(shield=True):
        return await backend_result(backend, tool, **arguments)


def matches_path(value, source, *, exists=False):
    return (
        isinstance(value, str) and Path(value).is_absolute()
        and Path(value).resolve(strict=exists) == source
    )


async def handoff_to_com(native: Backend, com: Backend, arguments: dict) -> dict:
    request = HandoffArguments.model_validate(arguments)
    receipt = {
        "ok": False, "stage": "check_backends", "requested_path": request.path,
        "saved_path": None, "opened_path": None, "native_released": False,
        "native_stopped": False, "save_confirmed": False, "com_connected": False,
    }
    try:
        for backend in (native, com):
            missing = [name for name in HANDOFF_REQUIREMENTS[backend.name] if not backend.supports(name)]
            if missing:
                raise BackendCallError("backend_tools_unavailable", f"{backend.name} missing: {', '.join(missing)}")
            if not await backend.ping():
                raise RuntimeError(f"{backend.name} backend unavailable ({backend.stage})")

        receipt["stage"] = "validate_native"
        source = Path(request.path)
        if not source.is_absolute() or source.suffix.lower() != ".ms14":
            raise ValueError("path must be an absolute .ms14 filename")
        source = source.resolve(strict=True)

        document = await backend_result(native, "native_handoff_context")
        if (document.get("isolated") is not False
                or not matches_path(document.get("source_path"), source)
                or not matches_path(document.get("project_path"), source)):
            raise BackendCallError("path_mismatch", f"Active Native document does not match {source}")

        stopped = await handoff_step(
            receipt, "stop_native", native, "native_control_interactive", action="stop", wait_timeout=15,
        )
        if stopped.get("transition_confirmed") is not True:
            raise RuntimeError(f"Interactive simulation did not stop ({stopped.get('state_name', 'unknown')})")
        receipt["native_stopped"] = True

        saved = await handoff_step(receipt, "save_native", native, "native_save_source")
        receipt["saved_path"] = saved.get("path")
        if saved.get("saved_to_source") is not True or not receipt["saved_path"]:
            raise RuntimeError("Native backend did not confirm a source save")
        if not matches_path(receipt["saved_path"], source, exists=True):
            raise BackendCallError("path_mismatch", f"Native backend saved a different document than {source}")
        receipt["save_confirmed"] = True

        closed = await handoff_step(receipt, "release_native", native, "native_close_copy", save_changes=False)
        if closed.get("closed") is not True and closed.get("detached") is not True:
            raise RuntimeError("Native backend did not release the document")
        receipt["native_released"] = True

        connected = await handoff_step(receipt, "connect_com", com, "connect")
        if connected.get("connected") is not True:
            raise RuntimeError("COM connection was not established")
        receipt["com_connected"] = True

        opened = await handoff_step(receipt, "open_com", com, "open_circuit", path=receipt["saved_path"])
        receipt["opened_path"] = opened.get("file")
        if not matches_path(receipt["opened_path"], source):
            raise BackendCallError("path_mismatch", f"COM backend did not confirm opening {source}")

        receipt.update(ok=True, stage="complete")
    except (OSError, ValueError, RuntimeError) as error:
        receipt["error"] = str(error)
        receipt["error_code"] = getattr(error, "code", "handoff_step_failed")
        receipt["recovery"] = RECOVERY[receipt["stage"]]
    await checkpoint()
    return receipt
