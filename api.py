from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn
import os
import sys
import json

# Ensure app path and shared path are importable
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, "app"))
sys.path.append(os.path.join(current_dir, "shared"))
sys.path.append(os.path.join(current_dir, "..", "shared"))

# Load local .env file if present
env_path = os.path.join(current_dir, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                key, val = line.strip().split("=", 1)
                os.environ[key.strip()] = val.strip()

from app.agent import app as adk_app
from google.adk.runners import InMemoryRunner
from google.genai import types

api = FastAPI(
    title="Hey Hood — Escalation Agent",
    description="ADK 2.0 agent for escalating overdue neighborhood issues",
    version="1.0.0"
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class EscalationInput(BaseModel):
    trigger: str = "scheduled" 

class AgentResponse(BaseModel):
    status: str
    action: str
    result: dict

@api.get("/health")
async def health():
    return {"status": "healthy", "agent": "escalation"}

@api.post("/run", response_model=AgentResponse)
async def run_agent(input_data: EscalationInput):
    try:
        runner = InMemoryRunner(app=adk_app)
        session = await runner.session_service.create_session(
            app_name=adk_app.name,
            user_id="railway_user"
        )
        
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=json.dumps(input_data.model_dump()))]
        )
        
        final_result = {}
        async for event in runner.run_async(
            user_id="railway_user",
            session_id=session.id,
            new_message=new_message
        ):
            if event.output:
                final_result = event.output
                
        # Consolidate action output
        action = "publish"
        if isinstance(final_result, dict):
            action = final_result.get("action", final_result.get("status", "publish"))
            
        return AgentResponse(
            status="success",
            action=str(action),
            result=final_result if isinstance(final_result, dict) else {"output": final_result}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(api, host="0.0.0.0", port=port)
