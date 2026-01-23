import os
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
    tool_call: ToolCall

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
    return dispatch_tool_call(req.tool_call.name, req.tool_call.arguments)
