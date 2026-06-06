import os
import random
import string
import time
import threading
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# code -> {path, filename, expires_at}
store: dict[str, dict] = {}
store_lock = threading.Lock()

CODE_CHARS = string.ascii_letters + string.digits  # 62 chars, case-sensitive
EXPIRY_SECONDS = 600  # 10 minutes


def generate_code() -> str:
    with store_lock:
        for _ in range(100):
            code = "".join(random.choices(CODE_CHARS, k=5))
            if code not in store:
                return code
    raise RuntimeError("Could not generate unique code")


def cleanup_loop():
    while True:
        time.sleep(60)
        now = time.time()
        with store_lock:
            expired = [c for c, v in store.items() if v["expires_at"] < now]
            for code in expired:
                try:
                    Path(store[code]["path"]).unlink(missing_ok=True)
                except Exception:
                    pass
                del store[code]


threading.Thread(target=cleanup_loop, daemon=True).start()


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    code = generate_code()
    dest = UPLOAD_DIR / f"{code}_{file.filename}"
    contents = await file.read()
    dest.write_bytes(contents)

    with store_lock:
        store[code] = {
            "path": str(dest),
            "filename": file.filename,
            "expires_at": time.time() + EXPIRY_SECONDS,
            "size": len(contents),
        }

    return {"code": code, "filename": file.filename, "expires_in": EXPIRY_SECONDS}


@app.get("/download/{code}")
def download(code: str):
    with store_lock:
        entry = store.get(code)

    if not entry:
        raise HTTPException(status_code=404, detail="Code not found or expired")

    if time.time() > entry["expires_at"]:
        with store_lock:
            store.pop(code, None)
        Path(entry["path"]).unlink(missing_ok=True)
        raise HTTPException(status_code=410, detail="Code expired")

    return FileResponse(
        path=entry["path"],
        filename=entry["filename"],
        media_type="application/octet-stream",
    )


@app.get("/check/{code}")
def check(code: str):
    with store_lock:
        entry = store.get(code)
    if not entry or time.time() > entry["expires_at"]:
        raise HTTPException(status_code=404, detail="Not found")
    remaining = int(entry["expires_at"] - time.time())
    size_kb = round(entry["size"] / 1024, 1)
    return {"filename": entry["filename"], "expires_in": remaining, "size_kb": size_kb}


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
