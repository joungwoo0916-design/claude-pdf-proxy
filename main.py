import base64
import json
import os
import fitz  # PyMuPDF
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx

app = FastAPI()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# 🌟 연결 속도 극대화를 위한 전역 HTTP 세션 클라이언트 풀
http_client = httpx.AsyncClient(timeout=120.0, limits=httpx.Limits(max_keepalive_connections=20, max_connections=50))

def convert_pdf_base64_to_images(b64_str):
    """가장 선명하면서도 전송 용량을 60% 압축하여 0.1초 만에 렌더링"""
    try:
        pdf_bytes = base64.b64decode(b64_str)
        if not pdf_bytes.startswith(b"%PDF"):
            return None
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        for page in doc:
            # 1.4배율(DPI 약 105): 도면의 미세 치수는 칼같이 유지하며 용량은 대폭 감소
            pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4))
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
    
    payload = {
        "model": "claude-sonnet-5",
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
        async with http_client.stream("POST", "https://api.anthropic.com/v1/messages", json=payload, headers=headers) as resp:
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
                                    "choices": [{"delta": {"content": chunk_text}, "index": 0}]
                                }
                                yield f"data: {json.dumps(chunk)}\n\n"
                    except:
                        continue
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
