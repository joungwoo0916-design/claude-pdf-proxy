import base64
import json
import os
import time
import fitz  # PyMuPDF
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

app = FastAPI()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
http_client = httpx.AsyncClient(timeout=120.0, limits=httpx.Limits(max_keepalive_connections=50, max_connections=100))

def convert_pdf_base64_to_images(b64_str):
    try:
        pdf_bytes = base64.b64decode(b64_str)
        if not pdf_bytes.startswith(b"%PDF"):
            return None
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3))
            img_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            images.append(img_b64)
        return images
    except Exception as e:
        print(f"[Error] PDF convert failed: {e}")
        return None

@app.post("/v1/chat/completions")
@app.post("/chat/completions")
@app.post("/v1/v1/chat/completions")
async def chat_completions(request: Request):
    data = await request.json()
    messages = data.get("messages", [])
    stream = data.get("stream", True)
    
    anthropic_messages = []
    system_prompt = ""
    first_user_text = "새로운 대화"
    
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        
        if role == "system":
            system_prompt = content
            continue
            
        anthropic_content = []
        if isinstance(content, str):
            anthropic_content.append({"type": "text", "text": content})
            if role == "user" and first_user_text == "새로운 대화":
                first_user_text = content[:20]
        elif isinstance(content, list):
            for part in content:
                p_type = part.get("type")
                if p_type == "text":
                    t_val = part.get("text", "")
                    anthropic_content.append({"type": "text", "text": t_val})
                    if role == "user" and first_user_text == "새로운 대화":
                        first_user_text = t_val[:20]
                elif p_type in ["image_url", "image"]:
                    url = part.get("image_url", {}).get("url", "") or part.get("url", "")
                    if "base64," in url:
                        header, b64_data = url.split("base64,", 1)
                        if "application/pdf" in header or b64_data.startswith("JVBERi"):
                            captured_images = convert_pdf_base64_to_images(b64_data)
                            if captured_images:
                                for img_b64 in captured_images:
                                    anthropic_content.append({
                                        "type": "image",
                                        "source": {"type": "base64", "media_type": "image/png", "data": img_b64}
                                    })
                                continue
                        
                        media_type = "image/png"
                        if "jpeg" in header or "jpg" in header: media_type = "image/jpeg"
                        elif "webp" in header: media_type = "image/webp"
                        anthropic_content.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64_data}
                        })
        anthropic_messages.append({"role": "user" if role == "user" else "assistant", "content": anthropic_content})
    
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": anthropic_messages,
        "max_tokens": 8192,
        "stream": stream
    }
    if system_prompt:
        payload["system"] = system_prompt

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    created_time = int(time.time())

    # 1. 일반 대화 스트리밍 모드
    if stream:
        async def stream_generator():
            async with http_client.stream("POST", "https://api.anthropic.com/v1/messages", json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    err_text = await resp.aread()
                    error_msg = f"⚠️ [클로드 API 오류 {resp.status_code}]: {err_text.decode('utf-8')}"
                    chunk = {"id": "err", "object": "chat.completion.chunk", "created": created_time, "model": "claude-sonnet-5", "choices": [{"index": 0, "delta": {"role": "assistant", "content": error_msg}, "finish_reason": "stop"}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                first_chunk = {"id": "chatcmpl-proxy", "object": "chat.completion.chunk", "created": created_time, "model": "claude-sonnet-5", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}
                yield f"data: {json.dumps(first_chunk)}\n\n"

                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        raw_data = line[6:].strip()
                        try:
                            event = json.loads(raw_data)
                            if event.get("type") == "content_block_delta":
                                chunk_text = event.get("delta", {}).get("text", "")
                                if chunk_text:
                                    chunk = {
                                        "id": "chatcmpl-proxy",
                                        "object": "chat.completion.chunk",
                                        "created": created_time,
                                        "model": "claude-sonnet-5",
                                        "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}]
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"
                        except:
                            continue
                
                stop_chunk = {"id": "chatcmpl-proxy", "object": "chat.completion.chunk", "created": created_time, "model": "claude-sonnet-5", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                yield f"data: {json.dumps(stop_chunk)}\n\n"
                yield "data: [DONE]\n\n"

        response_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
        return StreamingResponse(stream_generator(), headers=response_headers, media_type="text/event-stream")

    # 2. 🌟 대화방 제목 자동 생성 모드 (Non-stream / 초고속 안전 응답 보장)
    else:
        full_text = ""
        try:
            resp = await http_client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
            if resp.status_code == 200:
                res_data = resp.json()
                for part in res_data.get("content", []):
                    if part.get("type") == "text":
                        full_text += part.get("text", "")
        except Exception as e:
            print(f"[Title Generation Warning] {e}")

        # 혹시 클로드 지연 시 안전하게 첫 질문 바탕으로 즉시 제목 부여
        if not full_text.strip():
            full_text = first_user_text.replace("\n", " ").strip()[:20]

        return JSONResponse({
            "id": "chatcmpl-title",
            "object": "chat.completion",
            "created": created_time,
            "model": "claude-sonnet-5",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_text.strip().replace('"', '')
                },
                "finish_reason": "stop"
            }]
        })

@app.api_route("/{path_name:path}", methods=["GET", "POST"])
async def catch_all(path_name: str):
    return {"status": "ok", "path": path_name}
