import uvicorn

from .config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run("backend.app:app", host=HOST, port=PORT, log_level="info")
