"""Transfer an active native Multisim document to the COM automation session."""

from pathlib import Path

from mcp import types
from pydantic import BaseModel, Field

from .backend_calls import backend_result
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


async def handoff_to_com(native: Backend, com: Backend, arguments: dict) -> dict:
    request = HandoffArguments.model_validate(arguments)
    receipt = {
        "ok": False, "stage": "check_backends", "requested_path": request.path,
        "saved_path": None, "opened_path": None, "native_released": False,
    }
    try:
        for backend in (native, com):
            if not await backend.ping():
                raise RuntimeError(f"{backend.name} backend unavailable ({backend.stage})")

        receipt["stage"] = "validate_native"
        source = Path(request.path)
        if not source.is_absolute() or source.suffix.lower() != ".ms14":
            raise ValueError("path must be an absolute .ms14 filename")
        source = source.resolve(strict=True)

        document = await backend_result(native, "native_handoff_context")
        if (document["isolated"] or not document["source_path"]
                or Path(document["source_path"]).resolve() != source
                or Path(document["project_path"]).resolve() != source):
            raise RuntimeError(f"Active native document does not match {source}")

        receipt["stage"] = "stop_native"
        stopped = await backend_result(
            native, "native_control_interactive", action="stop", wait_timeout=15,
        )
        if not stopped["transition_confirmed"]:
            raise RuntimeError(f"Interactive simulation did not stop ({stopped['state_name']})")

        receipt["stage"] = "save_native"
        saved = await backend_result(native, "native_save_source")
        receipt["saved_path"] = saved.get("path")
        if not saved["saved_to_source"] or not receipt["saved_path"]:
            raise RuntimeError("Native backend did not confirm a source save")
        if Path(receipt["saved_path"]).resolve(strict=True) != source:
            raise RuntimeError(f"Native backend saved a different document than {source}")

        receipt["stage"] = "release_native"
        closed = await backend_result(native, "native_close_copy", save_changes=False)
        if not (closed["closed"] or closed["detached"]):
            raise RuntimeError("Native backend did not release the document")
        receipt["native_released"] = True

        receipt["stage"] = "connect_com"
        connected = await backend_result(com, "connect")
        if not connected["connected"]:
            raise RuntimeError("COM connection was not established")

        receipt["stage"] = "open_com"
        opened = await backend_result(com, "open_circuit", path=receipt["saved_path"])
        receipt["opened_path"] = opened.get("file")
        if not receipt["opened_path"] or Path(receipt["opened_path"]).resolve() != source:
            raise RuntimeError(f"COM backend did not confirm opening {source}")

        receipt.update(ok=True, stage="complete")
    except (OSError, ValueError, RuntimeError) as error:
        receipt["error"] = str(error)
    return receipt
