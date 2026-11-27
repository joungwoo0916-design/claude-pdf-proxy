import base64
import json
import os
import time
import asyncio
import fitz  # PyMuPDF
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

app = FastAPI()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
http_client = httpx.AsyncClient(timeout=120.0)


def convert_pdf_base64_to_images(b64_str):
    try:
        pdf_bytes = base64.b64decode(b64_str.strip())
        if not pdf_bytes.startswith(b"%PDF"):
            return None
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        zoom_levels = (2.0, 1.5, 1.0) if len(doc) <= 5 else (1.5, 1.2, 1.0)
        for page in doc:
            for zoom in zoom_levels:
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                png_bytes = pix.tobytes("png")
                if len(png_bytes) <= 4_500_000 or zoom == 1.0:
                    img_b64 = base64.b64encode(png_bytes).decode("utf-8")
                    images.append(img_b64)
                    break
        print(f"[Magic Hook] PDF {len(images)}페이지 고화질 PNG 변환 완료", flush=True)
        return images
    except Exception as e:
        print(f"[Error] PDF convert failed: {e}", flush=True)
        return None


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
@app.post("/v1/v1/chat/completions")
async def chat_completions(request: Request):
    data = await request.json()
    messages = data.get("messages", [])
    stream = data.get("stream", True)
    requested_model = data.get("model", "claude-sonnet-5-5")

    anthropic_messages = []
    system_prompt = ""
    first_user_text = "새로운 대화"

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            if isinstance(content, str):
                system_prompt = content
            elif isinstance(content, list):
                system_prompt = "\n".join(
                    p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                )
            continue

        anthropic_content = []
        if isinstance(content, str):
            if content:
                anthropic_content.append({"type": "text", "text": content})
                if role == "user" and first_user_text == "새로운 대화":
                    first_user_text = content[:20]

        elif isinstance(content, list):
            print(
                f"[Debug] incoming content types: {[p.get('type') for p in content if isinstance(p, dict)]}",
                flush=True
            )
            for part in content:
                if not isinstance(part, dict):
                    continue
                p_type = part.get("type")

                if p_type == "text":
                    t_val = part.get("text", "")
                    if t_val:
                        anthropic_content.append({"type": "text", "text": t_val})
                        if role == "user" and first_user_text == "새로운 대화":
                            first_user_text = t_val[:20]

                elif p_type in ["file", "input_file", "document", "image_url", "image"]:
                    raw_url = ""
                    if p_type in ["image_url", "image"]:
                        img_obj = part.get("image_url")
                        if isinstance(img_obj, dict):
                            raw_url = img_obj.get("url", "")
                        elif isinstance(img_obj, str):
                            raw_url = img_obj
                        else:
                            raw_url = part.get("url", "")
                    elif p_type == "file":
                        file_obj = part.get("file", {})
                        raw_url = file_obj.get("file_data", "") or file_obj.get("url", "")
                    elif p_type == "input_file":
                        raw_url = part.get("file_data", "") or part.get("file_url", "")
                    elif p_type == "document":
                        src = part.get("source", {})
                        if src.get("data"):
                            raw_url = f"data:{src.get('media_type', 'application/pdf')};base64,{src['data']}"

                    if not raw_url:
                        continue

                    if raw_url.startswith(("http://", "https://")):
                        try:
                            f_resp = await http_client.get(raw_url)
                            f_resp.raise_for_status()
                            ctype = f_resp.headers.get("content-type", "")
                            b64_data = base64.b64encode(f_resp.content).decode("utf-8")
                            header = f"data:{ctype};"
                        except Exception as fe:
                            print(f"[Error] Remote file fetch failed: {fe}", flush=True)
                            continue
                    elif "base64," in raw_url:
                        header, b64_data = raw_url.split("base64,", 1)
                    else:
                        header, b64_data = "", raw_url

                    b64_data = b64_data.strip()

                    if "application/pdf" in header or b64_data.startswith("JVBERi"):
                        print(f"[Magic Hook] 도면 PDF 감지 (type={p_type}) -> 고화질 이미지 변환 실행!", flush=True)
                        captured_images = convert_pdf_base64_to_images(b64_data)
                        if captured_images:
                            for img_b64 in captured_images:
                                anthropic_content.append({
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": "image/png", "data": img_b64}
                                })
                            continue

                    media_type = "image/png"
                    if "jpeg" in header or "jpg" in header:
                        media_type = "image/jpeg"
                    elif "webp" in header:
                        media_type = "image/webp"
                    elif "gif" in header:
                        media_type = "image/gif"

                    anthropic_content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64_data}
                    })

        if anthropic_content:
            anthropic_messages.append({
                "role": "user" if role == "user" else "assistant",
                "content": anthropic_content
            })

    # Anthropic 공식 claude-sonnet-5-5 모델 적용
    payload = {
        "model": "claude-sonnet-5-5",
        "messages": anthropic_messages,
        "max_tokens": 8192
    }
    if system_prompt:
        payload["system"] = system_prompt

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    created_time = int(time.time())

    try:
        resp = await http_client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
        if resp.status_code != 200:
            err_body = resp.text
            print(f"[Anthropic Error] {resp.status_code}: {err_body}", flush=True)
            full_text = f"⚠️ [클로드 API 거절 {resp.status_code}]: {err_body}"
        else:
            res_data = resp.json()
            full_text = ""
            for part in res_data.get("content", []):
                if part.get("type") == "text":
                    full_text += part.get("text", "")
            print(f"[Success] Claude Sonnet 5.5 답변 생성 완료 ({len(full_text)}자)", flush=True)
    except Exception as e:
        print(f"[Exception] {e}", flush=True)
        full_text = f"⚠️ [프록시 오류]: {str(e)}"

    if stream:
        async def stream_generator():
            start_chunk = {
                "id": "chatcmpl-proxy",
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": requested_model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
            }
            yield f"data: {json.dumps(start_chunk)}\n\n"

            chunk_size = 8
            for i in range(0, len(full_text), chunk_size):
                piece = full_text[i:i+chunk_size]
                chunk = {
                    "id": "chatcmpl-proxy",
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": requested_model,
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0.01)

            stop_chunk = {
                "id": "chatcmpl-proxy",
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": requested_model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(stop_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        response_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
        return StreamingResponse(stream_generator(), headers=response_headers, media_type="text/event-stream")

    else:
        # 비스트리밍(대화 제목 생성) 응답
        if not full_text.strip():
            full_text = first_user_text.replace("\n", " ").strip()[:20]
        return JSONResponse({
            "id": "chatcmpl-proxy",
            "object": "chat.completion",
            "created": created_time,
            "model": requested_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full_text.strip().replace('"', '')}, "finish_reason": "stop"}]
        })


@app.api_route("/{path_name:path}", methods=["GET", "POST"])
async def catch_all(path_name: str):
    return {"status": "ok", "path": path_name}
