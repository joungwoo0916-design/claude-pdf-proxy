import base64
import json
import os
import fitz  # PyMuPDF
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
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
async def chat_completions(request: Request):
    data = await request.json()
    messages = data.get("messages", [])
    
    anthropic_messages = []
    system_prompt = ""
    
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        
        if role == "system":
            system_prompt = content
            continue
            
        anthropic_content = []
        if isinstance(content, str):
            anthropic_content.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for part in content:
                p_type = part.get("type")
                if p_type == "text":
                    anthropic_content.append({"type": "text", "text": part.get("text", "")})
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
    
    # 🌟 클로드 본사 공식 정식 모델명 지정
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": anthropic_messages,
        "max_tokens": 8192,
        "stream": True
    }
    if system_prompt:
        payload["system"] = system_prompt

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    async def stream_generator():
        # 1. API 키 누락 체크
        if not ANTHROPIC_API_KEY:
            error_msg = "⚠️ [프록시 오류] Railway의 claude-pdf-proxy 서비스에 ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다!"
            chunk = {"id": "err", "object": "chat.completion.chunk", "choices": [{"delta": {"role": "assistant", "content": error_msg}, "index": 0}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
            return

        async with http_client.stream("POST", "https://api.anthropic.com/v1/messages", json=payload, headers=headers) as resp:
            # 2. 클로드 본사 응답이 에러인 경우 화면에 즉시 출력
            if resp.status_code != 200:
                err_text = await resp.aread()
                print(f"[Anthropic API Error] {resp.status_code}: {err_text.decode('utf-8')}")
                error_msg = f"⚠️ [클로드 본사 API 거절 - 코드 {resp.status_code}]: {err_text.decode('utf-8')}"
                chunk = {"id": "err", "object": "chat.completion.chunk", "choices": [{"delta": {"role": "assistant", "content": error_msg}, "index": 0}]}
                yield f"data: {json.dumps(chunk)}\n\n"
                yield "data: [DONE]\n\n"
                return

            # 3. 정상 스트리밍 전달
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    raw_data = line[6:].strip()
                    if raw_data == "[DONE]":
                        yield "data: [DONE]\n\n"
                        break
                    try:
                        event = json.loads(raw_data)
                        if event.get("type") == "content_block_delta":
                            chunk_text = event.get("delta", {}).get("text", "")
                            if chunk_text:
                                chunk = {
                                    "id": "chatcmpl-proxy",
                                    "object": "chat.completion.chunk",
                                    "choices": [{"delta": {"role": "assistant", "content": chunk_text}, "index": 0}]
                                }
                                yield f"data: {json.dumps(chunk)}\n\n"
                    except:
                        continue
        yield "data: [DONE]\n\n"

    response_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    }

    return StreamingResponse(stream_generator(), headers=response_headers, media_type="text/event-stream")

@app.post("/v1/responses")
@app.post("/responses")
async def dummy_responses():
    return {"status": "ok"}

@app.get("/v1/models")
async def get_models():
    return {"data": [{"id": "claude-sonnet-5", "object": "model"}]}
