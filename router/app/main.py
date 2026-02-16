import os
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from .tool_schemas import TOOLS
from .tools import dispatch_tool_call

ROUTER_AUTH_TOKEN = os.getenv("ROUTER_AUTH_TOKEN", "")

app = FastAPI(title="Agent Tool Router", version="0.1.0")


class ToolCall(BaseModel):
    name: str
    arguments: dict


class ToolRequest(BaseModel):
    """Accept both wrapped and flat payload shapes.

    Wrapped (original):  {"tool_call": {"name": "...", "arguments": {...}}}
    Flat (LaunchBase):   {"name": "...", "arguments": {...}}
    """
    tool_call: Optional[ToolCall] = None
    name: Optional[str] = None
    arguments: Optional[dict] = None


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/tools")
def get_tools():
    return {"tools": TOOLS}


def auth(x_router_token: str | None):
    if ROUTER_AUTH_TOKEN and x_router_token != ROUTER_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/tool")
def tool(req: ToolRequest, x_router_token: str | None = Header(default=None)):
    auth(x_router_token)

    if req.tool_call is not None:
        tool_name = req.tool_call.name
        tool_args = req.tool_call.arguments
    elif req.name is not None and req.arguments is not None:
        tool_name = req.name
        tool_args = req.arguments
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either {tool_call: {name, arguments}} or {name, arguments}",
        )

    return dispatch_tool_call(tool_name, tool_args)
