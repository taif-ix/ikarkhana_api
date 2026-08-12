from fastapi import FastAPI, Request


app = FastAPI(title="Local Callback Test Server")


@app.get("/")
async def health():
    return {"status": "callback test server is running"}


@app.post("/ai/callback")
async def callback(request: Request):
    payload = await request.json()
    print("CALLBACK RECEIVED:", payload, flush=True)
    return {"received": True}
