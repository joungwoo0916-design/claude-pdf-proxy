import base64
import io
import json
import os
import fitz  # PyMuPDF 고속 도면 렌더링 엔진
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx

app = FastAPI()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

def convert_pdf_base64_to_images(b64_str):
    """PDF base64 데이터를 받아서 초고화질 PNG 이미지 목록으로 자동 캡처"""
    try:
        pdf_bytes = base64.b64decode(b64_str)
        if not pdf_bytes.startswith(b"%PDF"):
            return None
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        for page in doc:
            # 도면 치수와 작은 글씨 판독을 위해 고화질 2배율 확대 렌더링
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
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
                        # PDF 감지 시 백그라운드 초고화질 자동 캡처 실행!
                        if "application/pdf" in header or b64_data.startswith("JVBERi"):
                            print("[Magic Hook] CAD 도면 PDF 감지 -> 초고화질 이미지 자동 캡처 중...")
                            captured_images = convert_pdf_base64_to_images(b64_data)
                            if captured_images:
                                for img_b64 in captured_images:
                                    anthropic_content.append({
                                        "type": "image",
                                        "source": {"type": "base64", "media_type": "image/png", "data": img_b64}
                                    })
                                print(f"[Success] {len(captured_images)}장의 고화질 도면 사진으로 클로드에 전송 완료!")
                                continue
                        # 일반 이미지
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
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", "https://api.anthropic.com/v1/messages", json=payload, headers=headers) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        raw_data = line[6:].strip()
                        if raw_data == "[DONE]":
                            yield "data: [DONE]\n\n"
                            break
                        try:
                            event = json.loads(raw_data)
                            e_type = event.get("type")
                            text_chunk = ""
                            if e_type == "content_block_delta":
                                text_chunk = event.get("delta", {}).get("text", "")
                            if text_chunk:
                                chunk = {
                                    "id": "chatcmpl-proxy",
                                    "object": "chat.completion.chunk",
                                    "choices": [{"delta": {"content": text_chunk}, "index": 0}]
                                }
                                yield f"data: {json.dumps(chunk)}\n\n"
                        except:
                            continue
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
