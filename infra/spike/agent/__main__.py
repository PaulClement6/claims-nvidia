"""One turn of the claim agent (issue #13).

    echo "message" | python -m agent --thread C-2026-001

State: SQLite checkpointer in /sandbox/state, thread id = claim id, so one claim
keeps its conversation across turns (each turn is a new process).
Prints one JSON line: {"answer": ..., "tools": [...], "seconds": ...}.
"""

import argparse
import asyncio
import json
import os
import sys
import time

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

SYSTEM = (
    "You are a claims assistant of an insurance company. Be brief. "
    "Use your tools to look up facts about the customer. Never invent policy numbers. "
    "Copy identifiers exactly, with plain ASCII characters (a normal hyphen, no special hyphen)."
)


async def turn(thread: str, message: str) -> dict:
    t0 = time.monotonic()
    # NVIDIA_API_KEY holds a placeholder in the sandbox. The supervisor swaps in
    # the real key on the Authorization header toward the provider host only.
    llm = ChatOpenAI(
        model=os.environ.get("MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
        base_url=os.environ.get("MODEL_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        api_key=os.environ["NVIDIA_API_KEY"],
        temperature=0,
        max_retries=1,
        timeout=90,
    )
    mcp = MultiServerMCPClient(
        {"gateway": {"transport": "streamable_http", "url": os.environ["MCP_URL"]}}
    )
    allowed = set(os.environ.get("AGENT_TOOLS", "get_customer_policies").split(","))
    tools = [t for t in await mcp.get_tools() if t.name in allowed]
    os.makedirs("/sandbox/state", exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string("/sandbox/state/checkpoints.sqlite") as saver:
        agent = create_agent(llm, tools, system_prompt=SYSTEM, checkpointer=saver)
        out = await agent.ainvoke(
            {"messages": [("user", message)]}, {"configurable": {"thread_id": thread}}
        )
    msgs = out["messages"]
    # Only this turn: the messages after the last user message.
    last_user = max(i for i, m in enumerate(msgs) if isinstance(m, HumanMessage))
    called = [
        {"name": c["name"], "args": c["args"]}
        for m in msgs[last_user:]
        if isinstance(m, AIMessage)
        for c in m.tool_calls
    ]
    return {
        "answer": msgs[-1].content,
        "tools": called,
        "messages": len(msgs),
        "seconds": round(time.monotonic() - t0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread", required=True)
    ap.add_argument("--message", help="default: read stdin")
    a = ap.parse_args()
    message = a.message if a.message is not None else sys.stdin.read()
    print(json.dumps(asyncio.run(turn(a.thread, message.strip()))))


main()
