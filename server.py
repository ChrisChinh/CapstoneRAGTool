import os
import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# Lazy import of the heavy RAG model to keep the server start lightweight
from importlib import import_module


# -----------------------------
# OpenAI-like schema models
# -----------------------------

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
	role: Role
	content: str


class ChatCompletionRequest(BaseModel):
	model: Optional[str] = Field(default=None, description="Model name (ignored, for compatibility)")
	messages: List[ChatMessage]
	temperature: Optional[float] = 0.2
	stream: Optional[bool] = False
	max_tokens: Optional[int] = None  # ignored; provided for compatibility


class ChatMessageOut(BaseModel):
	role: Role
	content: str


class Choice(BaseModel):
	index: int
	message: ChatMessageOut
	finish_reason: Optional[str] = "stop"


class Usage(BaseModel):
	prompt_tokens: int = 0
	completion_tokens: int = 0
	total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
	id: str
	object: Literal["chat.completion"] = "chat.completion"
	created: int
	model: str
	choices: List[Choice]
	usage: Usage = Usage()


class DeltaMessage(BaseModel):
	role: Optional[Role] = None
	content: Optional[str] = None


class ChoiceDelta(BaseModel):
	index: int
	delta: DeltaMessage
	finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
	id: str
	object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
	created: int
	model: str
	choices: List[ChoiceDelta]


# -----------------------------
# App and model setup
# -----------------------------

APP_NAME = "CapstoneRAGTool API"
MODEL_ID = os.getenv("RAG_MODEL_ID", "capstone-rag")

app = FastAPI(title=APP_NAME, version="1.0.0")

# Permissive CORS by default for local dev
app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


rag_model: Any = None
rag_model_error: Optional[str] = None


@app.on_event("startup")
def _maybe_warm() -> None:
	# Do nothing on startup; model will be loaded lazily on first use.
	pass


def _ensure_model() -> None:
	global rag_model, rag_model_error
	if rag_model is not None:
		return
	try:
		model_module = import_module("model")
		RagModel = getattr(model_module, "Model")
		rag_model = RagModel()
		rag_model_error = None
	except Exception as e:
		rag_model_error = str(e)
		rag_model = None


# -----------------------------
# Helper functions
# -----------------------------

def _messages_to_prompt(messages: List[ChatMessage]) -> str:
	# Convert a list of chat messages into a flattened prompt string
	# Preserve order; simple tagged transcript format
	lines: List[str] = []
	for m in messages:
		role = m.role.upper()
		lines.append(f"{role}: {m.content}")
	return "\n".join(lines).strip()


def _fake_tokenize(text: str, chunk_size: int = 60) -> List[str]:
	# Simple chunker to simulate streaming tokens
	return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


# -----------------------------
# Endpoints (OpenAI-like)
# -----------------------------


@app.get("/v1/models")
def list_models() -> Dict[str, Any]:
	created = int(time.time())
	return {
		"object": "list",
		"data": [
			{
				"id": MODEL_ID,
				"object": "model",
				"created": created,
				"owned_by": "local",
			}
		],
	}


@app.get("/health")
def health() -> Dict[str, Any]:
	_ensure_model()
	if rag_model is None:
		return {"status": "degraded", "detail": rag_model_error or "model not available"}
	try:
		ok = rag_model.check_connection()
		return {"status": "ok" if ok else "degraded"}
	except Exception as e:
		return {"status": "degraded", "detail": str(e)}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
	if not req.messages:
		raise HTTPException(status_code=400, detail="messages must be a non-empty array")
	_ensure_model()
	if rag_model is None:
		raise HTTPException(status_code=503, detail=f"Model unavailable: {rag_model_error}")

	created = int(time.time())
	completion_id = f"chatcmpl-{uuid.uuid4().hex}"
	prompt_text = _messages_to_prompt(req.messages)

	try:
		full_text = rag_model.run(prompt_text)
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"Model error: {e}")

	model_name = req.model or MODEL_ID

	if req.stream:
		def event_stream():
			# Initial role event
			first_chunk = ChatCompletionChunk(
				id=completion_id,
				created=created,
				model=model_name,
				choices=[ChoiceDelta(index=0, delta=DeltaMessage(role="assistant"))],
			)
			yield f"data: {first_chunk.model_dump_json()}\n\n"

			for piece in _fake_tokenize(full_text):
				chunk = ChatCompletionChunk(
					id=completion_id,
					created=created,
					model=model_name,
					choices=[ChoiceDelta(index=0, delta=DeltaMessage(content=piece))],
				)
				yield f"data: {chunk.model_dump_json()}\n\n"

			# Final stop signal
			final_chunk = ChatCompletionChunk(
				id=completion_id,
				created=created,
				model=model_name,
				choices=[ChoiceDelta(index=0, delta=DeltaMessage(), finish_reason="stop")],
			)
			yield f"data: {final_chunk.model_dump_json()}\n\n"
			yield "data: [DONE]\n\n"

		return StreamingResponse(event_stream(), media_type="text/event-stream")

	# Non-streaming
	response = ChatCompletionResponse(
		id=completion_id,
		created=created,
		model=model_name,
		choices=[
			Choice(
				index=0,
				message=ChatMessageOut(role="assistant", content=full_text),
				finish_reason="stop",
			)
		],
		usage=Usage(),
	)
	return JSONResponse(content=response.model_dump())


if __name__ == "__main__":
	import uvicorn

	host = os.getenv("HOST", "0.0.0.0")
	port = int(os.getenv("PORT", "8000"))
	uvicorn.run("server:app", host=host, port=port, reload=False)

