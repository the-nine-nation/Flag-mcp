# Interactive Feedback MCP
# Developed by Fábio Ferreira (https://x.com/fabiomlferreira)
# Inspired by/related to dotcursorrules.com (https://dotcursorrules.com/)
# Enhanced by Pau Oliva (https://x.com/pof) with ideas from https://github.com/ttommyth/interactive-mcp
import os
import sys
import json
import base64
import mimetypes
import tempfile
import subprocess
import asyncio

from typing import Any

from anyio import BrokenResourceError
from fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from pydantic import Field

# The log_level is necessary for Cline to work: https://github.com/jlowin/fastmcp/issues/81
os.environ["FASTMCP_LOG_LEVEL"] = "ERROR"
mcp = FastMCP("Interactive Feedback MCP")

_FEEDBACK_TIMEOUT_SEC = int(os.getenv("INTERACTIVE_FEEDBACK_TIMEOUT_SEC", "60000"))
_feedback_lock = asyncio.Lock()


def _empty_result() -> dict[str, Any]:
    return {"interactive_feedback": "", "images": [], "temp_images": []}


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    """Best-effort process shutdown used on timeout/cancellation."""
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except Exception:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass


def _normalize_result(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_result()
    text = payload.get("interactive_feedback")
    if not isinstance(text, str):
        text = ""
    images = payload.get("images")
    if not isinstance(images, list):
        images = []
    images = [p for p in images if isinstance(p, str) and os.path.isfile(p)]
    temp_images = payload.get("temp_images")
    if not isinstance(temp_images, list):
        temp_images = []
    temp_images = [p for p in temp_images if isinstance(p, str) and p in images]
    return {"interactive_feedback": text, "images": images, "temp_images": temp_images}


async def launch_feedback_ui(
    summary: str,
    predefinedOptions: list[str] | None = None,
    prompt_images: list[str] | None = None,
) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        output_file = tmp.name

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        feedback_ui_path = os.path.join(script_dir, "feedback_ui.py")

        args = [
            sys.executable,
            "-u",
            feedback_ui_path,
            "--prompt", summary,
            "--output-file", output_file,
            "--predefined-options", "|||".join(str(opt) for opt in predefinedOptions) if predefinedOptions else "",
            "--prompt-images", "|||".join(str(p) for p in prompt_images) if prompt_images else "",
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )

        try:
            await asyncio.wait_for(proc.wait(), timeout=_FEEDBACK_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            await _terminate_process(proc)
            return _empty_result()
        except asyncio.CancelledError:
            await _terminate_process(proc)
            return _empty_result()

        if proc.returncode != 0:
            return _empty_result()

        try:
            with open(output_file, "r", encoding="utf-8") as f:
                result = json.load(f)
        except Exception:
            return _empty_result()
        return _normalize_result(result)
    except Exception:
        return _empty_result()
    finally:
        if os.path.exists(output_file):
            os.unlink(output_file)


def _build_content_blocks(result: dict[str, Any]) -> list:
    """Convert feedback result dict into a list of MCP content blocks (Text + Image)."""
    blocks: list = []
    temp_images = set(result.get("temp_images", []))

    text = result.get("interactive_feedback", "")
    if text:
        blocks.append(TextContent(type="text", text=text))

    for img_path in result.get("images", []):
        try:
            if os.path.isfile(img_path):
                mime = mimetypes.guess_type(img_path)[0] or "image/png"
                with open(img_path, "rb") as img_f:
                    data = base64.standard_b64encode(img_f.read()).decode("ascii")
                blocks.append(ImageContent(type="image", data=data, mimeType=mime))
        except Exception:
            pass
        finally:
            if img_path in temp_images:
                try:
                    if os.path.isfile(img_path):
                        os.remove(img_path)
                except Exception:
                    pass

    if not blocks:
        blocks.append(TextContent(type="text", text=""))

    return blocks


@mcp.tool()
async def interactive_feedback(
    message: str = Field(default="", description="The specific question for the user"),
    predefined_options: list = Field(default=None, description="Predefined options for the user to choose from (optional)"),
    message_images: list = Field(default=None, description="Image paths to render in the prompt area (optional)"),
) -> list:
    """Request interactive feedback from the user. Supports text and image attachments.
**使用方式**:
- 当你通过此工具询问用户时,务必说明需要用户确定的内容。
- 请在完成用户布置的工作后调用该工具时,提供"结束"的选项,如果用户选择,则本次请求结束.
**使用技巧**:
- 请给出可选项,让用户选择,选项中请包涵你最推荐的选项,并说明理由.
- 针对每个选项,请给出对应的描述,让用户了解每个选项的含义.
**什么时候使用**:
- 每当你全部工作完成时，请调用此工具，而非直接结束流程.
- 每当你遇到需要用户确定的点时(例如进行需求讨论,或者执行任务中遇到重要分岔路口时),务必调用此工具."""
    predefined_options_list = predefined_options if isinstance(predefined_options, list) else None
    prompt_images_list = (
        [str(p) for p in message_images if isinstance(p, str)] if isinstance(message_images, list) else None
    )
    async with _feedback_lock:
        result = await launch_feedback_ui(message, predefined_options_list, prompt_images_list)
        return _build_content_blocks(result)

if __name__ == "__main__":
    try:
        try:
            mcp.run(transport="stdio")
        except (BrokenPipeError, EOFError, KeyboardInterrupt):
            # Normal shutdown paths when the host terminates stdio.
            pass
    except* BrokenResourceError:
        # Cursor may close stdio abruptly; treat as normal shutdown.
        pass
